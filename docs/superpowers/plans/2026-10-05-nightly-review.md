# Nightly Auto-Review: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every night, read each game that finished processing for signs that something went wrong, and raise one ⚑ flag (`origin: "auto"`) per suspicious game in the existing flag list.

**Architecture:**
- A pure module, `curling_score/autoreview.py`, turns one game from a served timeline into findings. Each finding is strong, weak or a note.
- An admin endpoint, `POST /api/admin/review`, picks the games whose current run went ready in the lookback, reviews them against a rolling per-format baseline built from earlier reviews, stores a `Review` record per (game, run), and writes a deterministic-id `Flag` for each game that should be flagged.
- Cloud Scheduler calls the endpoint hourly from 03:00 to 06:00 Pacific.
- `scripts/review.py` runs the same module over downloaded timelines for tuning.
- `scripts/flags.py` shows auto-flags with a link per finding.

**Tech Stack:** Python 3, FastAPI, Firestore (google-cloud-firestore), pytest, Cloud Scheduler (gcloud).

**Spec:** `docs/superpowers/specs/2026-10-05-nightly-review-design.md`, sections 1-6 plus Testing and Rollout. Section 7 (recordings) is the other plan, `docs/superpowers/plans/2026-10-05-live-recordings-week.md`; the two ship independently.

**Refinements of the spec (deliberate).**
1. The module is `curling_score/autoreview.py`, not `review.py`. `service/api.py` already has `review_page`, `review_timeline` and other "review" names for the `/g/` review link, and a module called `review` there would be one careless local variable away from shadowing.
2. `review_game(game, baseline)` takes the game dict itself, not `(doc, game_index, baseline)`. The endpoint gets the game from `game_doc(...)`, the same trimmed document `/g/<id>/timeline.json` serves, and the script gets it from the file.
3. The flag id hashes `source_id|run_id|run.ready_at`, not just source and run. A run retried in place keeps its id but gets a new `ready_at`, so it is reviewed again with a new flag. The `reviews` record stays `{source_id}_{run_id}` and is replaced.
4. `missing_rocks` also fires on a middle end whose shot list is shorter than `shots_expected` (2 cases in the backtest).
5. `not_alternating` compares only consecutively numbered rocks. Without this, all 10 backtest hits came from a missing rock between two real ones; with it, 0.
6. Doubles brooms have no fixed floor (`NO_FLOOR`). Before the baseline exists, the 60% floor would flag every doubles end, because doubles ends carry no brooms.
7. The endpoint also takes `until` (ISO): only runs that finished before it. Seeding the baseline then stops 3 days back, leaving the last 3 days unread for the dry run you see before the scheduler is switched on, and for the first real night.

## Global Constraints

