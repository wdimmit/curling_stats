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


def review_game(game: dict, baseline: Baseline) -> GameReview:
    fmt = game_format(game)
    metrics = [end_metrics(e) for e in game.get("ends", [])]
    findings = (_structure(game, fmt) + _coverage(game, metrics, fmt, baseline)
                + _odd_rocks(game))
    return GameReview(fmt, metrics, findings, _notes(game))


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