- Work in a git worktree at `/home/tcuser/src/curling_score/.claude/worktrees/nightly-review` on branch `nightly-review`, created from `main`. **Never write under `/home/tcuser/src/curling_score` outside that worktree.** Another session shares the main checkout.
- Run tests from the worktree with the main checkout's venv: `cd <worktree> && /home/tcuser/src/curling_score/.venv/bin/pytest <files>`. `pyproject.toml`'s `pythonpath = ["src"]` makes the worktree's source the one tested. Do not add `-q`.
- The full suite is OOM-killed on this laptop (exit 137). Run only the files a task names.
- `tests/test_service_api.py` holds the shared fixtures (`world`, `submit`, `work_through`, `ADMIN`, `T0`, `VID`). Import them; don't copy them.
- Thresholds (copied from the spec; each constant's comment says what set it):

  | Constant | Value |
  |---|---|
  | `SPLIT_MIN_S` / `SPLIT_MAX_S` | 7.0 / 22.0 |
  | `MIN_ENDS` | 3 |
  | `TINY_END_ROCKS` | 4 |
  | `COVERAGE_MIN_ROCKS` | 6 |
  | `COVERAGE_GAP_ROCKS` | 4 |
  | `BASELINE_PERCENTILE` | 0.02 |
  | `BASELINE_DAYS` | 14 |
  | `BASELINE_MIN_ENDS` | 100 |
  | `COVERAGE_FLOOR` | 0.6 |
  | `BROOM_BEHIND_M` | 0.3 |
  | `BROOM_PAST_HOG_M` | 1.0 |
  | `BROOM_MISS_M` | 1.0 |
  | `BATCH` | 25 |
  | `LOOKBACK_DAYS` | 3 |
  | `TIME_BUDGET_S` | 40.0 |

- A game is flagged when it has **at least one strong finding or at least two weak ones**. Notes never raise a flag.
- Strong checks: `same_house`, `end_gap`, `missing_rocks`, `not_alternating` (fours only), `team_over`, `short_game`, `tiny_last_end`, `coverage_<broom|split|line|release>`, `broom_off_sheet`, `unplaced`.
- Weak checks: `odd_split`, `colour_inferred`.
- Notes: `board_disagrees`, `hammer`, `broom_miss`.
- Auto-flags are never resolved automatically. A flag whose id exists is never rewritten.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Match the surrounding code: docstrings that say why, sparing comments, the neighbours' naming.

## Review Focus

1. **A run retried in place** (same run id, new `ready_at`). It is reviewed again, gets a new flag, and that flag names the earlier one. Test in Task 7.
2. **A page merged into another game** (`Source.merged_into` set). It is not reviewed, because it isn't a game of its own any more. Test in Task 7.
3. **Timelines without the newer fields** (`shots_expected`, `unplaced_shots`, `target_broom`, `long_split_s`, `line`, `t_release_s`, `color_inferred`), as in the API tests' `sample_doc` and pre-schema-8 runs. Reviewing never raises a `KeyError`. Tests in Task 1 and Task 7.
4. **A source whose game is not in its run's document** (index mismatch, or a play start past the last end). It becomes an error record, not a crash, and the batch goes on. Test in Task 7.
5. **A game with a long list of findings.** The flag note stays within `MAX_FLAG_NOTE` (2,000), and the earlier-flag mention survives the truncation. Test in Task 1.

---

### Task 1: `autoreview` core: findings, end metrics, baseline, the raise rule

**Files:**
- Create: `src/curling_score/autoreview.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Produces, all in `curling_score.autoreview`:
  - `Finding(check: str, strength: str, end: int | None = None, rock: int | None = None, detail: str = "", t_video_s: float | None = None)`, with `.to_dict()`. `strength` is one of `"strong"`, `"weak"`, `"note"`.
  - `EndMetrics(number: int, rocks: int, broom: int, split: int, line: int, release: int)`, with `.to_dict()`.
  - `GameReview(format: str, ends: list[EndMetrics], findings: list[Finding], notes: list[Finding])`, with property `raise_flag -> bool`.
  - `Baseline(thresholds: dict[tuple[str, str], float], ends: dict[str, int])`, with `Baseline.from_ends(rows: Iterable[tuple[str, EndMetrics | dict]]) -> Baseline` and `.threshold(fmt: str, metric: str) -> float`.
  - `game_format(game: dict) -> str` returning `"fours"` or `"doubles"`.
  - `end_metrics(end: dict) -> EndMetrics`.
  - `review_game(game: dict, baseline: Baseline) -> GameReview`. In this task it returns no findings; Tasks 2 and 3 add the checks.
  - `describe(f: Finding) -> str`, e.g. `"e3 r7 split 23.4 s"`.
  - `summary(got: GameReview, extra: str = "", limit: int = 2000) -> str`.
  - `flag_id(source_id: str, run_id: str, ready_at: datetime | None) -> str`, returning `"fa_" + 16 hex`.
  - Every constant in Global Constraints, plus `METRICS = ("broom", "split", "line", "release")`, `NO_FLOOR = {("doubles", "broom")}`, `BACK_LINE_M = -1.829`, `HOG_M = 6.401`, `HALF_SHEET_M = 2.375`.

- [ ] **Step 1: Create the worktree**

```bash
cd /home/tcuser/src/curling_score
git worktree add -b nightly-review .claude/worktrees/nightly-review main
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_review.py`:

```python
"""The nightly review's reading of one game: what counts as wrong, and how
sure it has to be before a game is flagged."""

from datetime import datetime, timezone

import pytest

from curling_score import autoreview
from curling_score.autoreview import Baseline, EndMetrics, Finding, GameReview

FLOOR = Baseline({}, {})        # no history yet: the fixed floor stands in


def rock(n, colour=None, **kw):
    s = {"number": n, "color": colour or ("red" if n % 2 else "yellow"), "missing": False,
         "t_video_s": 100.0 + n, "target_broom": {"x": 0.1, "y": 0.0, "confidence": 0.9},
         "long_split_s": 14.0, "line": {"at_broom": {"miss_m": 0.2}},
         "t_release_s": 90.0 + n, "color_inferred": False}
    s.update(kw)
    return s


def end(number, house, rocks=16, expected=16, shots=None, **kw):
    e = {"number": number, "house": house, "start_s": 1000.0 * number,
         "shots_expected": expected, "unplaced_shots": 0,
         "score": {"red": 1, "yellow": 0}, "detected_score": {"red": 1, "yellow": 0},
         "shots": shots if shots is not None else [rock(n) for n in range(1, rocks + 1)]}
    e.update(kw)
    return e


def house(i):
    return "top" if i % 2 else "bottom"


def game(*ends, n=4, hammer=True):
    ends = list(ends) or [end(i, house(i)) for i in range(1, n + 1)]
    return {"index": 0, "hammer_consistent": hammer, "ends": ends}


def doubles_game(n=6):
    return game(*[end(i, house(i), rocks=10, expected=10) for i in range(1, n + 1)])


def checks(g, baseline=FLOOR):
    return sorted(f.check for f in autoreview.review_game(g, baseline).findings)


class TestAGame:
    def test_a_clean_fours_game_has_nothing_to_say(self):
        got = autoreview.review_game(game(), FLOOR)
        assert got.findings == [] and got.notes == [] and not got.raise_flag
        assert got.format == "fours" and [m.number for m in got.ends] == [1, 2, 3, 4]

    def test_ten_rock_ends_are_doubles(self):
        assert autoreview.game_format(doubles_game()) == "doubles"
        assert autoreview.game_format(game()) == "fours"

    def test_end_metrics_count_placed_rocks_and_what_each_has(self):
        shots = [rock(n) for n in range(1, 17)]
        shots[2]["missing"] = True
        shots[3]["target_broom"] = None
        shots[4]["long_split_s"] = None
        shots[5]["line"] = None
        shots[6]["t_release_s"] = None
        assert autoreview.end_metrics(end(1, "top", shots=shots)) == EndMetrics(
            number=1, rocks=15, broom=14, split=14, line=14, release=14)

    def test_a_timeline_from_before_the_newer_fields_is_read_without_error(self):
        bare = {"index": 0, "ends": [{"number": 1, "house": "top", "start_s": 0.0,
                                      "shots": [{"number": 1, "color": "red",
                                                 "missing": False}]}]}
        got = autoreview.review_game(bare, FLOOR)
        assert got.ends == [EndMetrics(1, 1, 0, 0, 0, 0)]


class TestTheBaseline:
    def rows(self, fmt, broom_counts):
        return [(fmt, EndMetrics(i, 16, b, 16, 16, 16)) for i, b in enumerate(broom_counts)]

    def test_normal_is_the_2nd_percentile_of_recent_ends(self):
        counts = [16] * 95 + [8] * 3 + [0] * 2          # 100 ends
        base = Baseline.from_ends(self.rows("fours", counts))
        # sorted coverage: 0, 0, .5, .5, .5, 1 ...; index int(0.02 * 99) = 1 -> 0.0
        assert base.threshold("fours", "broom") == 0.0
        assert base.ends == {"fours": 100}

    def test_too_few_ends_fall_back_to_the_floor(self):
        base = Baseline.from_ends(self.rows("fours", [16] * 99))
        assert base.threshold("fours", "broom") == autoreview.COVERAGE_FLOOR

    def test_doubles_brooms_have_no_floor(self):
        assert FLOOR.threshold("doubles", "broom") == 0.0
        assert FLOOR.threshold("doubles", "split") == autoreview.COVERAGE_FLOOR

    def test_ends_too_short_to_judge_are_left_out(self):
        rows = [("fours", EndMetrics(1, 5, 0, 0, 0, 0))] * 200
        assert Baseline.from_ends(rows).ends == {}

    def test_stored_records_are_plain_dicts(self):
        rows = [("fours", EndMetrics(i, 16, 12, 16, 16, 16).to_dict()) for i in range(100)]
        assert Baseline.from_ends(rows).threshold("fours", "broom") == 0.75


class TestWhenAGameIsFlagged:
    def got(self, *strengths):
        return GameReview("fours", [], [Finding("x", s) for s in strengths if s != "note"],
                          [Finding("n", "note") for s in strengths if s == "note"])

    @pytest.mark.parametrize("strengths,flagged", [
        (("strong",), True), (("weak",), False), (("weak", "weak"), True),
        (("note", "note", "note"), False), ((), False)])
    def test_one_strong_or_two_weak(self, strengths, flagged):
        assert self.got(*strengths).raise_flag is flagged


class TestTheFlagItRaises:
    def test_its_id_is_derived_from_the_game_the_run_and_when_the_run_finished(self):
        t1 = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
        a = autoreview.flag_id("s_1", "r_1", t1)
        assert a == autoreview.flag_id("s_1", "r_1", t1) and a.startswith("fa_")
        assert len(a) == 3 + 16
        assert a != autoreview.flag_id("s_1", "r_1", t2) != autoreview.flag_id("s_2", "r_1", t2)

    def test_the_summary_names_each_finding(self):
        got = GameReview("fours", [], [Finding("same_house", "strong", 5, None, "e4 and e5 both top"),
                                       Finding("odd_split", "weak", 6, 9, "split 23.4 s")],
                         [Finding("hammer", "note", detail="hammer sequence broken")])
        text = autoreview.summary(got)
        assert text.startswith("Auto-review: 2 findings")
        assert "e5 e4 and e5 both top" in text and "e6 r9 split 23.4 s" in text
        assert "hammer sequence broken" in text

    def test_a_long_summary_is_cut_but_keeps_the_earlier_flag(self):
        many = [Finding("odd_split", "weak", 1, n, "split 30.0 s") for n in range(400)]
        text = autoreview.summary(GameReview("fours", [], many, []),
                                  extra="earlier auto-flag fa_0123 (run r_9)", limit=2000)
        assert len(text) <= 2000 and text.endswith("…")
        assert "earlier auto-flag fa_0123 (run r_9)" in text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'curling_score.autoreview'`.

- [ ] **Step 4: Implement**

Create `src/curling_score/autoreview.py`:

```python
"""Look over a finished game for the signs that something went wrong.

The nightly review (``POST /api/admin/review``) runs this over every game that
finished processing the day before and raises one ⚑ flag for each game that
looks wrong, so a morning starts with a list to investigate rather than
waiting for somebody to notice. ``scripts/review.py`` runs it over downloaded
timelines, to tune what "looks wrong" means.

Pure: one game from a served timeline in, findings out. Each threshold says
what set it: the backtest over the 126 hosted games of 2026-09-27 to 10-05 in
docs/superpowers/specs/2026-10-05-nightly-review-design.md.

A finding is *strong* (one flags the game), *weak* (it takes two) or a *note*
(listed when the game is flagged, never the reason it is).
"""

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass, field

# The sheet, in metres from the tee: y along it towards the hog line, x across.
BACK_LINE_M = -1.829
HOG_M = 6.401
HALF_SHEET_M = 2.375
# A broom this far behind the back line or past the hog line is not a target:
# none of 9,317 backtest rocks since the along-sheet cluster fix (6cf19cf).
BROOM_BEHIND_M = 0.3
BROOM_PAST_HOG_M = 1.0
# Splits: p0.5 7.7 s and p99.5 20.8 s over 8,517 backtest rocks, max 27.8 s.
SPLIT_MIN_S, SPLIT_MAX_S = 7.0, 22.0
# Fewer ends is a fragment or a feed that died (Kozai Draw 5 lost all five).
MIN_ENDS = 3
# A last end this small after a real game is the next draw's first rocks.
TINY_END_ROCKS = 4
# Coverage is judged on ends with enough rocks to say anything, and an end is
# flagged only when it lacks the measurement on this many of them: the gap a
# calibration failure leaves (Mens S1 end 1: 0 brooms of 14), not one rock's.
COVERAGE_MIN_ROCKS = 6
COVERAGE_GAP_ROCKS = 4
METRICS = ("broom", "split", "line", "release")
# Normal is the 2nd percentile of the last BASELINE_DAYS of reviewed ends, per
# format; with fewer than BASELINE_MIN_ENDS behind it, a fixed floor stands in.
BASELINE_PERCENTILE = 0.02
BASELINE_DAYS = 14
BASELINE_MIN_ENDS = 100
COVERAGE_FLOOR = 0.6
# Doubles ends carry no brooms (112 of 118 backtest ends had none at all), so
# there is no floor to fall below.
NO_FLOOR = {("doubles", "broom")}
# A line this far from its broom is the tangent extrapolation (84 of 126
# backtest games), not a bad read: a note, counted.
BROOM_MISS_M = 1.0
# The endpoint: games per call, how far back to look for runs that finished,
# and when to stop starting games inside Cloud Run's 60 s.
BATCH = 25
LOOKBACK_DAYS = 3
TIME_BUDGET_S = 40.0


@dataclass
class Finding:
    check: str
    strength: str                       # "strong" | "weak" | "note"
    end: int | None = None
    rock: int | None = None
    detail: str = ""
    t_video_s: float | None = None

    to_dict = asdict


@dataclass
class EndMetrics:
    """How many rocks an end placed, and how many of them have each measurement."""

    number: int
    rocks: int
    broom: int
    split: int
    line: int
    release: int

    to_dict = asdict


@dataclass
class GameReview:
    format: str
    ends: list = field(default_factory=list)       # [EndMetrics]
    findings: list = field(default_factory=list)   # [Finding], strong and weak
    notes: list = field(default_factory=list)      # [Finding], never the reason

    @property
    def raise_flag(self) -> bool:
        strong = sum(1 for f in self.findings if f.strength == "strong")
        weak = sum(1 for f in self.findings if f.strength == "weak")
        return strong > 0 or weak >= 2


@dataclass
class Baseline:
    """What normal coverage looks like lately: a threshold per (format, metric)."""

    thresholds: dict = field(default_factory=dict)
    ends: dict = field(default_factory=dict)       # {format: ends behind it}

    @classmethod
    def from_ends(cls, rows) -> "Baseline":
        """``rows``: (format, EndMetrics or its dict) for each recent end."""
        coverage, counts = {}, Counter()
        for fmt, m in rows:
            m = m if isinstance(m, dict) else m.to_dict()
            if m["rocks"] < COVERAGE_MIN_ROCKS:
                continue
            counts[fmt] += 1
            for k in METRICS:
                coverage.setdefault((fmt, k), []).append(m[k] / m["rocks"])
        thresholds = {}
        for (fmt, k), values in coverage.items():
            if counts[fmt] >= BASELINE_MIN_ENDS:
                values.sort()
                thresholds[(fmt, k)] = values[int(BASELINE_PERCENTILE * (len(values) - 1))]
        return cls(thresholds, dict(counts))

    def threshold(self, fmt: str, metric: str) -> float:
        if (fmt, metric) in self.thresholds:
            return self.thresholds[(fmt, metric)]
        return 0.0 if (fmt, metric) in NO_FLOOR else COVERAGE_FLOOR


def game_format(game: dict) -> str:
    return ("doubles" if any(e.get("shots_expected") == 10 for e in game.get("ends", []))
            else "fours")


def _placed(end: dict) -> list:
    return [s for s in end.get("shots", []) if not s.get("missing")]


def _has(metric: str, shot: dict) -> bool:
    if metric == "broom":
        return bool(shot.get("target_broom"))
    if metric == "split":
        return shot.get("long_split_s") is not None
    if metric == "line":
        return bool(shot.get("line"))
    return shot.get("t_release_s") is not None


def end_metrics(end: dict) -> EndMetrics:
    placed = _placed(end)
    return EndMetrics(end.get("number"), len(placed),
                      *(sum(1 for s in placed if _has(k, s)) for k in METRICS))


def review_game(game: dict, baseline: Baseline) -> GameReview:
    fmt = game_format(game)
    return GameReview(fmt, [end_metrics(e) for e in game.get("ends", [])], [], [])


def describe(f: Finding) -> str:
    at = "" if f.end is None else f"e{f.end}"
    if f.rock is not None:
        at += f" r{f.rock}"
    return f"{at} {f.detail}".strip()


def summary(got: GameReview, extra: str = "", limit: int = 2000) -> str:
    """The flag's note: every finding on one line, cut to ``limit``. ``extra``
    (the earlier auto-flag, if any) goes first so a cut never loses it."""
    n = len(got.findings)
    head = f"Auto-review: {n} finding{'' if n == 1 else 's'}"
    if extra:
        head += f" ({extra})"
    text = head + " — " + "; ".join(describe(f) for f in got.findings)
    if got.notes:
        text += ". Notes: " + "; ".join(describe(f) for f in got.notes)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def flag_id(source_id: str, run_id: str, ready_at) -> str:
    """The auto-flag of one review of one game. Derived, so a call that dies
    after writing it and runs again finds it rather than adding a second; the
    run's ``ready_at`` is in it, so a run retried in place is a new review."""
    stamp = ready_at.isoformat() if ready_at is not None else ""
    return "fa_" + hashlib.sha256(f"{source_id}|{run_id}|{stamp}".encode()).hexdigest()[:16]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add src/curling_score/autoreview.py tests/test_review.py
git commit -m "autoreview: findings, end coverage, a rolling baseline and the flag rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Structure checks

**Files:**
- Modify: `src/curling_score/autoreview.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: Task 1's `Finding`, `_placed`, `game_format`, `review_game`.
- Produces: `_structure(game: dict, fmt: str) -> list[Finding]`, called from `review_game`, with checks `short_game`, `tiny_last_end`, `same_house`, `end_gap`, `missing_rocks`, `not_alternating`, `team_over`. All are strong.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def missing(e, *numbers):
    for s in e["shots"]:
        if s["number"] in numbers:
            s["missing"] = True
    return e


class TestStructure:
    def test_two_ends_in_a_row_to_the_same_house(self):
        g = game(end(1, "top"), end(2, "bottom"), end(3, "bottom"), end(4, "top"))
        got = autoreview.review_game(g, FLOOR).findings
        assert [(f.check, f.end, f.strength) for f in got] == [("same_house", 3, "strong")]

    def test_an_end_number_skipped(self):
        g = game(end(1, "top"), end(2, "bottom"), end(4, "top"), end(5, "bottom"))
        assert checks(g) == ["end_gap"]

    def test_rocks_missing_mid_game(self):
        g = game()
        missing(g["ends"][1], 5, 9)
        got = [f for f in autoreview.review_game(g, FLOOR).findings]
        assert [(f.check, f.end, f.rock) for f in got] == [("missing_rocks", 2, 5)]
        assert "5, 9" in got[0].detail

    def test_joining_late_is_not_missing_rocks(self):
        g = game()
        missing(g["ends"][0], 1, 2, 3)
        assert checks(g) == []

    def test_a_gap_in_the_first_end_after_rock_1_is(self):
        g = game()
        missing(g["ends"][0], 1, 2, 5)
        assert checks(g) == ["missing_rocks"]

    def test_a_conceded_last_end_is_not_missing_rocks(self):
        g = game()
        missing(g["ends"][-1], 14, 15, 16)
        assert checks(g) == []

    def test_a_middle_end_listing_too_few_rocks(self):
        g = game(end(1, "top"), end(2, "bottom", rocks=14), end(3, "top"), end(4, "bottom"))
        assert checks(g) == ["missing_rocks"]

    def test_short_lists_at_either_end_of_the_game_are_normal(self):
        g = game(end(1, "top", rocks=13), end(2, "bottom"), end(3, "top"), end(4, "bottom", rocks=9))
        assert checks(g) == []

    def test_one_colour_twice_in_a_row(self):
        g = game()
        g["ends"][2]["shots"][5]["color"] = g["ends"][2]["shots"][4]["color"]
        assert "not_alternating" in checks(g)

    def test_a_missing_rock_between_two_of_a_colour_is_not_a_repeat(self):
        g = game()
        missing(g["ends"][1], 6)          # rocks 5 and 7 are both red, and should be
        assert "not_alternating" not in checks(g)

    def test_doubles_are_not_held_to_alternating(self):
        g = doubles_game()
        g["ends"][0]["shots"][1]["color"] = "red"
        assert "not_alternating" not in checks(g)

    def test_one_colour_with_more_than_half_the_rocks(self):
        g = game()
        for s in g["ends"][1]["shots"][:10]:
            s["color"] = "red"
        assert "team_over" in checks(g)

    def test_a_game_of_two_ends(self):
        got = autoreview.review_game(game(n=2), FLOOR).findings
        assert [(f.check, f.end) for f in got] == [("short_game", None)]

    def test_a_tiny_last_end_after_a_real_game(self):
        g = game(*[end(i, house(i)) for i in range(1, 7)], end(7, "top", rocks=3))
        assert checks(g) == ["tiny_last_end"]

    def test_a_short_game_is_not_also_a_tiny_end(self):
        g = game(end(1, "top"), end(2, "bottom", rocks=2))
        assert checks(g) == ["short_game"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: FAIL. The `TestStructure` cases find `[]`.

- [ ] **Step 3: Implement**

In `src/curling_score/autoreview.py`, add before `review_game`:

```python
def _missing(end: dict, first: bool, last: bool) -> list:
    """Rocks an end lost. A leading run in the game's first end is the stream
    joining late, and a trailing run in its last end is a concession; anything
    else, or a middle end listing fewer rocks than it should, is not."""
    shots = sorted(end.get("shots", []), key=lambda s: s["number"])
    gone = [s["number"] for s in shots if s.get("missing")]
    expected = end.get("shots_expected")
    if gone:
        leading = first and gone == list(range(1, len(gone) + 1))
        top = shots[-1]["number"]
        trailing = last and gone == list(range(top - len(gone) + 1, top + 1))
        if leading or trailing:
            return []
        return [Finding("missing_rocks", "strong", end.get("number"), gone[0],
                        "rocks " + ", ".join(map(str, gone)) + " missing",
                        end.get("start_s"))]
    if expected and len(shots) < expected and not first and not last:
        return [Finding("missing_rocks", "strong", end.get("number"), None,
                        f"{len(shots)} of {expected} rocks listed", end.get("start_s"))]
    return []


def _structure(game: dict, fmt: str) -> list:
    ends = game.get("ends", [])
    out = []
    if len(ends) < MIN_ENDS:
        out.append(Finding("short_game", "strong", None, None,
                           f"{len(ends)} end{'' if len(ends) == 1 else 's'}"))
    elif len(_placed(ends[-1])) <= TINY_END_ROCKS:
        e = ends[-1]
        out.append(Finding("tiny_last_end", "strong", e.get("number"), None,
                           f"last end has {len(_placed(e))} rocks", e.get("start_s")))
    for i, e in enumerate(ends):
        n, before = e.get("number"), ends[i - 1] if i else None
        if before is not None and e.get("house") == before.get("house"):
            out.append(Finding("same_house", "strong", n, None,
                               f"e{before.get('number')} and e{n} both to the "
                               f"{e.get('house')} house", e.get("start_s")))
        if before is not None and n != before.get("number", 0) + 1:
            out.append(Finding("end_gap", "strong", n, None,
                               f"e{before.get('number')} then e{n}", e.get("start_s")))
        out += _missing(e, first=i == 0, last=i == len(ends) - 1)
        placed = sorted(_placed(e), key=lambda s: s["number"])
        if fmt == "fours":
            for a, b in zip(placed, placed[1:]):
                if b["number"] == a["number"] + 1 and a.get("color") == b.get("color"):
                    out.append(Finding("not_alternating", "strong", n, b["number"],
                                       f"r{a['number']} and r{b['number']} both "
                                       f"{b.get('color')}", b.get("t_video_s")))
                    break                   # one an end is enough to look
        expected = e.get("shots_expected") or (10 if fmt == "doubles" else 16)
        for colour, k in sorted(Counter(s.get("color") for s in placed).items()):
            if k > expected // 2:
                out.append(Finding("team_over", "strong", n, None,
                                   f"{colour} has {k} of {expected} rocks", e.get("start_s")))
    return out
```

Change `review_game` to:

```python
def review_game(game: dict, baseline: Baseline) -> GameReview:
    fmt = game_format(game)
    findings = _structure(game, fmt)
    return GameReview(fmt, [end_metrics(e) for e in game.get("ends", [])], findings, [])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add src/curling_score/autoreview.py tests/test_review.py
git commit -m "autoreview: game structure -- ends cut in two, lost rocks, fragments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Coverage, odd rocks, and notes

**Files:**
- Modify: `src/curling_score/autoreview.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: Task 1's `Baseline.threshold`, `end_metrics`; Task 2's `_structure`.
- Produces:
  - `_coverage(game, metrics, fmt, baseline) -> list[Finding]`, giving `coverage_<metric>`, strong.
  - `_odd_rocks(game) -> list[Finding]`, giving `broom_off_sheet` and `unplaced` (strong) and `odd_split` and `colour_inferred` (weak).
  - `_notes(game) -> list[Finding]`, giving `board_disagrees`, `hammer` and `broom_miss`.
  - `review_game` returns all of them; notes go in `GameReview.notes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def without(e, metric_key, count, value=None):
    """Take a measurement off the first ``count`` rocks of an end."""
    for s in e["shots"][:count]:
        s[metric_key] = value
    return e


class TestCoverage:
    def test_an_end_well_below_normal_is_flagged(self):
        g = game()
        without(g["ends"][1], "target_broom", 10)       # 6/16 = 38% < the 60% floor
        got = autoreview.review_game(g, FLOOR).findings
        assert [(f.check, f.end) for f in got] == [("coverage_broom", 2)]
        assert "6/16" in got[0].detail and "60%" in got[0].detail

    def test_below_normal_by_fewer_than_four_rocks_is_not(self):
        g = game()
        without(g["ends"][1], "target_broom", 3)        # 13/16, below a 0.9 normal
        base = Baseline({("fours", "broom"): 0.9}, {"fours": 500})
        assert checks(g, base) == []

    def test_normal_comes_from_the_baseline(self):
        g = game()
        without(g["ends"][1], "target_broom", 10)       # 38%, above a 25% normal
        base = Baseline({("fours", "broom"): 0.25}, {"fours": 500})
        assert checks(g, base) == []

    def test_doubles_without_brooms_are_normal(self):
        g = doubles_game()
        for e in g["ends"]:
            without(e, "target_broom", 10)
        assert checks(g) == []

    @pytest.mark.parametrize("key,metric", [("long_split_s", "split"), ("line", "line"),
                                            ("t_release_s", "release")])
    def test_each_measurement_is_judged(self, key, metric):
        g = game()
        without(g["ends"][2], key, 12)
        assert checks(g) == [f"coverage_{metric}"]

    def test_ends_too_short_to_judge_are_skipped(self):
        g = game(end(1, "top", rocks=5), end(2, "bottom"), end(3, "top"), end(4, "bottom"))
        without(g["ends"][0], "target_broom", 5)
        assert checks(g) == []


class TestOddRocks:
    @pytest.mark.parametrize("broom", [{"x": 2.5, "y": 0.0}, {"x": 0.0, "y": -2.2},
                                       {"x": 0.0, "y": 7.5}])
    def test_a_broom_off_the_sheet(self, broom):
        g = game()
        g["ends"][0]["shots"][3]["target_broom"] = {**broom, "confidence": 0.8}
        got = autoreview.review_game(g, FLOOR)
        assert [(f.check, f.strength, f.end, f.rock) for f in got.findings] == [
            ("broom_off_sheet", "strong", 1, 4)]

    def test_one_odd_split_is_weak_and_alone_flags_nothing(self):
        g = game()
        g["ends"][1]["shots"][8]["long_split_s"] = 23.4
        got = autoreview.review_game(g, FLOOR)
        assert [(f.check, f.strength, f.detail) for f in got.findings] == [
            ("odd_split", "weak", "split 23.4 s")]
        assert not got.raise_flag

    def test_two_weak_findings_flag_the_game(self):
        g = game()
        g["ends"][1]["shots"][8]["long_split_s"] = 6.5
        g["ends"][2]["shots"][2]["color_inferred"] = True
        got = autoreview.review_game(g, FLOOR)
        assert sorted(f.check for f in got.findings) == ["colour_inferred", "odd_split"]
        assert got.raise_flag

    def test_rocks_seen_but_not_placed(self):
        g = game()
        g["ends"][3]["unplaced_shots"] = 2
        assert checks(g) == ["unplaced"]


class TestNotes:
    def test_notes_are_listed_but_never_flag(self):
        g = game(hammer=False)
        g["ends"][1]["score"] = {"red": 2, "yellow": 0}
        g["ends"][2]["shots"][0]["line"] = {"at_broom": {"miss_m": 1.4}}
        g["ends"][2]["shots"][1]["line"] = {"at_broom": {"miss_m": -1.2}}
        got = autoreview.review_game(g, FLOOR)
        assert got.findings == [] and not got.raise_flag
        assert [(n.check, n.end) for n in got.notes] == [
            ("board_disagrees", 2), ("hammer", None), ("broom_miss", None)]
        assert got.notes[2].detail == "2 lines miss their broom by more than 1.0 m"

    def test_an_end_the_board_never_read_says_nothing(self):
        g = game()
        g["ends"][1]["score"] = None
        assert autoreview.review_game(g, FLOOR).notes == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: FAIL. The new classes find no findings and no notes.

- [ ] **Step 3: Implement**

In `src/curling_score/autoreview.py`, add before `review_game`:

```python
def _coverage(game: dict, metrics: list, fmt: str, baseline: Baseline) -> list:
    starts = {e.get("number"): e.get("start_s") for e in game.get("ends", [])}
    out = []
    for m in metrics:
        if m.rocks < COVERAGE_MIN_ROCKS:
            continue
        for k in METRICS:
            have, normal = getattr(m, k), baseline.threshold(fmt, k)
            if have / m.rocks < normal and m.rocks - have >= COVERAGE_GAP_ROCKS:
                out.append(Finding(f"coverage_{k}", "strong", m.number, None,
                                   f"{k} on {have}/{m.rocks} rocks (normal ≥ {normal:.0%})",
                                   starts.get(m.number)))
    return out


def _off_sheet(broom: dict) -> bool:
    x, y = broom.get("x"), broom.get("y")
    if x is None or y is None:
        return False
    return (abs(x) > HALF_SHEET_M or y < BACK_LINE_M - BROOM_BEHIND_M
            or y > HOG_M + BROOM_PAST_HOG_M)


def _odd_rocks(game: dict) -> list:
    out = []
    for e in game.get("ends", []):
        n = e.get("number")
        for s in _placed(e):
            r, t = s.get("number"), s.get("t_video_s")
            broom = s.get("target_broom")
            if broom and _off_sheet(broom):
                out.append(Finding("broom_off_sheet", "strong", n, r,
                                   f"broom at x {broom['x']:+.2f} m, y {broom['y']:+.2f} m", t))
            split = s.get("long_split_s")
            if split is not None and not SPLIT_MIN_S <= split <= SPLIT_MAX_S:
                out.append(Finding("odd_split", "weak", n, r, f"split {split:.1f} s", t))
            if s.get("color_inferred"):
                out.append(Finding("colour_inferred", "weak", n, r,
                                   "colour inferred, not seen", t))
        if e.get("unplaced_shots"):
            k = e["unplaced_shots"]
            out.append(Finding("unplaced", "strong", n, None,
                               f"{k} rock{'' if k == 1 else 's'} seen but not placed",
                               e.get("start_s")))
    return out


def _score_text(score) -> str:
    if isinstance(score, dict):
        return " ".join(f"{k} {v}" for k, v in sorted(score.items()))
    return str(score)


def _notes(game: dict) -> list:
    out = []
    for e in game.get("ends", []):
        board, house = e.get("score"), e.get("detected_score")
        if board is not None and house is not None and board != house:
            out.append(Finding("board_disagrees", "note", e.get("number"), None,
                               f"board {_score_text(board)}, house {_score_text(house)}",
                               e.get("start_s")))
    if game.get("hammer_consistent") is False:
        out.append(Finding("hammer", "note", detail="hammer sequence broken"))
    wide = sum(1 for e in game.get("ends", []) for s in _placed(e)
               if abs(((s.get("line") or {}).get("at_broom") or {}).get("miss_m") or 0.0)
               > BROOM_MISS_M)
    if wide:
        out.append(Finding("broom_miss", "note",
                           detail=f"{wide} line{'' if wide == 1 else 's'} miss their broom "
                                  f"by more than {BROOM_MISS_M:.1f} m"))
    return out
```

Change `review_game` to:

```python
def review_game(game: dict, baseline: Baseline) -> GameReview:
    fmt = game_format(game)
    metrics = [end_metrics(e) for e in game.get("ends", [])]
    findings = (_structure(game, fmt) + _coverage(game, metrics, fmt, baseline)
                + _odd_rocks(game))
    return GameReview(fmt, metrics, findings, _notes(game))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: PASS. If `test_a_clean_fours_game_has_nothing_to_say` now fails, a builder default trips a check. Fix the builder in the test, not the threshold.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add src/curling_score/autoreview.py tests/test_review.py
git commit -m "autoreview: end coverage against normal, odd rocks, and notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Backtest fixture from real games

**Files:**
- Create: `tests/fixtures/review/s_0qsNY1Vdc3vynNPx5.json`, `s_11DBbXARm30gMQEsy.json`, `s_00hRGMbcChFTNviqg.json`, `s_0PXVGfZmiusxIlyX5.json`
- Test: `tests/test_review.py` (new class `TestRealGames`)

**Interfaces:**
- Consumes: `autoreview.review_game`, `Baseline` (Tasks 1-3).
- Produces: pared fixtures with keys `source{video_id, sheet}` and `games[0]{index, hammer_consistent, ends[...]}`. Each end keeps `number, house, start_s, shots_expected, unplaced_shots, score, detected_score, shots`. Each shot keeps `number, color, missing, t_video_s, target_broom, long_split_s, t_release_s, color_inferred, line`, with `line` reduced to its `at_broom.miss_m`.

The source timelines are in `/home/tcuser/curling-work/nightly-review/tl/`, the backtest set from 2026-10-05. Don't re-fetch them: a reprocess since would move the expected findings.

- [ ] **Step 1: Pare the four games into fixtures**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
mkdir -p tests/fixtures/review
/home/tcuser/src/curling_score/.venv/bin/python - <<'EOF'
import json
SRC = "/home/tcuser/curling-work/nightly-review/tl"
SHOT = ("number", "color", "missing", "t_video_s", "target_broom", "long_split_s",
        "t_release_s", "color_inferred")
END = ("number", "house", "start_s", "shots_expected", "unplaced_shots", "score",
       "detected_score")

def line(value):
    if not value:
        return value
    at = value.get("at_broom")
    return {"at_broom": {"miss_m": at["miss_m"]}} if at else {"kept": True}

for sid in ("s_0qsNY1Vdc3vynNPx5", "s_11DBbXARm30gMQEsy", "s_00hRGMbcChFTNviqg",
            "s_0PXVGfZmiusxIlyX5"):
    doc = json.load(open(f"{SRC}/{sid}.json"))
    g = doc["games"][0]
    game = {"index": g["index"], "hammer_consistent": g["hammer_consistent"], "ends": [
        {**{k: e[k] for k in END},
         "shots": [{**{k: s[k] for k in SHOT}, "line": line(s["line"])} for s in e["shots"]]}
        for e in g["ends"]]}
    out = {"source": {k: doc["source"][k] for k in ("video_id", "sheet")}, "games": [game]}
    with open(f"tests/fixtures/review/{sid}.json", "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
EOF
du -sh tests/fixtures/review
```

Expected: about 100-200 KB in total.

- [ ] **Step 2: Write the test**

Append to `tests/test_review.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "review"
# The fours thresholds the 2026-09-27..10-05 backtest gave (p2 of 506 ends) and
# the doubles ones (118 ends), as the endpoint would have them.
BACKTEST = Baseline({("fours", "broom"): 0.5, ("fours", "split"): 0.75,
                     ("fours", "line"): 0.5, ("fours", "release"): 0.88,
                     ("doubles", "broom"): 0.0, ("doubles", "split"): 0.8,
                     ("doubles", "line"): 0.8, ("doubles", "release"): 0.8},
                    {"fours": 506, "doubles": 118})


class TestRealGames:
    """Four hosted games, as served on 2026-10-05, read the way the backtest read them."""

    @pytest.mark.parametrize("sid,expected,flagged", [
        # Super League 09/30 S5 (EUpp): the brooms and lines of two ends lost,
        # and one end missing all 8 of one colour.
        ("s_0qsNY1Vdc3vynNPx5", ["coverage_broom", "coverage_line", "missing_rocks"], True),
        # Supper 09/29 S3 (LFvF): e8 back to e7's house with 4 rocks -- the
        # next draw's first -- plus an unplaced rock and an odd split.
        ("s_11DBbXARm30gMQEsy", ["odd_split", "same_house", "tiny_last_end", "unplaced"], True),
        ("s_00hRGMbcChFTNviqg", [], False),          # a clean fours game, 7 ends
        ("s_0PXVGfZmiusxIlyX5", [], False),          # a clean doubles game, no brooms
    ])
    def test_the_backtest_reading(self, sid, expected, flagged):
        doc = json.loads((FIXTURES / f"{sid}.json").read_text())
        got = autoreview.review_game(doc["games"][0], BACKTEST)
        assert sorted({f.check for f in got.findings}) == expected
        assert got.raise_flag is flagged
```

Move the two new imports (`json`, `Path`) to the top of the file with the others.

- [ ] **Step 3: Run the test**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py`

Expected: PASS. If a game's set differs, don't edit the expectation to match. Run `~/curling-work/nightly-review/pick.py` logic on the same file, find which rule disagrees with the spec's table, and fix the module. The expectations come from the approved backtest.

- [ ] **Step 4: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add tests/fixtures/review tests/test_review.py
git commit -m "autoreview: pin the backtest's reading of four hosted games

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `scripts/review.py`, the local runner

**Files:**
- Create: `scripts/review.py`
- Test: `tests/test_review_script.py`

**Interfaces:**
- Consumes: `autoreview.review_game`, `Baseline.from_ends`, `end_metrics`, `game_format`, `summary`.
- Produces: the CLI `python scripts/review.py DIR [--baseline-from DIR2] [--any]`, plus `load(folder) -> Iterator[tuple[str, dict, dict]]`, `baseline_from(folder) -> Baseline` and `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_script.py`:

```python
"""scripts/review.py: the nightly review over a folder of downloaded timelines."""

import importlib.util
import json
import shutil
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "review_script", Path(__file__).resolve().parents[1] / "scripts" / "review.py")
review_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_script)

FIXTURES = Path(__file__).parent / "fixtures" / "review"


def test_it_lists_the_games_it_would_flag_and_counts_each_check(tmp_path, capsys):
    for p in FIXTURES.glob("*.json"):
        shutil.copy(p, tmp_path / p.name)
    assert review_script.main([str(tmp_path), "--any"]) == 0
    out = capsys.readouterr().out
    assert "s_11DBbXARm30gMQEsy  LFvFYGqdrlk S3" in out
    assert "s_0qsNY1Vdc3vynNPx5  EUpphjp9UMc S5" in out
    assert "s_00hRGMbcChFTNviqg" not in out.split("\n\n")[1]   # clean: not listed
    assert "2/4 games flagged; 2 with any finding" in out
    assert "same_house" in out and "threshold" in out


def test_a_folder_too_small_for_a_baseline_says_so(tmp_path, capsys):
    shutil.copy(FIXTURES / "s_00hRGMbcChFTNviqg.json", tmp_path / "a.json")
    review_script.main([str(tmp_path)])
    assert "fixed floor" in capsys.readouterr().out


def test_a_file_that_is_not_a_timeline_is_skipped(tmp_path, capsys):
    (tmp_path / "games.json").write_text(json.dumps({"games": "not a list"}))
    assert review_script.main([str(tmp_path)]) == 0
    assert "skipped games.json" in capsys.readouterr().err
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review_script.py`

Expected: FAIL with `FileNotFoundError` for `scripts/review.py`.

- [ ] **Step 3: Implement**

Create `scripts/review.py`:

```python
#!/usr/bin/env python
"""The nightly review over downloaded timelines, to tune what it flags.

    python scripts/review.py DIR [--baseline-from DIR2] [--any]

DIR holds served timelines, one game each -- /g/<source_id>/timeline.json saved
as <source_id>.json. Prints each game the nightly review would flag, how many
games each check fired on, and the coverage thresholds it judged them by.
The baseline is DIR's own ends unless --baseline-from names another folder;
--any also counts games with any finding at all, flagged or not.
"""

import argparse
import collections
import glob
import json
import os
import sys

from curling_score import autoreview


def load(folder):
    """(name, document, game) for every game of every timeline in ``folder``."""
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        name = os.path.basename(path)[:-5]
        try:
            with open(path) as fh:
                doc = json.load(fh)
            games = doc["games"]
            if not isinstance(games, list):
                raise TypeError("games is not a list")
        except (OSError, ValueError, KeyError, TypeError) as err:
            print(f"skipped {name}.json: {err}", file=sys.stderr)
            continue
        for game in games:
            yield name, doc, game


def baseline_from(folder) -> autoreview.Baseline:
    return autoreview.Baseline.from_ends(
        (autoreview.game_format(g), autoreview.end_metrics(e))
        for _n, _d, g in load(folder) for e in g.get("ends", []))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--baseline-from")
    ap.add_argument("--any", action="store_true")
    args = ap.parse_args(argv)
    base = baseline_from(args.baseline_from or args.dir)
    print("thresholds:", ", ".join(
        f"{fmt} {k} {v:.2f}" for (fmt, k), v in sorted(base.thresholds.items()))
        or f"none -- fewer than {autoreview.BASELINE_MIN_ENDS} ends a format, "
           f"so the fixed floor ({autoreview.COVERAGE_FLOOR:.0%})")
    print()
    counts, total, flagged, anything = collections.Counter(), 0, 0, 0
    for name, doc, game in load(args.dir):
        got = autoreview.review_game(game, base)
        total += 1
        anything += bool(got.findings)
        counts.update({f.check for f in got.findings})
        if got.raise_flag:
            flagged += 1
            src = doc.get("source", {})
            print(f"{name}  {src.get('video_id')} S{src.get('sheet')}  "
                  f"{autoreview.summary(got)}")
    print(f"\n{flagged}/{total} games flagged"
          + (f"; {anything} with any finding" if args.any else ""))
    for check, n in counts.most_common():
        print(f"  {check:20} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

The first test splits the output on a blank line and looks at the second block. The thresholds line comes first, then the flagged games, then the totals. Check that the printed layout matches; if it doesn't, adjust the test's split, not the layout.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review_script.py`

Expected: PASS.

- [ ] **Step 5: Check it against the whole backtest**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/python scripts/review.py /home/tcuser/curling-work/nightly-review/tl --any | tail -15`

Expected: about `38/126 games flagged`. It may land a few lower, because `not_alternating` no longer fires on gaps (refinement 5). Record the actual figure in the task report.

- [ ] **Step 6: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add scripts/review.py tests/test_review_script.py
git commit -m "scripts/review.py: the nightly review over downloaded timelines

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Records and repository: `Review`, auto-flag fields, the new queries

**Files:**
- Modify: `src/curling_score/service/records.py` (`Flag`, add `Review`, `review_id`)
- Modify: `src/curling_score/service/repo.py` (Protocol + `MemoryRepo`)
- Modify: `src/curling_score/service/firestore_repo.py`
- Modify: `src/curling_score/service/restore.py` (`_TIME_FIELDS`)
- Test: `tests/test_repo_contract.py`, `tests/test_service_core.py`

**Interfaces:**
- Produces:
  - `Flag.origin: str = "viewer"` and `Flag.findings: list = []`.
  - `Review(id, source_id, run_id, reviewed_at, video_id=None, game_index=None, format=None, run_ready_at=None, processing_version=None, ends=[], findings=[], notes=[], flag_id=None, error=None)`.
  - `review_id(source_id: str, run_id: str) -> str`, which is `f"{source_id}_{run_id}"`.
  - Repo methods, each returning these types:
    - `put_review(review: Review) -> None`
    - `get_review(review_id: str) -> Review | None`
    - `reviews_ready_between(t0: datetime, t1: datetime) -> list[Review]` (`t0 <= run_ready_at < t1`)
    - `reviews_for_source(source_id: str) -> list[Review]` (newest `reviewed_at` first)
    - `runs_ready_since(t: datetime) -> list[Run]` (`ready_at >= t`, oldest first)
    - `put_flag_if_absent(flag: Flag) -> bool`
  - `export_all()["reviews"]`, and `import_all` reads it back.

- [ ] **Step 1: Write the failing tests**

In `tests/test_repo_contract.py`:
- add `Review` and `review_id` to the `records` import;
- add `"reviews"` to the collection list in `_firestore_repo`;
- add these classes after `TestFlags`.

```python
class TestReviews:
    def review(self, **kw):
        base = dict(id=review_id("s_1", "r_1"), source_id="s_1", run_id="r_1",
                    reviewed_at=T0, format="fours", run_ready_at=T0,
                    ends=[{"number": 1, "rocks": 16, "broom": 15, "split": 16, "line": 16,
                           "release": 16}],
                    findings=[{"check": "odd_split", "strength": "weak", "end": 1, "rock": 9,
                               "detail": "split 23.4 s", "t_video_s": 1234.5}])
        base.update(kw)
        return Review(**base)

    def test_put_and_get(self, repo):
        repo.put_review(self.review())
        got = repo.get_review("s_1_r_1")
        assert got.ends[0]["broom"] == 15 and got.findings[0]["rock"] == 9
        assert repo.get_review("s_9_r_9") is None

    def test_between_is_half_open_on_when_the_run_finished(self, repo):
        for i, t in enumerate((-60, 0, 60)):
            repo.put_review(self.review(id=f"s_{i}_r_1", source_id=f"s_{i}",
                                        run_ready_at=at(t)))
        got = repo.reviews_ready_between(at(-60), at(60))
        assert sorted(r.id for r in got) == ["s_0_r_1", "s_1_r_1"]

    def test_for_a_source_newest_first(self, repo):
        repo.put_review(self.review(id="s_1_r_1", reviewed_at=at(0)))
        repo.put_review(self.review(id="s_1_r_2", run_id="r_2", reviewed_at=at(60)))
        repo.put_review(self.review(id="s_2_r_1", source_id="s_2"))
        assert [r.id for r in repo.reviews_for_source("s_1")] == ["s_1_r_2", "s_1_r_1"]


class TestRunsReadySince:
    def test_only_runs_that_finished_since_oldest_first(self, repo):
        repo.put_run(run(id="r_never"))
        repo.put_run(run(id="r_old", status="ready", ready_at=at(-10)))
        repo.put_run(run(id="r_b", status="ready", ready_at=at(20)))
        repo.put_run(run(id="r_a", status="ready", ready_at=at(10)))
        assert [r.id for r in repo.runs_ready_since(at(0))] == ["r_a", "r_b"]


class TestFlagIfAbsent:
    def test_the_first_write_wins_and_a_resolved_flag_stays_resolved(self, repo):
        f = Flag(id="fa_1", created_at=T0, note="first", origin="auto",
                 findings=[{"check": "same_house"}])
        assert repo.put_flag_if_absent(f) is True
        repo.resolve_flag("fa_1", at(60))
        again = Flag(id="fa_1", created_at=at(120), note="second", origin="auto")
        assert repo.put_flag_if_absent(again) is False
        (got,) = repo.list_flags()
        assert (got.note, got.status, got.origin) == ("first", "resolved", "auto")
        assert got.findings == [{"check": "same_house"}]

    def test_a_flag_from_before_origins_reads_as_a_viewer_flag(self, repo):
        repo.put_flag(Flag(id="f_1", created_at=T0, note="n"))
        (got,) = repo.list_flags()
        assert got.origin == "viewer" and got.findings == []
```

Check that the module-level `run()` helper in this file accepts `ready_at`. It builds a `Run(**base)` from keyword overrides, so any `Run` field works.

In `TestBackup.test_export_then_import_into_a_fresh_store`, add a review before `data = repo.export_all()`:

```python
        repo.put_review(Review(id="s_1_r_1", source_id="s_1", run_id="r_1",
                               reviewed_at=T0, run_ready_at=T0, flag_id="fa_1"))
```

and after the `fresh.import_all(data)` asserts:

```python
        assert fresh.get_review("s_1_r_1").flag_id == "fa_1"
```

In `tests/test_service_core.py`, after `test_restore_revives_a_flags_times`:

```python
def test_restore_revives_a_reviews_times():
    from curling_score.service.repo import MemoryRepo
    from curling_score.service.restore import restore

    repo = MemoryRepo()
    restore(repo, {"reviews": [{"id": "s_1_r_1", "source_id": "s_1", "run_id": "r_1",
                                "reviewed_at": "2026-10-05T10:00:00+00:00",
                                "run_ready_at": "2026-10-05T06:30:00+00:00"}]})
    got = repo.get_review("s_1_r_1")
    assert got.reviewed_at.hour == 10 and got.run_ready_at.hour == 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_repo_contract.py tests/test_service_core.py`

Expected: FAIL with `ImportError: cannot import name 'Review'`. The Firestore half skips unless `FIRESTORE_EMULATOR_HOST` is set.

- [ ] **Step 3: Implement the records**

In `src/curling_score/service/records.py`, add two fields to `Flag`, after `ip_hash`:

```python
    # "viewer": somebody pressed ⚑ Flag. "auto": the nightly review found
    # something (curling_score.autoreview), and `findings` says what, rock by
    # rock -- each {check, strength, end, rock, detail, t_video_s}.
    origin: str = "viewer"
    findings: list = field(default_factory=list)
```

Add after `Flag`:

```python
def review_id(source_id: str, run_id: str) -> str:
    """One review per game per run; a run retried in place replaces it."""
    return f"{source_id}_{run_id}"


@dataclass
class Review:
    """What the nightly review read in one game: each end's coverage, which
    later nights' baseline is made of; what it found; and the flag it raised.

    `run_ready_at` is the run's `ready_at` when it was read. A run retried in
    place finishes again later, and that is how the review knows to read it
    again. `error` is why the game could not be read; such a game is not
    tried again by later calls.
    """

    id: str
    source_id: str
    run_id: str
    reviewed_at: datetime
    video_id: str | None = None
    game_index: int | None = None
    format: str | None = None             # "fours" | "doubles"
    run_ready_at: datetime | None = None
    processing_version: str | None = None
    ends: list = field(default_factory=list)       # autoreview.EndMetrics dicts
    findings: list = field(default_factory=list)   # autoreview.Finding dicts
    notes: list = field(default_factory=list)
    flag_id: str | None = None
    error: str | None = None

    to_dict = asdict
    from_dict = classmethod(_from_dict)
```

In `src/curling_score/service/restore.py`, add `"reviewed_at", "run_ready_at"` to `_TIME_FIELDS`.

- [ ] **Step 4: Implement the repositories**

In `src/curling_score/service/repo.py`:
- add `Review` to the records import;
- add to the `Repo` Protocol under `# flags`:

```python
    def put_flag_if_absent(self, flag: Flag) -> bool: ...
```

- add a section to the Protocol:

```python
    # reviews -- the nightly review's reading of each game, per run
    def put_review(self, review: Review) -> None: ...
    def get_review(self, review_id: str) -> Review | None: ...
    def reviews_ready_between(self, t0: datetime, t1: datetime) -> list[Review]: ...
    def reviews_for_source(self, source_id: str) -> list[Review]: ...
```

- add under `# runs`:

```python
    def runs_ready_since(self, t: datetime) -> list[Run]: ...
```

In `MemoryRepo.__init__`, add `self.reviews: dict[str, Review] = {}`. Then add the methods:

```python
    def runs_ready_since(self, t):
        got = [r for r in self.runs.values() if r.ready_at is not None and r.ready_at >= t]
        return sorted(got, key=lambda r: r.ready_at)
```

(next to `list_runs`)

```python
    def put_flag_if_absent(self, flag):
        with self._lock:
            if flag.id in self.flags:
                return False
            self.flags[flag.id] = flag
            return True

    # ---- reviews ------------------------------------------------------
    def put_review(self, review):
        with self._lock:
            self.reviews[review.id] = review

    def get_review(self, review_id):
        return self.reviews.get(review_id)

    def reviews_ready_between(self, t0, t1):
        return [r for r in self.reviews.values()
                if r.run_ready_at is not None and t0 <= r.run_ready_at < t1]

    def reviews_for_source(self, source_id):
        got = [r for r in self.reviews.values() if r.source_id == source_id]
        return sorted(got, key=lambda r: r.reviewed_at, reverse=True)
```

(after `resolve_flag`)

In `export_all`, add `"reviews": [r.to_dict() for r in self.reviews.values()],`. In `import_all`, after the plays loop, add:

```python
            for d in data.get("reviews", []):
                self.put_review(Review.from_dict(d))
```

In `src/curling_score/service/firestore_repo.py`:
- add `Review` to the records import;
- add `REVIEWS = "reviews"` (on the `FLAGS, PLAYLIST_INDEX, PLAYS` line, or below it);
- add to the module docstring's list, after the indexes: "Single-field ranges only, which Firestore indexes by itself: `vod_runs.ready_at`, `reviews.run_ready_at`."
- add the methods:

```python
    def runs_ready_since(self, t):
        # A range on one field: Firestore's automatic index, no composite.
        q = self._where(RUNS, "ready_at", ">=", t).order_by("ready_at")
        return [Run.from_dict(d.to_dict()) for d in q.stream()]
```

(next to `list_runs`)

```python
    def put_flag_if_absent(self, flag):
        from google.api_core.exceptions import AlreadyExists

        try:
            self._col(FLAGS).document(flag.id).create(flag.to_dict())
        except AlreadyExists:
            return False
        return True

    # ---- reviews ------------------------------------------------------
    def put_review(self, review):
        self._col(REVIEWS).document(review.id).set(review.to_dict())

    def get_review(self, review_id):
        return self._get(REVIEWS, review_id, Review)

    def reviews_ready_between(self, t0, t1):
        q = (self._where(REVIEWS, "run_ready_at", ">=", t0)
             .where(filter=self._fs.FieldFilter("run_ready_at", "<", t1)))
        return [Review.from_dict(d.to_dict()) for d in q.stream()]

    def reviews_for_source(self, source_id):
        # One equality filter, sorted here: no composite index to keep.
        q = self._where(REVIEWS, "source_id", "==", source_id)
        docs = [Review.from_dict(d.to_dict()) for d in q.stream()]
        return sorted(docs, key=lambda r: r.reviewed_at, reverse=True)
```

(after `resolve_flag`)

- In `export_all`, add `"reviews": dump(REVIEWS)`. In `import_all`, add the same loop as `MemoryRepo`'s.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_repo_contract.py tests/test_service_core.py tests/test_service_api.py -k "flag or backup or restore or Review or Ready or Absent"`

Expected: PASS, with Firestore cases skipped. If `gcloud emulators firestore` is available here, also run the contract tests with `FIRESTORE_EMULATOR_HOST` set and report the result. If not, say they were skipped.

- [ ] **Step 6: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add src/curling_score/service/records.py src/curling_score/service/repo.py src/curling_score/service/firestore_repo.py src/curling_score/service/restore.py tests/test_repo_contract.py tests/test_service_core.py
git commit -m "records: a Review per game per run, and flags that say they were automatic

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The endpoint, `POST /api/admin/review`

**Files:**
- Modify: `src/curling_score/service/api.py` (imports; new block after the admin flag routes, around `@app.post("/api/admin/flags/{flag_id}/resolve")`)
- Test: `tests/test_review_api.py`

**Interfaces:**
- Consumes: `autoreview.*` (Tasks 1-3); `Review`, `review_id`, `Flag.origin/findings` and the repo methods (Task 6); and these existing `api.py` names: `game_doc(run, game_index, src, start_s)`, `require_admin`, `now`, `_flag_json`, `MAX_FLAG_NOTE`, `log`.
- Produces: `POST /api/admin/review`.
  - Query parameters: `dry_run: bool = False`, `flag: bool = True`, `since: str | None` (ISO), `until: str | None` (ISO; only runs that finished before it), `source_id: str | None`, `limit: int = 25` (1-200).
  - Returns `{"ok": True, "reviewed": int, "flagged": int, "errors": [{"source_id", "error"}], "pending": int}`, plus `"would_flag": [{"source_id", "title", "flag"}]` when `dry_run`.
  - Errors: 401/503 for the admin token, 404 for an unknown `source_id`, 409 when that game's run isn't ready, 422 for a bad `since`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_api.py`:

```python
"""The nightly review through the API: which games it reads, the flags it
raises, and that running it again -- or after a restore -- never doubles one."""

from datetime import timedelta

import pytest

from curling_score import autoreview
from curling_score.service.records import Review
from tests.test_service_api import ADMIN, T0, submit, work_through, world  # noqa: F401


def rock(n, t, broom=True):
    return {"number": n, "color": "red" if n % 2 else "yellow", "position": "lead",
            "rock_of_player": 1, "label": f"rock {n}", "t_rest_s": t + 20.0,
            "t_enter_s": t + 12.0, "t_video_s": t, "state_known": True, "missing": False,
            "shot_type": "draw", "stones": [], "track": [],
            "target_broom": {"x": 0.2, "y": 0.0, "confidence": 0.9} if broom else None,
            "long_split_s": 14.0, "line": {"at_broom": {"miss_m": 0.2}},
            "t_release_s": t - 4.0, "color_inferred": False}


def end(number, house, start, brooms=16):
    return {"number": number, "house": house, "start_s": start, "end_s": start + 600.0,
            "score": {"red": 1, "yellow": 0}, "detected_score": {"red": 1, "yellow": 0},
            "running": {"red": number, "yellow": 0}, "shots_expected": 16,
            "unplaced_shots": 0,
            "shots": [rock(n, start + 30.0 * n, broom=n > 16 - brooms) for n in range(1, 17)]}


def doc_of(*games):
    """A timeline whose games each have the ends ``houses`` names, 16 clean rocks
    an end; ``brooms`` maps an end number to how many of its rocks keep one."""
    out = []
    for i, (houses, brooms) in enumerate(games):
        start = 100.0 + i * 7400.0
        ends = [end(n, h, start + 700.0 * (n - 1), brooms.get(n, 16))
                for n, h in enumerate(houses, 1)]
        out.append({"index": i, "start_s": start, "end_s": start + 700.0 * len(houses),
                    "teams": {"red": {"name": None}, "yellow": {"name": None}},
                    "final": {"red": 0, "yellow": 0}, "hammer_consistent": True,
                    "ends": ends})
    return {"schema_version": 8, "processing_version": "2026.10.05+m-abc",
            "source": {"url": "u", "video_id": "VXU9xwmugRg", "sheet": 2,
                       "duration_s": 14392.0, "window": {"start_s": None, "end_s": None}},
            "calibration": {}, "games": out}


CLEAN = (["top", "bottom", "top", "bottom"], {})
SAME_HOUSE = (["top", "bottom", "bottom", "top"], {})
LOW_BROOMS = (["top", "bottom", "top", "bottom"], {2: 6})


def charted(w, *games):
    submit(w)
    work_through(w, doc=doc_of(*games), games=len(games))
    return sorted(w["repo"].list_sources(), key=lambda s: s.game_index)


def review(w, **params):
    r = w["client"].post("/api/admin/review", params=params, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def flags(w):
    return w["repo"].list_flags()


class TestAccess:
    def test_it_needs_the_admin_token(self, world):
        assert world["client"].post("/api/admin/review").status_code == 401


class TestReviewing:
    def test_a_clean_game_is_read_and_not_flagged(self, world):
        (src,) = charted(world, CLEAN)
        got = review(world)
        assert (got["reviewed"], got["flagged"], got["pending"]) == (1, 0, 0)
        rec = world["repo"].get_review(f"{src.id}_{src.current_run_id}")
        assert rec.format == "fours" and [e["broom"] for e in rec.ends] == [16] * 4
        assert rec.flag_id is None and rec.error is None and flags(world) == []

    def test_a_game_that_looks_wrong_gets_one_auto_flag(self, world):
        (src,) = charted(world, SAME_HOUSE)
        got = review(world)
        assert got["flagged"] == 1
        (f,) = flags(world)
        run = world["repo"].get_run(src.current_run_id)
        assert f.id == autoreview.flag_id(src.id, run.id, run.ready_at)
        assert f.origin == "auto" and f.status == "open"
        assert f.where == {"link": "g", "chart_id": None, "share_slug": None,
                           "source_id": src.id, "run_id": run.id, "video_id": run.video_id,
                           "processing_version": run.processing_version, "title": run.title}
        assert (f.place["end"], f.place["rock"], f.place["game_index"]) == (3, None, 0)
        assert f.note.startswith("Auto-review: 1 finding — e3 e2 and e3 both to the bottom house")
        assert [x["check"] for x in f.findings] == ["same_house"]
        listed = world["client"].get("/api/admin/flags", headers=ADMIN).json()["flags"]
        assert listed[0]["origin"] == "auto" and listed[0]["findings"][0]["end"] == 3

    def test_a_second_call_reads_nothing_new(self, world):
        charted(world, SAME_HOUSE)
        review(world)
        got = review(world)
        assert (got["reviewed"], got["flagged"], got["pending"]) == (0, 0, 0)
        assert len(flags(world)) == 1

    def test_runs_that_finished_before_the_lookback_are_left(self, world):
        charted(world, SAME_HOUSE)
        world["clock"].advance(4 * 86400)
        assert review(world)["reviewed"] == 0

    def test_since_reaches_further_back(self, world):
        charted(world, SAME_HOUSE)
        world["clock"].advance(10 * 86400)
        assert review(world, since=(T0 - timedelta(days=1)).isoformat())["reviewed"] == 1

    def test_until_stops_short_of_recent_runs(self, world):
        charted(world, SAME_HOUSE)
        assert review(world, until=(T0 - timedelta(minutes=1)).isoformat())["reviewed"] == 0
        assert review(world, until=(T0 + timedelta(minutes=1)).isoformat())["reviewed"] == 1

    @pytest.mark.parametrize("param", ["since", "until"])
    def test_since_and_until_must_be_times(self, world, param):
        r = world["client"].post("/api/admin/review", params={param: "last tuesday"},
                                 headers=ADMIN)
        assert r.status_code == 422


class TestModes:
    def test_a_dry_run_writes_nothing_and_says_what_it_would_flag(self, world):
        (src,) = charted(world, SAME_HOUSE)
        got = review(world, dry_run=True)
        assert got["reviewed"] == 1 and got["flagged"] == 1
        (would,) = got["would_flag"]
        assert would["source_id"] == src.id and would["flag"]["origin"] == "auto"
        assert flags(world) == [] and world["repo"].reviews_for_source(src.id) == []

    def test_without_flags_it_only_records_what_it_read(self, world):
        (src,) = charted(world, SAME_HOUSE)
        got = review(world, flag=False)
        assert got["reviewed"] == 1 and got["flagged"] == 0 and flags(world) == []
        (rec,) = world["repo"].reviews_for_source(src.id)
        assert rec.flag_id is None and rec.findings[0]["check"] == "same_house"

    def test_one_game_on_request_even_if_read_already(self, world):
        (src,) = charted(world, SAME_HOUSE)
        review(world)
        got = review(world, source_id=src.id)
        assert got["reviewed"] == 1 and got["flagged"] == 0    # same flag: not written twice
        assert len(flags(world)) == 1

    def test_an_unknown_game_on_request(self, world):
        r = world["client"].post("/api/admin/review", params={"source_id": "s_nope"},
                                 headers=ADMIN)
        assert r.status_code == 404


class TestBatches:
    def test_limit_leaves_the_rest_for_the_next_call(self, world):
        charted(world, CLEAN, CLEAN, CLEAN)
        assert (review(world, limit=2)["reviewed"], review(world)["reviewed"]) == (2, 1)

    def test_the_time_budget_stops_starting_games_but_always_does_one(self, world, monkeypatch):
        charted(world, CLEAN, CLEAN, CLEAN)
        monkeypatch.setattr(autoreview, "TIME_BUDGET_S", -1.0)
        got = review(world)
        assert (got["reviewed"], got["pending"]) == (1, 2)


class TestTheBaseline:
    def prior(self, w, at, brooms, n=110):
        for i in range(n):
            w["repo"].put_review(Review(
                id=f"s_p{i}_r_p", source_id=f"s_p{i}", run_id="r_p", reviewed_at=at,
                format="fours", run_ready_at=at,
                ends=[{"number": 1, "rocks": 16, "broom": brooms, "split": 16, "line": 16,
                       "release": 16}]))

    def test_with_no_history_the_floor_flags_a_thin_end(self, world):
        charted(world, LOW_BROOMS)                     # end 2: 6 of 16 brooms
        assert review(world)["flagged"] == 1

    def test_normal_comes_from_the_fortnight_before(self, world):
        self.prior(world, T0 - timedelta(days=1), brooms=4)    # 25% is normal lately
        charted(world, LOW_BROOMS)
        assert review(world)["flagged"] == 0

    def test_reviews_from_the_batch_or_after_are_not_its_normal(self, world):
        self.prior(world, T0 + timedelta(hours=1), brooms=4)
        charted(world, LOW_BROOMS)
        assert review(world)["flagged"] == 1


class TestNeverTwice:
    def test_a_resolved_auto_flag_is_never_reopened(self, world):
        (src,) = charted(world, SAME_HOUSE)
        review(world)
        (f,) = flags(world)
        world["client"].post(f"/api/admin/flags/{f.id}/resolve", headers=ADMIN)
        world["repo"].reviews.clear()                  # a restore that lost the reviews
        got = review(world)
        assert got["reviewed"] == 1 and got["flagged"] == 0
        (again,) = flags(world)
        assert again.status == "resolved"

    def test_a_run_retried_in_place_is_read_again_and_names_the_earlier_flag(self, world):
        (src,) = charted(world, SAME_HOUSE)
        review(world)
        (first,) = flags(world)
        world["repo"].update_run(src.current_run_id, ready_at=T0 + timedelta(hours=1))
        got = review(world)
        assert got["reviewed"] == 1 and got["flagged"] == 1
        second = next(f for f in flags(world) if f.id != first.id)
        assert f"earlier auto-flag {first.id} (run {src.current_run_id})" in second.note
        assert first.status == "open"                  # never resolved for you


class TestWhatIsNotAGame:
    def test_a_page_merged_into_another_game_is_not_read(self, world):
        a, b = charted(world, CLEAN, SAME_HOUSE)
        world["repo"].update_source(b.id, merged_into=a.id)
        got = review(world)
        assert got["reviewed"] == 1 and flags(world) == []

    def test_a_live_run_waits_until_it_is_ready(self, world):
        (src,) = charted(world, SAME_HOUSE)
        world["repo"].update_run(src.current_run_id, status="live")
        assert review(world)["reviewed"] == 0
        world["repo"].update_run(src.current_run_id, status="ready")
        assert review(world)["reviewed"] == 1

    def test_a_game_missing_from_its_run_is_an_error_and_the_rest_go_on(self, world):
        a, b = charted(world, CLEAN, SAME_HOUSE)
        world["repo"].update_source(a.id, game_index=99)
        got = review(world)
        assert got["reviewed"] == 2 and got["flagged"] == 1
        assert got["errors"] == [{"source_id": a.id,
                                  "error": f"LookupError: game 99 is not in run {a.current_run_id}"}]
        rec = world["repo"].get_review(f"{a.id}_{a.current_run_id}")
        assert rec.error.startswith("LookupError") and rec.flag_id is None
        assert review(world)["reviewed"] == 0          # not tried again

    def test_a_game_that_breaks_the_review_is_recorded_and_the_rest_go_on(self, world,
                                                                          monkeypatch):
        a, b = charted(world, CLEAN, SAME_HOUSE)
        real = autoreview.review_game

        def fussy(game, baseline):
            if game["index"] == 0:
                raise ValueError("no idea")
            return real(game, baseline)

        monkeypatch.setattr(autoreview, "review_game", fussy)
        got = review(world)
        assert got["errors"] == [{"source_id": a.id, "error": "ValueError: no idea"}]
        assert got["flagged"] == 1

    def test_a_timeline_from_before_the_newer_fields(self, world):
        from tests.test_service_api import sample_doc
        submit(world)
        work_through(world, doc=sample_doc(1), games=1)
        got = review(world)
        assert got["reviewed"] == 1 and got["errors"] == []
        assert got["flagged"] == 1                     # one end: a short game
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review_api.py`

Expected: FAIL. Every call returns 404 or 405, because the route doesn't exist yet.

- [ ] **Step 3: Implement**

In `src/curling_score/service/api.py`:
- change `from curling_score import timeline, version, viewer` to `from curling_score import autoreview, timeline, version, viewer`;
- add `Review` and `review_id` to the `curling_score.service.records` import;
- add this block right after the `admin_resolve_flag` route:

```python
    # ------------------------------------------------------------- review
    # Each night every game that finished processing is read for the signs
    # that something went wrong (curling_score.autoreview), and one that shows
    # them gets a ⚑ flag of its own beside the ones viewers send. Cloud
    # Scheduler calls this hourly from 03:00 to 06:00 (deploy/scheduler.sh,
    # REVIEW=1); each call does what fits in its time and leaves the rest.

    def review_candidates(since: datetime, until: datetime | None = None) -> list:
        """(source, run) for each game whose current run finished since
        ``since`` (and before ``until``) and has not been read at that finish,
        oldest first."""
        out = []
        for run in repo.runs_ready_since(since):
            if run.status != "ready" or run.ready_at is None:
                continue
            if until is not None and run.ready_at >= until:
                continue
            for src in repo.sources_for_video(run.video_id):
                if src.current_run_id != run.id or src.merged_into is not None:
                    continue
                done = repo.get_review(review_id(src.id, run.id))
                if (done is not None and done.run_ready_at is not None
                        and done.run_ready_at >= run.ready_at):
                    continue
                out.append((src, run))
        out.sort(key=lambda pair: (pair[1].ready_at, pair[0].game_index))
        return out

    def review_baseline(before: datetime) -> autoreview.Baseline:
        """Normal, from the games read in the fortnight before ``before`` --
        never from the batch itself, so a bad night cannot hide in its own."""
        since = before - timedelta(days=autoreview.BASELINE_DAYS)
        return autoreview.Baseline.from_ends(
            (r.format, m) for r in repo.reviews_ready_between(since, before)
            if r.format and not r.error for m in r.ends)

    def auto_flag(src: Source, run: Run, got: autoreview.GameReview) -> Flag:
        fid = autoreview.flag_id(src.id, run.id, run.ready_at)
        first = next((f for f in got.findings if f.strength == "strong"), got.findings[0])
        earlier = next((r for r in repo.reviews_for_source(src.id)
                        if r.flag_id and r.flag_id != fid), None)
        extra = f"earlier auto-flag {earlier.flag_id} (run {earlier.run_id})" if earlier else ""
        return Flag(
            id=fid, created_at=now(), note=autoreview.summary(got, extra, MAX_FLAG_NOTE),
            where={"link": "g", "chart_id": None, "share_slug": None, "source_id": src.id,
                   "run_id": run.id, "video_id": run.video_id,
                   "processing_version": run.processing_version, "title": run.title},
            place={"game_index": src.game_index, "end": first.end, "rock": first.rock,
                   "t_video_s": first.t_video_s},
            origin="auto", findings=[f.to_dict() for f in got.findings + got.notes])

    def review_one(src: Source, run: Run, baseline, with_flag: bool):
        """One game read: its record and the flag it would raise. Writes nothing."""
        try:
            doc = game_doc(run, src.game_index, src, src.play_start_s)
            if not doc.get("games"):
                raise LookupError(f"game {src.game_index} is not in run {run.id}")
            got, error = autoreview.review_game(doc["games"][0], baseline), None
        except Exception as e:  # noqa: BLE001 - one bad game never stops the night
            log.exception("could not review %s (run %s)", src.id, run.id)
            got, error = None, f"{type(e).__name__}: {e}"[:500]
        flag = auto_flag(src, run, got) if with_flag and got and got.raise_flag else None
        record = Review(
            id=review_id(src.id, run.id), source_id=src.id, run_id=run.id,
            reviewed_at=now(), video_id=run.video_id, game_index=src.game_index,
            format=got.format if got else None, run_ready_at=run.ready_at,
            processing_version=run.processing_version,
            ends=[m.to_dict() for m in got.ends] if got else [],
            findings=[f.to_dict() for f in got.findings] if got else [],
            notes=[f.to_dict() for f in got.notes] if got else [],
            flag_id=flag.id if flag else None, error=error)
        return record, flag

    def iso_param(name: str, value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            t = datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, f"{name} must be an ISO time") from None
        return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)

    @app.post("/api/admin/review")
    def admin_review(dry_run: bool = False, flag: bool = True, since: str | None = None,
                     until: str | None = None, source_id: str | None = None,
                     limit: int = Query(autoreview.BATCH, ge=1, le=200),
                     authorization: str | None = Header(default=None)):
        """Read the games that finished since ``since`` (default: the last
        LOOKBACK_DAYS), and before ``until`` if given, and flag the ones that
        look wrong.

        Idempotent: a game is read once per finish of its run, and its flag's
        id is derived, so a repeated call never adds a second -- nor reopens
        one that was resolved. ``dry_run`` writes nothing and returns the flags
        it would raise; ``flag=false`` records what it read but raises nothing,
        to give a new deployment its baseline (with ``until``, leaving the most
        recent games for the first real night); ``source_id`` reads that one
        game again now. Call it until ``pending`` is 0.
        """
        require_admin(authorization)
        started = now()
        if source_id is not None:
            src = repo.get_source(source_id)
            if src is None:
                raise HTTPException(404, "no such game")
            run = repo.get_run(src.current_run_id)
            if run is None or run.status != "ready" or run.ready_at is None:
                raise HTTPException(409, "that game's run is not ready")
            batch = [(src, run)]
        else:
            t0 = (iso_param("since", since)
                  or started - timedelta(days=autoreview.LOOKBACK_DAYS))
            batch = review_candidates(t0, iso_param("until", until))
        baseline = review_baseline(min(r.ready_at for _s, r in batch)) if batch else None
        reviewed, flagged, errors, would = 0, 0, [], []
        for src, run in batch[:limit]:
            if reviewed and (now() - started).total_seconds() > autoreview.TIME_BUDGET_S:
                break
            record, fl = review_one(src, run, baseline, with_flag=flag)
            reviewed += 1
            if record.error:
                errors.append({"source_id": src.id, "error": record.error})
            if fl is not None:
                if dry_run:
                    would.append({"source_id": src.id, "title": run.title,
                                  "flag": _flag_json(fl)})
                    flagged += 1
                elif repo.put_flag_if_absent(fl):
                    flagged += 1
            if not dry_run:
                repo.put_review(record)
        log.info("review: %d read, %d flagged, %d errors, %d left",
                 reviewed, flagged, len(errors), len(batch) - reviewed)
        out = {"ok": True, "reviewed": reviewed, "flagged": flagged, "errors": errors,
               "pending": len(batch) - reviewed}
        if dry_run:
            out["would_flag"] = would
        return out
```

`Source`, `Run`, `Flag` are already imported in `api.py`; check the import line and add any that are missing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review_api.py`

Expected: PASS.

`test_a_game_that_looks_wrong_gets_one_auto_flag` asserts the note's exact start. If the summary wording from Task 1 renders differently (for instance `describe` puts the end before a detail that already names the ends), keep the module's wording and fix the assertion. The note must start `Auto-review: 1 finding — ` and contain the same-house detail.

- [ ] **Step 5: Run the neighbouring suites**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_service_api.py tests/test_thinking_api.py tests/test_admin_playlists.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add src/curling_score/service/api.py tests/test_review_api.py
git commit -m "api: POST /api/admin/review reads the night's games and flags the odd ones

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `scripts/flags.py` shows auto-flags

**Files:**
- Modify: `scripts/flags.py`
- Test: `tests/test_flags_script.py`

**Interfaces:**
- Consumes: the flag JSON from `GET /api/admin/flags`, now carrying `origin` and `findings` (Task 6).
- Produces:
  - `rock_link(flag, base, place=None) -> str`, where `place` overrides `flag["place"]`
  - `finding_lines(flag, finding, base) -> list[str]`
  - `describe(flag, base)`, which marks auto-flags and lists their findings
  - `list --origin auto|viewer|all` (default `all`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_flags_script.py`:

```python
def auto_flag(**kw):
    return flag(id="fa_0123456789abcdef", user=None, origin="auto",
                note="Auto-review: 2 findings — e5 e4 and e5 both to the top house; e6 r9 split 23.4 s",
                place={"game_index": 0, "end": 5, "rock": None, "t_video_s": 4100.0},
                findings=[
                    {"check": "same_house", "strength": "strong", "end": 5, "rock": None,
                     "detail": "e4 and e5 both to the top house", "t_video_s": 4100.0},
                    {"check": "odd_split", "strength": "weak", "end": 6, "rock": 9,
                     "detail": "split 23.4 s", "t_video_s": 4712.3},
                    {"check": "hammer", "strength": "note", "end": None, "rock": None,
                     "detail": "hammer sequence broken", "t_video_s": None}],
                **kw)


def test_an_auto_flag_says_it_is_automatic():
    head = flags.describe(auto_flag(), BASE).splitlines()[0]
    assert head.startswith("fa_0123456789abcdef  open  auto  ") and head.endswith("auto-review")


def test_each_finding_gets_its_own_link():
    text = flags.describe(auto_flag(), BASE)
    assert "  - odd_split e6 r9: split 23.4 s" in text
    assert f"{BASE}/g/s_x/#e=6&s=9" in text and "https://youtu.be/VID?t=4712" in text
    assert "  - same_house e5: e4 and e5 both to the top house" in text
    assert "  notes: hammer sequence broken" in text


def test_a_viewer_flag_reads_as_it_did():
    text = flags.describe(flag(), BASE)
    assert "  auto  " not in text and "notes:" not in text


def test_list_can_show_one_origin(monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    monkeypatch.setattr(flags, "_call", lambda m, u, t: {"flags": [flag(), auto_flag()]})
    assert flags.main(["list", "--origin", "auto"]) == 0
    out = capsys.readouterr().out
    assert "fa_0123456789abcdef" in out and "f_abc " not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_flags_script.py`

Expected: FAIL. The header lacks `auto`, and argparse rejects `--origin`.

- [ ] **Step 3: Implement**

In `scripts/flags.py`:

1. In the module docstring's usage block, change the list line to:

```
    ADMIN_TOKEN=... python scripts/flags.py list [--status open|resolved|all] [--origin auto|viewer|all] [--limit N] [--json]
```

   Add a paragraph: "Auto-flags (origin "auto") come from the nightly review, one per game; each of their findings prints with its own link."

2. Replace `rock_link` with:

```python
def rock_link(flag: dict, base: str, place: dict | None = None) -> str:
    where, place = flag["where"], place if place is not None else flag["place"]
    prefix, field = PAGES[where["link"]]
    page = f"{base}{prefix}{where[field]}/"
    if place.get("rock") is None or place.get("end") is None:
        return page
    return f"{page}#e={place['end']}&s={place['rock']}"
```

3. Add after `youtube_link`:

```python
def finding_lines(flag: dict, finding: dict, base: str) -> list[str]:
    """One finding of an auto-flag: what, where, and the links to that rock."""
    at = " ".join(x for x in (
        f"e{finding['end']}" if finding.get("end") is not None else "",
        f"r{finding['rock']}" if finding.get("rock") is not None else "") if x)
    lines = [f"  - {finding.get('check')}{' ' + at if at else ''}: {finding.get('detail', '')}",
             f"    {rock_link(flag, base, finding)}"]
    yt = youtube_link({**flag, "place": finding})
    if yt:
        lines.append(f"    {yt}")
    return lines
```

4. In `describe`:

```python
    auto = flag.get("origin") == "auto"
    who = "auto-review" if auto else ((flag.get("user") or {}).get("email") or "anonymous")
```

   (replacing the existing `who = ...` line). Make the first line:

```python
    lines = [f"{flag['id']}  {flag['status']}{'  auto' if auto else ''}  {when}  {who}",
```

   After the `lines += [f"  > {line}" ...]` note lines, add:

```python
    if auto:
        found = flag.get("findings") or []
        for f in found:
            if f.get("strength") != "note":
                lines += finding_lines(flag, f, base)
        notes = [f.get("detail", "") for f in found if f.get("strength") == "note"]
        if notes:
            lines.append("  notes: " + "; ".join(notes))
```

5. In `main`, add to the `ls` parser:

```python
    ls.add_argument("--origin", default="all", choices=("auto", "viewer", "all"))
```

   After `got = _call(...)["flags"]` succeeds:

```python
        if args.origin != "all":
            got = [f for f in got if (f.get("origin") or "viewer") == args.origin]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_flags_script.py`

Expected: PASS, the existing viewer-flag tests included.

- [ ] **Step 5: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add scripts/flags.py tests/test_flags_script.py
git commit -m "flags.py: show auto-flags, a link per finding, and --origin

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Schedule and document it

**Files:**
- Modify: `deploy/scheduler.sh`
- Modify: `deploy/README.md` (the scheduler line in "Watching a league", around line 218, and a new section after it)

**Interfaces:**
- Consumes: `POST /api/admin/review` (Task 7).
- Produces: `REVIEW=1 ./deploy/scheduler.sh` also creates or updates the Cloud Scheduler job `curling-nightly-review`.

- [ ] **Step 1: Extend `deploy/scheduler.sh`**

Add after the existing `gcloud scheduler jobs create ... || ... update ...` command:

```bash
# The nightly review (POST /api/admin/review), with REVIEW=1: hourly from
# 03:00 to 06:00, after the last league's live job has finished. Each call
# reads up to 25 games and stops starting new ones after 40 s, so a spiel
# day's 40 games take two calls and the later ones find nothing left. The
# second of the three free Scheduler jobs. `update` takes --update-headers,
# not --headers.
if [ "${REVIEW:-}" = "1" ]; then
  gcloud scheduler jobs create http curling-nightly-review --project "$PROJECT_ID" \
    --location "$REGION" --schedule "0 3-6 * * *" --time-zone "America/Los_Angeles" \
    --uri "${PUBLIC_BASE_URL}/api/admin/review" --http-method POST \
    --headers "Authorization=Bearer ${ADMIN_TOKEN}" --attempt-deadline 120s \
    || gcloud scheduler jobs update http curling-nightly-review --project "$PROJECT_ID" \
    --location "$REGION" --schedule "0 3-6 * * *" --time-zone "America/Los_Angeles" \
    --uri "${PUBLIC_BASE_URL}/api/admin/review" \
    --update-headers "Authorization=Bearer ${ADMIN_TOKEN}"
fi
```

Also update the script's header comment: "One Cloud Scheduler job (free tier: 3 per account) ..." becomes "Cloud Scheduler jobs (free tier: 3 per account): the playlist poll always, and the nightly review with REVIEW=1."

Run: `bash -n deploy/scheduler.sh && echo ok`. Expected: `ok`.

- [ ] **Step 2: Document it in `deploy/README.md`**

Next to the scheduler line (`PUBLIC_BASE_URL=… ADMIN_TOKEN=… ./deploy/scheduler.sh  # poll every 3 min, 1 of 3 free Scheduler jobs`), add:

````markdown
REVIEW=1 PUBLIC_BASE_URL=… ADMIN_TOKEN=… ./deploy/scheduler.sh   # + the nightly review, 2 of 3
````

Add a section `## The nightly review` after `## Watching a league` (before `## The thinking report`):

````markdown
## The nightly review

Every game whose run finished in the last 3 days is read once for signs that
something went wrong (`curling_score/autoreview.py`): an end cut in two, lost
rocks, a fragment of a game, an end whose brooms/splits/lines/releases fall
well below the last fortnight's normal, a broom off the sheet, odd splits. A
game showing one strong sign (or two weak ones) gets one ⚑ flag with
`origin: "auto"`, in the same list as viewers' flags:

```
ADMIN_TOKEN=… python scripts/flags.py list --origin auto
```

Each finding prints with a link to its rock. Nothing is resolved or
reprocessed automatically. What it read is kept per game and run in the
`reviews` collection, which is also where "normal" comes from.

By hand (all take the admin token):

```
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE/api/admin/review?dry_run=true"   # what it would flag
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE/api/admin/review?source_id=s_…"  # read one game again
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE/api/admin/review?since=2026-09-19T00:00:00Z&until=2026-10-02T00:00:00Z&flag=false"  # seed the baseline, leaving the last 3 days
```

Repeat a call until `pending` is 0. To tune the checks, save served timelines
(`/g/<source_id>/timeline.json` as `<source_id>.json`) into a folder and run
`python scripts/review.py DIR --any`.
````

- [ ] **Step 3: Commit**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review
git add deploy/scheduler.sh deploy/README.md
git commit -m "deploy: schedule the nightly review (REVIEW=1) and say how to use it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Merge, deploy, seed, dry-run, then schedule (needs the user at each gate)

Operational, no new code. **Each numbered gate needs the user's explicit OK.** If auto mode blocks a deploy or a write against production, hand the user the exact command (memory `deploying-curling-chart`).

- [ ] **Step 1: Final check on the branch**

Run: `cd /home/tcuser/src/curling_score/.claude/worktrees/nightly-review && /home/tcuser/src/curling_score/.venv/bin/pytest tests/test_review.py tests/test_review_script.py tests/test_review_api.py tests/test_flags_script.py tests/test_repo_contract.py tests/test_service_core.py tests/test_service_api.py tests/test_thinking_api.py`

Expected: PASS.

- [ ] **Step 2 (gate 1: merge and deploy the API).** After the user's OK:
  1. In the main checkout, check `git status` for stray edits from this branch's subagents, then merge `nightly-review` into main.
  2. Deploy the API from a clean worktree: `git worktree add --detach <your scratchpad directory>/deploy-review HEAD`, then copy `firebase_api_key.json` in from the main checkout. Before deploying, compare `MODEL_ID` (the script's own one-liner) with the live one.
  3. Run `PROJECT_ID=curling-stats-508323 REGION=us-west1 PUBLIC_BASE_URL=https://curling.dimmit.net ./deploy/deploy-api.sh`, with `~/google-cloud-sdk/bin` on PATH. Check that it prints `accounts: sign-in is live`.
  4. The worker is unchanged.

- [ ] **Step 3 (gate 2: seed the baseline).** After the user's OK, read the 14 days before the lookback without flagging. The last 3 days stay unread, for step 4 and the first real night.

```bash
ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest --secret=curling-admin-token --project=curling-stats-508323)
SINCE=$(date -u -d '17 days ago' +%Y-%m-%dT%H:%M:%SZ)
UNTIL=$(date -u -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)
for i in $(seq 1 20); do
  curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
    "https://curling.dimmit.net/api/admin/review?since=$SINCE&until=$UNTIL&flag=false" \
    | tee -a ~/curling-work/nightly-review/seed-$(date +%F).log | grep -q '"pending":0' && break
done
tail -1 ~/curling-work/nightly-review/seed-$(date +%F).log
```

Expected: the last response shows `"pending":0`, and its `errors` (and earlier ones in the log) list any game that could not be read. Report them.

- [ ] **Step 4: Dry run over the last 3 days, and show the user**

```bash
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://curling.dimmit.net/api/admin/review?dry_run=true&limit=200" \
  > ~/curling-work/nightly-review/dry-run-$(date +%F).json
python3 -c "import json; d = json.load(open('$HOME/curling-work/nightly-review/dry-run-$(date +%F).json')); print(d['reviewed'], 'read,', d['flagged'], 'would flag,', d['pending'], 'left'); [print(w['title'], '|', w['flag']['note']) for w in d['would_flag']]"
```

A dry run writes nothing, so if `pending` > 0 (the 40 s budget) the same call can't page past it. Then run it again with `since` moved past the last game listed, or show the user what it got and say how many were left. Show the user each would-be flag (game, its findings, the per-rock links that `flags.py`'s `describe` would print) and the count per night.

- [ ] **Step 5 (gate 3: schedule it).** Only after the user has seen step 4 and says go:

```bash
cd <the deploy worktree>
PATH=~/google-cloud-sdk/bin:$PATH REVIEW=1 PROJECT_ID=curling-stats-508323 \
  PUBLIC_BASE_URL=https://curling.dimmit.net ADMIN_TOKEN="$ADMIN_TOKEN" ./deploy/scheduler.sh
~/google-cloud-sdk/bin/gcloud scheduler jobs describe curling-nightly-review --project curling-stats-508323 --location us-west1 | grep -E "schedule|state|uri"
```

Expected: `schedule: 0 3-6 * * *`, `state: ENABLED`, and the review URI.

- [ ] **Step 6: The morning after**

Run `python scripts/flags.py list --origin auto` and check the Cloud Run logs for `review: … read` lines. Report the night's flags to the user. Update memory `nightly-review` with what shipped and the first night's count.
