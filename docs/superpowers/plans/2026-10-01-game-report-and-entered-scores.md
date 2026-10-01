# Game report redesign, then entered scores: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the game report so it reads at a glance and prints on one page (Phase 1), then let any signed-in user fill in an end's score the wall board didn't read (Phase 2).

**Architecture:**
- Every number the report prints comes from a new framework-free module, `frontend/core/report.mjs`, which is tested in node. `Report.jsx` only lays those numbers out.
- Entered scores are stored on the game (`Source.entered_scores`), the way team names are.
- `timeline.apply_entered_scores` merges them into every served document inside `game_doc`, so all charts and links of a game agree.

**Tech Stack:**
- Frontend: React 19 bundled by esbuild (`npm run build`, which runs eslint first), plain CSS in `src/curling_score/viewer/style.css`, and node for core tests.
- Backend: FastAPI, with Firestore or the in-memory repo, and pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-game-report-and-entered-scores-design.md`. The mockups are the "Game Report Readability" canvas; its sources are in `out/mockups/report/` (gitignored, local).

## Global Constraints

- Percentages use graded rocks only and round as `toFixed(0)` rounds (62.5 → 63). Use `pctOf`, never `Math.round` or Python's `round`.
- Nothing in `frontend/core/` may touch `document`, `window` or React. `tests/test_viewer_js.py` imports it under bare node.
- Firebase is only ever imported on demand, through `import("../site/auth.js")`, never statically. A test enforces this.
- `src/curling_score/viewer/app.js` is committed. Rebuild it with `cd frontend && npm run build` (never esbuild directly) in every task that changes `frontend/`, and commit the bundle plus `frontend/.buildstamp.json`.
- Run Python tests with `.venv/bin/python -m pytest` from the worktree root, and run only the files a task touches. The full suite gets OOM-killed on this box (exit 137).
- Work in the worktree `.claude/worktrees/game-report` on branch `game-report`. Another session shares the main checkout. Never write under `/home/tcuser/src/curling_score` outside the worktree.
- Entered scores fill gaps only. The board's figure always wins. Who entered a score is stored, never shown.
- A score: exactly one team scores, from 1 to the format's `stones_per_team` (8 in fours, 6 in doubles), or 0–0 for a blank end.
- Copy: sentence case, no exclamation marks. Use these exact strings: "Close report", "Sign in to add the score", "entered by hand", "Couldn't save. Try again.", "Sign in again to save", "The board has a score for this end now".

## Review Focus

1. **A 10-end game on a 390 px phone.** The by-end table should scroll sideways inside its card, with the team column pinned, and the page should not scroll sideways. Covered by a CSS test in Task 4 and the headless check in Task 5.
2. **A doubles game.** Positions should read "Player A" and "Player B", and the picker should offer 1–6. Covered in Task 1 (`headToHead`) and Task 9 (`scoreChoices`).
3. **The board later reads an end someone entered** (a reprocess or a live update). The board's figure should be shown, and the entry ignored without an error. Covered by `test_the_board_wins_over_an_entry` in Task 6.
4. **Two people fill different ends at the same moment.** Both scores should survive. Covered by `test_two_ends_are_kept_apart` in Task 7 (repo contract) and Task 8 (API).
5. **Printing from a dark-mode device.** The page should print dark ink on white paper. Covered by `test_print_forces_the_light_tokens` in Task 5.

---

## Setup (once, before Task 1)

- [ ] **Create the worktree**

```bash
cd /home/tcuser/src/curling_score
git worktree add .claude/worktrees/game-report -b game-report main
ln -s /home/tcuser/src/curling_score/frontend/node_modules .claude/worktrees/game-report/frontend/node_modules
cd .claude/worktrees/game-report
mkdir -p tests/fixtures/report
```

Every path below is relative to `/home/tcuser/src/curling_score/.claude/worktrees/game-report`.

---

# Phase 1: the report redesign

### Task 1: `core/report.mjs`: the report's numbers

**Files:**
- Create: `frontend/core/report.mjs`
- Create: `tests/fixtures/report/dimmit_grant.json` (made by `scripts/pare_report_fixture.py`)
- Create: `scripts/pare_report_fixture.py`
- Modify: `frontend/core/index.mjs` (export the module)
- Modify: `tests/js/singleton.mjs` (pass the new exports through)
- Test: `tests/test_viewer_js.py` (new classes at the end)

**Interfaces:**
- Consumes: `buildGameView`, `gatherStats`, `gatherThinking` and `cumulativeThinking` (existing), plus `boardReadable`, `positionText`, `GROUPS`, `TYPE` and `TYPES`.
- Produces, all exported from `frontend/core/index.mjs`:
  - `pctOf(r) -> number|null`
  - `positionLabel(p, fmt) -> string`
  - `endList(numbers) -> string`
  - `byEnd(view) -> {status: "ok"|"predates"|"withheld", boardRead: bool, ends: [{number, hammer, score, entered, shooting:{red,yellow}, thinking:{red,yellow}}], total: {score:{red,yellow}|null, complete: bool, shooting:{red,yellow}, thinking:{red,yellow}}}`
  - `headToHead(stats, fmt) -> {positions:[{id,label,red,yellow}], team:{red,yellow}, types:[{id,label,red,yellow,redN,yellowN}], perPlayer:{graded,thrown}|null, other:[{color,type,thrown}]}`
  - `detailRows(stats, color, fmt) -> {positions:[{id,label}], rows:[{kind:"group"|"type", label, cells:[cell|null], all: cell|null}], all:{cells, all}}`, where `cell = {pct, graded, thrown}`
  - `longestThinks(view, series, n=5) -> [{secs, color, ei, si, end, number, position, type, estimated}]`
  - `coverage(view) -> {graded, thrown, ungradedEnds:[number], first:{ei,si}|null}`
  - `coverageText(cov) -> string|null`
  - `reportNotes(cov, think, endCount) -> string[]`
  - `reportMeta(doc, gi, day?) -> string`
  - `teamNames(game) -> {red, yellow}`

- [ ] **Step 1: Write the fixture script and make the fixture**

`scripts/pare_report_fixture.py`:

```python
#!/usr/bin/env python
"""Pare a served timeline down to what the game report reads, for a test fixture.

    python scripts/pare_report_fixture.py timeline.json overrides.json out.json

Keeps the first game's ends, scores, hammer, scoreboard and thinking, and each
rock's colour, position, type, grade and clock -- and drops the tracks, lines
and stones that make a real timeline 300 KB. The overrides ride along
unchanged, so the fixture is the game exactly as its charter graded it.
"""
import json
import sys

SHOT = ("number", "id", "color", "color_inferred", "position", "rock_of_player", "thrower_slot",
        "has_hammer", "label", "shot_type", "shot_type_source", "thinking_time_s",
        "t_tee_estimated", "missing", "state_known", "user_score", "t_guess_s")
END = ("number", "id", "house", "start_s", "end_s", "hammer", "score", "score_source",
       "running", "thinking_time", "unplaced_shots", "shots_expected", "splits_measured",
       "detected_score")
GAME = ("index", "start_s", "end_s", "teams", "final", "scoreboard", "hammer_consistent",
        "thinking_time", "detected")


def pare(doc: dict) -> dict:
    g = doc["games"][0]
    game = {k: g[k] for k in GAME if k in g}
    game["ends"] = []
    for e in g["ends"]:
        end = {k: e[k] for k in END if k in e}
        end["shots"] = [{**{k: s[k] for k in SHOT if k in s}, "stones": []} for s in e["shots"]]
        game["ends"].append(end)
    chart = doc.get("chart") or {}
    return {"schema_version": doc["schema_version"],
            "source": {"video_id": doc["source"]["video_id"], "sheet": doc["source"]["sheet"]},
            "chart": {"league": chart.get("league"), "title": chart.get("title")},
            "games": [game]}


def main() -> None:
    timeline, overrides, out = sys.argv[1:4]
    doc = pare(json.load(open(timeline)))
    fixture = {"doc": doc, "overrides": json.load(open(overrides))}
    with open(out, "w") as f:
        json.dump(fixture, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
```

Run it. The two inputs are the live chart's own files, saved while making the mockups:

```bash
python3 scripts/pare_report_fixture.py \
  /home/tcuser/src/curling_score/out/mockups/report/timeline.json \
  /home/tcuser/src/curling_score/out/mockups/report/overrides.json \
  tests/fixtures/report/dimmit_grant.json
ls -la tests/fixtures/report/dimmit_grant.json   # about 38 KB
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_viewer_js.py`:

```python
REPORT_FIXTURE = Path(__file__).resolve().parent / "fixtures/report/dimmit_grant.json"


def report_js(body: str, document=None, overrides=None):
    """Run ``body`` with ``view``, ``stats``, ``think`` and ``series`` built from
    Dimmit v Grant (2026-09-29, sheet 3) as its charter graded it -- or from
    ``document`` when one is given."""
    if document is None:
        fx = json.loads(REPORT_FIXTURE.read_text())
        document, overrides = fx["doc"], fx["overrides"]
    return run_js(
        f"const view = buildGameView({json.dumps(document)}, 0, {json.dumps(overrides or {})});\n"
        "const stats = gatherStats(view);\n"
        "const think = gatherThinkingOf(view);\n"
        "const series = cumulativeThinkingOf(view);\n" + body)


class TestTheReportByEnd:
    """The table at the top: the score, the hammer, and each team's
    shooting and clock, end by end."""

    def test_the_ends_and_their_hammer(self):
        got = report_js("out(byEnd(view).ends.map(e => [e.number, e.hammer]));")
        assert got == [[1, "yellow"], [2, "yellow"], [3, "red"], [4, "yellow"]]

    def test_an_end_the_board_never_read_has_no_score(self):
        got = report_js("out(byEnd(view).ends.map(e => e.score));")
        assert got == [{"red": 1, "yellow": 0}, {"red": 0, "yellow": 2},
                       {"red": 3, "yellow": 0}, None]

    def test_an_ungraded_end_has_no_shooting(self):
        got = report_js("out(byEnd(view).ends.map(e => e.shooting));")
        assert got == [{"red": None, "yellow": None}, {"red": 63, "yellow": 75},
                       {"red": 78, "yellow": 41}, {"red": 44, "yellow": 94}]

    def test_the_totals(self):
        got = report_js("out(byEnd(view).total);")
        assert got["score"] == {"red": 4, "yellow": 2}
        assert got["complete"] is False
        assert got["shooting"] == {"red": 61, "yellow": 70}
        assert round(got["thinking"]["red"]) == 653 and round(got["thinking"]["yellow"]) == 789

    def test_the_status_is_ok_and_the_board_was_read(self):
        got = report_js("const t = byEnd(view); out([t.status, t.boardRead]);")
        assert got == ["ok", True]

    def test_an_old_chart_says_it_predates_board_reading(self):
        d = doc([shot(1, "red", "lead")])
        d["schema_version"] = 3
        got = report_js("const t = byEnd(view); out([t.status, t.ends[0].score]);", d)
        assert got == ["predates", None]

    def test_a_withheld_board_says_so(self):
        d = doc([shot(1, "red", "lead")])
        d["games"][0]["scoreboard"] = {"scores_withheld": "could not place"}
        assert report_js("out(byEnd(view).status);", d) == "withheld"

    def test_a_ten_end_game_totals_every_end(self):
        d = doc([shot(1, "red", "lead", user_score=4)])
        first = d["games"][0]["ends"][0]
        d["games"][0]["ends"] = [{**first, "number": n, "score": {"red": n % 2, "yellow": 0},
                                  "shots": [dict(s) for s in first["shots"]]}
                                 for n in range(1, 11)]
        got = report_js("const t = byEnd(view); out([t.ends.length, t.total]);", d)
        assert got[0] == 10
        assert got[1]["score"] == {"red": 5, "yellow": 0} and got[1]["complete"] is True


class TestTheReportHeadToHead:
    def test_by_position(self):
        got = report_js("out(headToHead(stats, view.format).positions"
                        "  .map(p => [p.label, p.red, p.yellow]));")
        assert got == [["Lead", 63, 83], ["Second", 79, 63], ["Third", 54, 71], ["Skip", 50, 63]]

    def test_the_team(self):
        assert report_js("out(headToHead(stats, view.format).team);") == {"red": 61, "yellow": 70}

    def test_by_shot_type_with_the_rocks_behind_each(self):
        got = report_js("out(headToHead(stats, view.format).types"
                        "  .map(t => [t.label, t.red, t.yellow, t.redN, t.yellowN]));")
        assert got == [["Draw", 52, 68, 11, 15], ["Guard", 63, 100, 2, 2], ["Hit", 70, 58, 11, 6]]

    def test_every_player_graded_alike_is_said_once(self):
        got = report_js("out(headToHead(stats, view.format).perPlayer);")
        assert got == {"graded": 6, "thrown": 8}

    def test_the_other_rocks_are_listed(self):
        got = report_js("out(headToHead(stats, view.format).other);")
        assert got == [{"color": "red", "type": "Unknown", "thrown": 2},
                       {"color": "yellow", "type": "Unknown", "thrown": 2},
                       {"color": "yellow", "type": "Not thrown", "thrown": 1}]

    def test_doubles_reads_player_a_and_b(self):
        got = report_js("out(headToHead(stats, view.format).positions.map(p => p.label));",
                        doubles_doc(doubles_shots()))
        assert got == ["Player A", "Player B"]

    def test_nothing_graded_reads_as_no_percentage(self):
        got = report_js("out(headToHead(stats, view.format).positions[0]);",
                        doc([shot(1, "red", "lead")]))
        assert got == {"id": "lead", "label": "Lead", "red": None, "yellow": None}


class TestTheReportDetail:
    def cells(self, color):
        return report_js(
            f"out(detailRows(stats, {color!r}, view.format).rows.map(r => [r.kind, r.label,"
            "  r.cells.map(c => c && `${c.pct}:${c.graded}/${c.thrown}`),"
            "  r.all && `${r.all.pct}:${r.all.graded}/${r.all.thrown}`]));")

    def test_a_group_with_one_type_has_no_row_under_it(self):
        labels = [r[1] for r in self.cells("red")]
        assert labels == ["Draw", "Guard", "Guard", "Centre guard", "Hit", "Hit",
                          "Hit & stick", "Hit & roll", "Peel", "Run back", "Flashed", "Other"]

    def test_the_third_draws(self):
        draw = self.cells("red")[0]
        assert draw == ["group", "Draw", ["67:3/3", "50:2/2", "33:3/5", "58:3/3"], "52:11/13"]

    def test_nothing_of_a_kind_is_an_empty_cell(self):
        guard = self.cells("red")[1]
        assert guard[2] == ["63:2/2", None, None, None]

    def test_nothing_graded_has_no_percentage_but_keeps_its_count(self):
        other = self.cells("red")[-1]
        assert other[2][0] == "null:0/2"

    def test_the_all_row(self):
        got = report_js("out(detailRows(stats, 'red', view.format).all.all);")
        assert got == {"pct": 61, "graded": 24, "thrown": 32}

    def test_yellow_lists_its_own_types(self):
        labels = [r[1] for r in self.cells("yellow")]
        assert labels == ["Draw", "Draw", "Freeze", "Tap up", "Guard", "Centre guard",
                          "Corner guard", "Hit", "Hit", "Hit & stick", "Hit & roll", "Peel",
                          "Flashed", "Other", "Not thrown", "Unknown"]


class TestTheReportClockAndCoverage:
    def test_the_longest_thinks(self):
        got = report_js("out(longestThinks(view, series).map(l =>"
                        "  [clockText(l.secs), l.color, l.position, l.end, l.number, l.type]));")
        assert got[:2] == [["1:28", "yellow", "skip", 1, 14, "Hit"],
                           ["1:18", "yellow", "skip", 3, 13, "Draw"]]
        assert len(got) == 5

    def test_the_median(self):
        assert report_js("out(clockText(series.median));") == "0:21"

    def test_coverage(self):
        got = report_js("out(coverage(view));")
        assert got == {"graded": 48, "thrown": 64, "ungradedEnds": [1],
                       "first": {"ei": 0, "si": 0}}

    def test_the_pill_says_what_is_left(self):
        got = report_js("out(coverageText(coverage(view)));")
        assert got == "48 of 64 rocks graded · end 1 still to grade"

    def test_the_pill_goes_once_every_rock_is_graded(self):
        got = report_js("out(coverageText(coverage(view)));",
                        doc([shot(1, "red", "lead", user_score=3)]))
        assert got is None

    def test_nothing_graded_says_so(self):
        got = report_js("out(coverageText(coverage(view)));", doc([shot(1, "red", "lead")]))
        assert got == "No rocks graded yet"

    def test_the_notes(self):
        got = report_js("out(reportNotes(coverage(view), think, view.ends.length));")
        assert got == [
            "Percentages come from graded rocks only: 48 of 64. End 1 hasn’t been graded. "
            "A rock nobody graded counts as thrown, never as a miss.",
            "Thinking time is read for 56 of 64 rocks. An end’s first rock has nothing to "
            "time from, and the camera missed 4 more. 1 is estimated (outlined). Treat the "
            "totals as lower bounds."]

    def test_no_clock_means_no_clock_note(self):
        got = report_js("out(reportNotes(coverage(view), think, view.ends.length).length);",
                        doc([shot(1, "red", "lead")]))
        assert got == 1


class TestTheReportWords:
    def test_end_lists(self):
        assert run_js("out([endList([1]), endList([1, 3]), endList([1, 2, 4])]);") == [
            "end 1", "ends 1 and 3", "ends 1, 2 and 4"]

    def test_the_meta_line(self):
        got = run_js("out(reportMeta({chart: {league: 'Tuesday Super League 2026-2027',"
                     " played_at: '2026-09-30T02:00:00+00:00'}, source: {sheet: 3},"
                     " games: [{}]}, 0, d => d.toISOString().slice(0, 10)));")
        assert got == "Tuesday Super League 2026-2027 · Sheet 3 · 2026-09-30 · Game report"

    def test_a_recording_of_two_games_says_which(self):
        got = run_js("out(reportMeta({chart: {}, source: {}, games: [{}, {}]}, 1));")
        assert got == "Game 2 of 2 · Game report"

    def test_team_names_fall_back_to_the_colours(self):
        got = run_js("out(teamNames({teams: {red: {name: 'Dimmit'}, yellow: {name: null}}}));")
        assert got == {"red": "Dimmit", "yellow": "Yellow"}
```

- [ ] **Step 3: Pass the exports through the test adapter**

In `tests/js/singleton.mjs`, after the line `export const endSpan = core.endSpan;`, add:

```js
/* The report's numbers, by the view they are given -- not state's. */
export const byEnd = core.byEnd;
export const headToHead = core.headToHead;
export const detailRows = core.detailRows;
export const longestThinks = core.longestThinks;
export const coverage = core.coverage;
export const coverageText = core.coverageText;
export const reportNotes = core.reportNotes;
export const reportMeta = core.reportMeta;
export const teamNames = core.teamNames;
export const endList = core.endList;
export const pctOf = core.pctOf;
export const positionLabel = core.positionLabel;
export const gatherThinkingOf = core.gatherThinking;
export const cumulativeThinkingOf = core.cumulativeThinking;
```

- [ ] **Step 4: Run the tests to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheReport" -x`
Expected: FAIL. node exits non-zero because `core.byEnd` is undefined (`TypeError: byEnd is not a function`).

- [ ] **Step 5: Write `frontend/core/report.mjs`**

```js
/* What the game report prints, worked out from the game view.
 *
 * Framework-free like stats.mjs, so tests/test_viewer_js.py runs it under
 * node. viewer/Report.jsx only lays these numbers out: every percentage,
 * total and sentence on the page is decided here, where it can be tested
 * against a real game rather than eyeballed in one. See
 * docs/superpowers/specs/2026-10-01-game-report-and-entered-scores-design.md.
 */
import { GROUPS, TYPE, TYPES } from "./constants.mjs";
import { FOURS, positionText } from "./format.mjs";
import { isGraded } from "./shots.mjs";
import { boardReadable } from "./wire.mjs";

const COLORS = ["red", "yellow"];
const zero = () => ({ thrown: 0, graded: 0, sum: 0 });
const addShot = (r, s) => {
  r.thrown++;
  if (isGraded(s)) { r.graded++; r.sum += s.user_score; }
};
const addInto = (r, x) => {
  if (x) { r.thrown += x.thrown; r.graded += x.graded; r.sum += x.sum; }
  return r;
};
// The rocks gatherStats counts: a colour it knows and a position.
const counted = s => COLORS.includes(s?.color) && !!s.position;

/* pct()'s number: graded rocks only, rounded as toFixed(0) rounds, so the
 * report and every older surface can never disagree by one. */
export const pctOf = r =>
  (r?.graded ? Number((100 * r.sum / (4 * r.graded)).toFixed(0)) : null);

/* "Lead", or "Player A" where a team is two players. */
export function positionLabel(p, fmt = FOURS) {
  const t = positionText(p, fmt) || "";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/* "end 1", "ends 1 and 3", "ends 1, 2 and 4". */
export function endList(numbers) {
  if (!numbers.length) return "";
  if (numbers.length === 1) return `end ${numbers[0]}`;
  return `ends ${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
}

/* Who played, or the colour they threw when nobody has said. */
export function teamNames(game) {
  return { red: game?.teams?.red?.name || "Red", yellow: game?.teams?.yellow?.name || "Yellow" };
}

/* The line over the title: league, sheet, the day it was played, and the
 * game when a recording holds more than one. `day` formats the date, so
 * the rest stays testable without a locale. */
export function reportMeta(doc, gi, day = d => d.toLocaleDateString(undefined,
  { weekday: "short", day: "numeric", month: "short", year: "numeric" })) {
  const parts = [];
  if (doc?.chart?.league) parts.push(doc.chart.league);
  if (doc?.source?.sheet != null) parts.push(`Sheet ${doc.source.sheet}`);
  const at = doc?.chart?.played_at ? new Date(doc.chart.played_at) : null;
  if (at && !Number.isNaN(at.getTime())) parts.push(day(at));
  if ((doc?.games?.length || 0) > 1) parts.push(`Game ${gi + 1} of ${doc.games.length}`);
  parts.push("Game report");
  return parts.join(" · ");
}

/* The by-end table: per end, the score, who had the hammer, and each team's
 * shooting and thinking, plus the totals.
 *
 * `status` says whether there is a score to show at all: "predates" for a
 * chart from before board reading (its ends hold the detector's guesses,
 * which nothing shows), "withheld" for a board read but not placed, "ok"
 * otherwise. A score of null is an end the board never gave one. The score
 * total adds the ends that have one; `complete` says whether that is all. */
export function byEnd(view) {
  const readable = boardReadable(view.doc);
  const withheld = readable && !!view.game.scoreboard?.scores_withheld;
  const team = { red: zero(), yellow: zero() };
  const clock = { red: 0, yellow: 0 };
  const ends = view.ends.map(({ end, shots }) => {
    const shooting = { red: zero(), yellow: zero() };
    for (const s of shots) {
      if (!counted(s)) continue;
      addShot(shooting[s.color], s);
      addShot(team[s.color], s);
    }
    const t = end.thinking_time || {};
    for (const c of COLORS) clock[c] += t[c] || 0;
    return {
      number: end.number,
      hammer: end.hammer || null,
      score: readable ? (end.score ?? null) : null,
      entered: readable && end.score_source === "entered",
      shooting: { red: pctOf(shooting.red), yellow: pctOf(shooting.yellow) },
      thinking: { red: t.red ?? null, yellow: t.yellow ?? null },
    };
  });
  const known = ends.filter(e => e.score);
  return {
    status: !readable ? "predates" : withheld ? "withheld" : "ok",
    boardRead: !!view.game.scoreboard,
    ends,
    total: {
      score: known.length
        ? Object.fromEntries(COLORS.map(c => [c, known.reduce((n, e) => n + (e.score[c] || 0), 0)]))
        : null,
      complete: ends.length > 0 && known.length === ends.length,
      shooting: { red: pctOf(team.red), yellow: pctOf(team.yellow) },
      thinking: clock,
    },
  };
}

/* The positions a team is reported under: the format's, plus any other a
 * rock was thrown from (gatherStats keeps those in their own bucket). */
const positionsOf = (stats, fmt) => {
  const extra = COLORS.flatMap(c => Object.keys(stats[c]))
    .filter(p => !fmt.positions.includes(p) && COLORS.some(c => stats[c][p]?.thrown));
  return [...fmt.positions, ...new Set(extra)];
};

const groupOf = id => TYPE[id]?.group || "Other";
const groupTotal = (bucket, group) => Object.entries(bucket?.types || {})
  .filter(([id]) => groupOf(id) === group)
  .reduce((r, [, x]) => addInto(r, x), zero());

/* The two teams head to head: by position, the team, and by shot type. */
export function headToHead(stats, fmt = FOURS) {
  const positions = positionsOf(stats, fmt);
  const teamOf = c => positions.reduce((r, p) => addInto(r, stats[c][p]), zero());
  const graded = new Set(), thrown = new Set();
  for (const c of COLORS) for (const p of fmt.positions) {
    graded.add(stats[c][p]?.graded ?? 0);
    thrown.add(stats[c][p]?.thrown ?? 0);
  }
  const types = ["Draw", "Guard", "Hit"].map(g => {
    const r = Object.fromEntries(COLORS.map(c =>
      [c, positions.reduce((t, p) => addInto(t, groupTotal(stats[c][p], g)), zero())]));
    return { id: g, label: g, red: pctOf(r.red), yellow: pctOf(r.yellow),
             redN: r.red.graded, yellowN: r.yellow.graded };
  });
  const other = [];
  for (const c of COLORS) {
    const byType = {};
    for (const p of positions)
      for (const [id, x] of Object.entries(stats[c][p]?.types || {}))
        if (groupOf(id) === "Other") byType[id] = (byType[id] || 0) + x.thrown;
    for (const [id, n] of Object.entries(byType))
      other.push({ color: c, type: TYPE[id]?.name || id, thrown: n });
  }
  return {
    positions: positions.map(p => ({ id: p, label: positionLabel(p, fmt),
                                     red: pctOf(stats.red[p]), yellow: pctOf(stats.yellow[p]) })),
    team: { red: pctOf(teamOf("red")), yellow: pctOf(teamOf("yellow")) },
    types,
    perPlayer: graded.size === 1 && thrown.size === 1
      ? { graded: [...graded][0], thrown: [...thrown][0] } : null,
    other,
  };
}

/* One team's position-by-type table. A group row always; a row per type
 * only where the group holds more than one, so "Draw" never sits over a
 * lone "Draw". A cell is null where nothing of that kind was thrown. */
export function detailRows(stats, color, fmt = FOURS) {
  const team = stats[color];
  const positions = positionsOf(stats, fmt);
  const cell = r => (r && r.thrown ? { pct: pctOf(r), graded: r.graded, thrown: r.thrown } : null);
  const thrownIds = new Set(positions.flatMap(p => Object.keys(team[p]?.types || {})));
  const sum = (list, p) => list.reduce((r, id) => addInto(r, team[p]?.types[id]), zero());
  const across = list => positions.reduce((r, p) => addInto(r, sum(list, p)), zero());
  const rows = [];
  for (const group of GROUPS) {
    const ids = TYPES.filter(t => t.group === group && thrownIds.has(t.id)).map(t => t.id);
    if (group === "Other") ids.push(...[...thrownIds].filter(id => !TYPE[id]));
    if (!ids.length) continue;
    rows.push({ kind: "group", label: group,
                cells: positions.map(p => cell(sum(ids, p))), all: cell(across(ids)) });
    if (ids.length > 1)
      for (const id of ids)
        rows.push({ kind: "type", label: TYPE[id]?.name || id,
                    cells: positions.map(p => cell(team[p]?.types[id])), all: cell(across([id])) });
  }
  return {
    positions: positions.map(p => ({ id: p, label: positionLabel(p, fmt) })),
    rows,
    all: { cells: positions.map(p => cell(team[p])),
           all: cell(positions.reduce((r, p) => addInto(r, team[p]), zero())) },
  };
}

/* The longest thinks, longest first, with what the report says of each. */
export function longestThinks(view, series, n = 5) {
  return series.points
    .filter(p => p.secs != null)
    .sort((a, b) => b.secs - a.secs)
    .slice(0, n)
    .map(p => {
      const s = view.ends[p.ei]?.shots[p.si];
      return { secs: p.secs, color: p.color, ei: p.ei, si: p.si, end: p.end,
               number: s?.number ?? null, position: s?.position ?? null,
               type: TYPE[s?.shot_type]?.name ?? null, estimated: !!p.estimated };
    });
}

/* How much of the game is graded, which ends have nothing graded, and the
 * first rock still to grade -- where the pill takes you. */
export function coverage(view) {
  let graded = 0, thrown = 0, first = null;
  const ungradedEnds = [];
  view.ends.forEach(({ end, shots }, ei) => {
    let any = false, some = false;
    shots.forEach((s, si) => {
      if (!counted(s)) return;
      some = true;
      thrown++;
      if (isGraded(s)) { graded++; any = true; } else if (!first) first = { ei, si };
    });
    if (some && !any) ungradedEnds.push(end.number);
  });
  return { graded, thrown, ungradedEnds, first };
}

/* The pill's words, or null once every rock is graded. */
export function coverageText(cov) {
  if (!cov.thrown || cov.graded === cov.thrown) return null;
  if (!cov.graded) return "No rocks graded yet";
  const left = cov.ungradedEnds.length ? ` · ${endList(cov.ungradedEnds)} still to grade` : "";
  return `${cov.graded} of ${cov.thrown} rocks graded${left}`;
}

/* "About these numbers": what the percentages and the clock are read from. */
export function reportNotes(cov, think, endCount) {
  const notes = [];
  if (cov.thrown && cov.graded === cov.thrown) {
    notes.push("Every rock is graded.");
  } else {
    const ends = cov.ungradedEnds;
    const which = ends.length
      ? `. ${endList(ends).replace(/^e/, "E")} ${ends.length === 1 ? "hasn’t" : "haven’t"} been graded`
      : "";
    notes.push(`Percentages come from graded rocks only: ${cov.graded} of ${cov.thrown}${which}. `
             + "A rock nobody graded counts as thrown, never as a miss.");
  }
  if (think.measured) {
    const missed = think.unmeasured - endCount;
    notes.push(`Thinking time is read for ${think.measured} of ${think.measured + think.unmeasured} rocks. `
             + "An end’s first rock has nothing to time from"
             + (missed > 0 ? `, and the camera missed ${missed} more` : "") + "."
             + (think.estimated
               ? ` ${think.estimated} ${think.estimated === 1 ? "is" : "are"} estimated (outlined).`
               : "")
             + " Treat the totals as lower bounds.");
  }
  return notes;
}
```

In `frontend/core/index.mjs`, add after `export * from "./stats.mjs";`:

```js
export * from "./report.mjs";
```

- [ ] **Step 6: Run the tests to make sure they pass**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheReport or ReportArithmetic or DoublesStats"`
Expected: PASS. All the new classes pass, and the old report arithmetic tests still do.

- [ ] **Step 7: Commit**

```bash
git add frontend/core/report.mjs frontend/core/index.mjs tests/js/singleton.mjs \
        tests/test_viewer_js.py tests/fixtures/report/dimmit_grant.json scripts/pare_report_fixture.py
git commit -m "report: every number the game report prints, worked out in core

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(No bundle rebuild yet: nothing in `viewer/` imports the module, so `app.js` is unchanged. Check with `cd frontend && npm run check`. If it says stale, run `npm run build` and add `src/curling_score/viewer/app.js frontend/.buildstamp.json`.)

---

### Task 2: Chart labels: each line's total and the longest bars

**Files:**
- Modify: `frontend/core/charts.mjs` (bars carry `secs`; add `lineEnds` and `barLabels`)
- Modify: `tests/js/singleton.mjs`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `chartGeometry`, `barsGeometry` and `clockText`.
- Produces:
  - `lineEnds(geom, series, gap=18) -> [{color, x, y, total}]`
  - `barLabels(geom, n=3, apart=4) -> [{shot, x, y, text}]`
  - `barsGeometry(...).bars[i].secs`

- [ ] **Step 1: Write the failing tests**

```python
class TestTheReportChartLabels:
    BOX = "{ w: 868, h: 200, padL: 52, padR: 128, padT: 22, padB: 30 }"

    def test_the_three_longest_bars_skip_a_neighbour(self):
        """End 3's rocks 13 and 15 are two apart: one label, not two piled up."""
        got = report_js(f"out(barLabels(barsGeometry(series, null, {self.BOX}))"
                        "  .map(l => [l.shot, l.text]));")
        assert got == [[14, "1:28"], [45, "1:18"], [63, "1:17"]]

    def test_each_line_is_labelled_with_its_total(self):
        got = report_js(f"out(lineEnds(chartGeometry(series, null, {{...{self.BOX}, h: 240,"
                        " padT: 12}), series).map(e => [e.color, clockText(e.total)]));")
        assert got == [["yellow", "13:09"], ["red", "10:53"]]

    def test_two_totals_that_would_overlap_are_pushed_apart(self):
        got = run_js("const g = {lines: [{color: 'yellow', points: [[0, 0], [100, 50]]},"
                     " {color: 'red', points: [[0, 0], [100, 55]]}]};"
                     "out(lineEnds(g, {red: 600, yellow: 590}).map(e => [e.color, e.y]));")
        assert got == [["yellow", 61.5], ["red", 43.5]]

    def test_a_bar_knows_its_seconds(self):
        got = report_js(f"out(barsGeometry(series, null, {self.BOX}).bars[0].secs);")
        assert isinstance(got, (int, float))
```

Add to `tests/js/singleton.mjs` after the report exports:

```js
export const lineEnds = core.lineEnds;
export const barLabels = core.barLabels;
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheReportChartLabels -x`
Expected: FAIL with `barLabels is not a function`.

- [ ] **Step 3: Implement**

In `frontend/core/charts.mjs`, inside `barsGeometry`'s `bars: thrown.map(p => ({`, change the first line of the object to:

```js
      shot: p.i, ei: p.ei, si: p.si, secs: p.secs, color: p.color || "", est: !!p.estimated,
```

Append to the file:

```js
/* Each team's total at the end of its line, to label it there. Two that
 * would overlap are pushed apart, the larger total kept above. */
export function lineEnds(geom, series, gap = 18) {
  if (!geom) return [];
  const out = geom.lines.map(l => {
    const [x, y] = l.points[l.points.length - 1];
    return { color: l.color, x, y, total: series[l.color] };
  });
  const [a, b] = out;
  if (a && b && Math.abs(a.y - b.y) < gap) {
    const mid = (a.y + b.y) / 2;
    const [hi, lo] = a.total >= b.total ? [a, b] : [b, a];
    hi.y = mid - gap / 2;
    lo.y = mid + gap / 2;
  }
  return out;
}

/* The longest few bars, to print their time over them. A bar within
 * `apart` rocks of one already labelled is passed over, or the two labels
 * would print on top of each other. */
export function barLabels(geom, n = 3, apart = 4) {
  if (!geom) return [];
  const picked = [];
  for (const b of [...geom.bars].sort((p, q) => q.secs - p.secs)) {
    if (picked.length === n) break;
    if (picked.some(p => Math.abs(p.shot - b.shot) < apart)) continue;
    picked.push(b);
  }
  return picked.map(b => ({ shot: b.shot, x: b.x + b.w / 2, y: b.y, text: clockText(b.secs) }));
}
```

- [ ] **Step 4: Run the chart tests**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "Clock or Bars or ChartLabels or BarIsSomething"`
Expected: PASS.

- [ ] **Step 5: Rebuild and commit**

`charts.mjs` is in the bundle, so rebuild:

```bash
(cd frontend && npm run build)
git add frontend/core/charts.mjs tests/js/singleton.mjs tests/test_viewer_js.py \
        src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "charts: label each line's total and the longest bars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The chart document says when the game was played, and which game it is

**Files:**
- Modify: `src/curling_score/service/api.py`, in `chart_doc` (around line 584) and `review_timeline` (around line 1572)
- Test: `tests/test_service_api.py` (new class at the end)

**Interfaces:**
- Produces: `doc.chart.played_at` (ISO string or null) and `doc.chart.source_id` (string or null) on `/c/`, `/s/` and `/g/` timelines.

- [ ] **Step 1: Write the failing test**

```python
class TestTheChartSaysWhichGameAndWhen:
    """The report's title line needs the day; entering a score needs the game."""

    def _ready(self, world):
        slug = submit(world).json()["slug"]
        work_through(world, games=1)
        src = world["repo"].get_chart(slug).source_id
        return slug, src

    def test_the_edit_and_view_links_carry_both(self, world):
        slug, src = self._ready(world)
        share = world["repo"].get_chart(slug).share_slug
        played = world["repo"].get_source(src).played_at.isoformat(timespec="seconds")
        for path in (f"/c/{slug}/timeline.json", f"/s/{share}/timeline.json"):
            chart = world["client"].get(path).json()["chart"]
            assert chart["source_id"] == src
            assert chart["played_at"] == played

    def test_the_review_link_carries_both(self, world):
        _slug, src = self._ready(world)
        chart = world["client"].get(f"/g/{src}/timeline.json").json()["chart"]
        assert chart["source_id"] == src
        assert chart["played_at"] is not None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/python -m pytest tests/test_service_api.py -k TheChartSaysWhichGameAndWhen`
Expected: FAIL with `KeyError: 'source_id'`.

- [ ] **Step 3: Implement**

In `chart_doc`, add two keys to the `doc["chart"] = {...}` literal, after `"league": run.league,`:

```python
            # The day it was played, for the report's title, and the game it
            # is, which an entered score is sent to. Neither is secret: the
            # catalogue lists both.
            "played_at": _iso(src.played_at if src else run.published_at),
            "source_id": chart.source_id,
```

In `review_timeline`, change the `doc["chart"] = {...}` literal to:

```python
        doc["chart"] = {"read_only": True, "review": True,
                        "title": run.title, "league": run.league,
                        "played_at": _iso(src.played_at or run.published_at),
                        "source_id": src.id,
                        "ends_trimmed": timeline.ends_trimmed(doc)}
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_service_api.py tests/test_review_mode.py -k "Chart or review"`
Expected: PASS. If `tests/test_review_mode.py` does not exist in this checkout, drop it from the command.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py tests/test_service_api.py
git commit -m "api: a chart's timeline says which game it is and when it was played

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The redesigned report on screen

**Files:**
- Rewrite: `frontend/viewer/Report.jsx`
- Modify: `frontend/viewer/Charts.jsx` (add `ReportClock`)
- Modify: `frontend/viewer/App.jsx` (the Report button's label)
- Modify: `src/curling_score/viewer/index.html` (font link)
- Modify: `src/curling_score/viewer/style.css`:
  - the tokens;
  - replace the `/* --- report --- */` block (lines ~249–254) and the `.clockcard` / `td.sub` / `tr.total` rules (lines ~310 and ~324–333);
  - the phone `h1, #src` rule (line ~463).
- Test: `tests/test_viewer_js.py` (source and stylesheet tests)

**Interfaces:**
- Consumes: everything Task 1 produces, plus `lineEnds` and `barLabels` (Task 2) and `doc.chart.played_at` (Task 3).
- Produces:
  - `Report({ view, stats, think, series, actions })` (Task 9 adds `config`);
  - `ReportClock({ series, names, onSelect })` and `Dot({ c, size })`, both exported from `Charts.jsx`;
  - the CSS classes `.rpt`, `.rpt-byend`, `.duel`, `.rpt-matrix`, `.rclock` and `.screenonly`, which Task 5 styles for print and Task 9 extends.

- [ ] **Step 1: Write the failing source and stylesheet tests**

```python
class TestTheReportPage:
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_the_report_holds_no_arithmetic(self):
        """Every number comes from core/report.mjs, where it is tested."""
        report = self.src("frontend/viewer/Report.jsx")
        for name in ("byEnd(", "headToHead(", "detailRows(", "coverage(", "reportNotes("):
            assert name in report
        assert "/ (4 *" not in report and "toFixed" not in report

    def test_the_button_says_close_while_open(self):
        app = self.src("frontend/viewer/App.jsx")
        assert '{ui.reporting ? "Close report" : "Report"}' in app

    def test_the_viewer_loads_source_sans(self):
        html = self.src("src/curling_score/viewer/index.html")
        assert "family=Source+Sans+3" in html
        assert html.index("fonts.googleapis.com") < html.index('href="style.css"')

    def test_the_phone_hides_only_the_headers_title(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "header > h1, #src { display: none; }" in css
        assert "\n  h1, #src { display: none; }" not in css

    def test_a_long_game_scrolls_inside_its_card(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert ".rpt-scroll { overflow-x:auto; }" in css
        assert "position:sticky; left:0;" in css[css.index(".rpt-byend th[scope=row]"):]

    def test_team_colours_are_drawn_not_painted_behind(self):
        """CSS backgrounds vanish when printed; SVG fills do not."""
        report = self.src("frontend/viewer/Report.jsx")
        assert "className=\"swatch\"" not in report
        assert "<Dot " in report
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheReportPage`
Expected: FAIL on every test.

- [ ] **Step 3: Add the fonts**

In `src/curling_score/viewer/index.html`, add this line before `<link rel="stylesheet" href="style.css">`:

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@500;600;700&amp;family=Source+Sans+3:wght@400;600;700&amp;display=swap">
```

- [ ] **Step 4: Add `ReportClock` and `Dot` to `frontend/viewer/Charts.jsx`**

Change the imports at the top to:

```jsx
import { Fragment, useLayoutEffect, useRef, useState } from "react";
import {
  barLabels, barsGeometry, chartGeometry, clockText, endSpan, lineEnds,
} from "../core/index.mjs";
import { CHARTBOX } from "../core/constants.mjs";
```

Append:

```jsx
/* A team's colour, drawn rather than painted: a CSS background is dropped
 * when the page is printed, an SVG fill is not. */
export function Dot({ c, size = 12 }) {
  return (
    <svg className={`rpt-dot ${c}`} width={size} height={size} viewBox="0 0 12 12"
         aria-hidden="true">
      <circle cx="6" cy="6" r="5.5" />
    </svg>
  );
}

/* The width the element is drawn at, followed as it changes. */
function useWidth(ref, fallback) {
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      const x = Math.round(el.getBoundingClientRect().width);
      if (x > 0) setW(x);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

function ReportGrid({ geom, words }) {
  const { box } = geom;
  return (
    <>
      {geom.grid.map(g => (
        <Fragment key={g.v}>
          <line className="g" x1={box.padL} x2={box.w - box.padR} y1={n(g.y)} y2={n(g.y)} />
          <text className="yl" x={box.padL - 8} y={n(g.y + 4)}>{g.label}</text>
        </Fragment>
      ))}
      {geom.ticks.map((t, i) => (
        <line key={i} className="b" x1={n(t.x)} x2={n(t.x)} y1={t.y1} y2={t.y2} />
      ))}
      {geom.endLabels.map(l => (
        <text key={l.number} className="xl" x={n(l.x)} y={l.y}>
          {words ? `End ${l.number}` : l.number}
        </text>
      ))}
    </>
  );
}

/* The report's clock: both charts drawn at the card's own width, so a label
 * stays 13 px whatever the screen -- the sidebar's scale with their box
 * instead. Narrow (a phone), the totals move under the chart, out of the
 * margin they would otherwise need. The bars are screen-only: print keeps
 * the running totals. */
export function ReportClock({ series, names, onSelect }) {
  const ref = useRef(null);
  const width = useWidth(ref, 860);
  const roomy = width >= 600;
  const lineBox = { w: width, h: roomy ? 240 : 190, padL: roomy ? 52 : 40,
                    padR: roomy ? 128 : 6, padT: 12, padB: 30 };
  const barBox = { ...lineBox, h: roomy ? 200 : 160, padT: 22 };
  const lines = chartGeometry(series, null, lineBox);
  const bars = barsGeometry(series, null, barBox);
  const words = series.bounds.length <= 5;
  return (
    <div ref={ref} className="rpt-clock">
      {lines ? (
        <svg className="rclock lines" viewBox={lines.viewBox} width="100%" role="img"
             aria-label={lines.aria}>
          <ReportGrid geom={lines} words={words} />
          {lines.lines.map(l => (
            <polyline key={l.color} className={`ln ${l.color}`}
                      points={l.points.map(([x, y]) => `${n(x)},${n(y)}`).join(" ")} />
          ))}
          {lines.marks.map((m, i) => (
            <circle key={i} cx={n(m.x)} cy={n(m.y)} r="3" className={`est ${m.color}`} />
          ))}
          {roomy ? lineEnds(lines, series).map(e => (
            <g key={e.color}>
              <circle className={`dot ${e.color}`} cx={n(e.x + 15)} cy={n(e.y)} r="5" />
              <text className="tot" x={n(e.x + 25)} y={n(e.y + 5)}>
                {names[e.color]} {clockText(e.total)}
              </text>
            </g>
          )) : null}
        </svg>
      ) : null}
      {!roomy ? (
        <div className="rpt-clockkey">
          {["red", "yellow"].map(c => (
            <span key={c}><Dot c={c} size={11} />{names[c]} {clockText(series[c])}</span>
          ))}
        </div>
      ) : null}
      {bars ? (
        <svg className="rclock bars screenonly" viewBox={bars.viewBox} width="100%" role="img"
             aria-label={bars.aria}>
          <ReportGrid geom={bars} words={words} />
          {bars.bars.map(b => (
            <rect key={b.shot} className={`bar ${b.color}${b.est ? " est" : ""}`}
                  x={n(b.x)} y={n(b.y)} width={n(b.w)} height={n(b.h)}
                  onClick={onSelect && (() => onSelect(b))}>
              <title>{b.title}</title>
            </rect>
          ))}
          {bars.median ? (
            <>
              <line className="median" x1={bars.median.x1} x2={bars.median.x2}
                    y1={n(bars.median.y)} y2={n(bars.median.y)}>
                <title>{bars.median.title}</title>
              </line>
              {roomy ? (
                <text className="mlabel" x={n(bars.median.x2 + 10)} y={n(bars.median.y + 4)}>
                  median {clockText(series.median)}
                </text>
              ) : null}
            </>
          ) : null}
          {barLabels(bars, 3).map(l => (
            <text key={l.shot} className="blabel" x={n(l.x)} y={n(l.y - 5)}>{l.text}</text>
          ))}
        </svg>
      ) : null}
      {!roomy && bars?.median ? (
        <div className="rpt-clockkey screenonly">
          Dashed line: median {clockText(series.median)} a rock
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 5: Rewrite `frontend/viewer/Report.jsx`**

```jsx
/* The game report: the printable summary.
 *
 * Always renders its content, even while closed. `@media print` forces
 * #report visible and hides everything else, so a component that returned
 * null when the report was shut would print a blank page -- and the person
 * printing would have no reason to suspect the button had anything to do with
 * it. Whether it is on screen is the stylesheet's business, via body.reporting
 * and #report.show.
 *
 * Every number comes from core/report.mjs; this file only lays them out, as
 * on board B of the "Game Report Readability" canvas. See
 * docs/superpowers/specs/2026-10-01-game-report-and-entered-scores-design.md.
 */
import { useMemo } from "react";
import {
  byEnd, clockText, coverage, coverageText, detailRows, endList, headToHead, liveGame,
  longestThinks, positionText, reportMeta, reportNotes, teamNames,
} from "../core/index.mjs";
import { Dot, ReportClock } from "./Charts.jsx";

const COLORS = ["red", "yellow"];
const other = c => (c === "red" ? "yellow" : "red");
const better = (a, b) => a != null && (b == null || a >= b);
const percent = v => (v == null ? "—" : `${v}%`);

function Team({ c, names, size }) {
  return <span className="rpt-team"><Dot c={c} size={size} />{names[c]}</span>;
}

function Hammer() {
  return (
    <svg className="ham" viewBox="0 0 6 6" role="img" aria-label="had the hammer">
      <circle cx="3" cy="3" r="3" />
    </svg>
  );
}

function ScoreCell({ end, c }) {
  if (!end.score) return <span className="none">?</span>;
  const n = end.score[c] || 0;
  return <span className={n ? "won" : "zero"}>{n}</span>;
}

function ByEnd({ table, names }) {
  const { ends, total, status } = table;
  const span = ends.length + 2;
  const rowsFor = cells => COLORS.map(c => (
    <tr key={c}>
      <th scope="row"><Team c={c} names={names} /></th>
      {cells(c)}
    </tr>
  ));
  return (
    <div className="rpt-scroll">
      <table className="rpt-byend">
        <thead>
          <tr>
            <td />
            {ends.map(e => <th key={e.number} scope="col">End {e.number}</th>)}
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          <tr className="blk"><th colSpan={span}>Score</th></tr>
          {status !== "ok" ? (
            <tr>
              <td className="rpt-noscore" colSpan={span}>
                {status === "predates"
                  ? "This chart predates board reading, so no score is shown."
                  : "The wall board was read, but its scores could not be matched to these "
                    + "ends. Setting this game’s start time places them."}
              </td>
            </tr>
          ) : rowsFor(c => (
            <>
              {ends.map(e => (
                <td key={e.number}>
                  {e.hammer === c ? <Hammer /> : null}
                  <ScoreCell end={e} c={c} />
                </td>
              ))}
              <td className={`tot${total.complete ? "" : " partial"}`}>
                {total.score ? total.score[c] : "—"}
              </td>
            </>
          ))}
          <tr className="blk"><th colSpan={span}>Shooting</th></tr>
          {rowsFor(c => (
            <>
              {ends.map(e => (
                <td key={e.number}
                    className={e.shooting[c] == null ? "none"
                      : better(e.shooting[c], e.shooting[other(c)]) ? "won" : undefined}>
                  {percent(e.shooting[c])}
                </td>
              ))}
              <td className="tot">{percent(total.shooting[c])}</td>
            </>
          ))}
          <tr className="blk"><th colSpan={span}>Thinking time</th></tr>
          {rowsFor(c => (
            <>
              {ends.map(e => <td key={e.number}>{clockText(e.thinking[c])}</td>)}
              <td className="tot">{clockText(total.thinking[c])}</td>
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ByEndKey({ table, live }) {
  const unread = table.ends.filter(e => !e.score).map(e => e.number);
  const ungraded = table.ends
    .filter(e => e.shooting.red == null && e.shooting.yellow == null).map(e => e.number);
  const ok = table.status === "ok";
  return (
    <div className="rpt-key">
      {ok ? <span><Hammer />had the hammer</span> : null}
      {ok && unread.length ? (
        <span>
          ? {endList(unread)} {!table.boardRead ? "— the wall board couldn’t be read"
            : live ? "not posted yet" : "not read from the wall board"}
        </span>
      ) : null}
      {ok && unread.length && table.total.score ? <span>totals count the ends with a score</span> : null}
      {ungraded.length ? <span>— {endList(ungraded)} not graded</span> : null}
      <span><b>bold</b> = the better of the two</span>
    </div>
  );
}

function Bar({ v, c, side }) {
  const w = v == null ? 0 : v;
  return (
    <svg className="duel-bar" viewBox="0 0 100 14" preserveAspectRatio="none" aria-hidden="true">
      <rect className="track" width="100" height="14" />
      <rect className={c} x={side === "l" ? 100 - w : 0} width={w} height="14" />
    </svg>
  );
}

function Num({ v, against, n, side }) {
  return (
    <span className={`duel-num ${side}${better(v, against) ? " better" : ""}`}>
      {percent(v)}
      {n != null ? <small>{n} rock{n === 1 ? "" : "s"}</small> : null}
    </span>
  );
}

function Duel({ rows, names }) {
  return (
    <div className="duel">
      <div className="duel-head">
        <Team c="red" names={names} />
        <Team c="yellow" names={names} />
      </div>
      {rows.map(r => (
        <div key={r.id} className={`duel-row${r.total ? " total" : ""}`}>
          <Num v={r.red} against={r.yellow} n={r.redN} side="l" />
          <Bar v={r.red} c="red" side="l" />
          <span className="duel-label">{r.label}</span>
          <Bar v={r.yellow} c="yellow" side="r" />
          <Num v={r.yellow} against={r.red} n={r.yellowN} side="r" />
        </div>
      ))}
    </div>
  );
}

function HeadToHead({ h2h, names }) {
  const rows = [...h2h.positions, { id: "team", label: "Team", ...h2h.team, total: true }];
  const others = h2h.other.map(o => `${names[o.color]} ${o.thrown} ${o.type.toLowerCase()}`);
  return (
    <div className="rpt-h2h-body">
      <div>
        <h2>By position</h2>
        <p className="sub">Shooting percentage, player against player</p>
        <Duel rows={rows} names={names} />
        <p className="rpt-cap">
          {h2h.perPlayer
            ? `Each player: ${h2h.perPlayer.graded} of ${h2h.perPlayer.thrown} rocks graded`
            : "Graded rocks only"}
        </p>
      </div>
      <div>
        <h2>By shot type</h2>
        <Duel rows={h2h.types} names={names} />
        {others.length
          ? <p className="rpt-cap">Other — {others.join(", ")} — is in the detail below.</p>
          : null}
      </div>
    </div>
  );
}

function Thinking({ view, series, names, onSelect }) {
  const longest = useMemo(() => longestThinks(view, series), [view, series]);
  return (
    <section className="card rpt-think">
      <h2>Thinking time</h2>
      <p className="sub">Running total through the game, then each rock. Click a bar to watch that rock.</p>
      <div className="rpt-think-body">
        <ReportClock series={series} names={names} onSelect={onSelect} />
        <div className="rpt-long screenonly">
          <h3>Longest thinks</h3>
          {longest.map(l => (
            <button key={`${l.ei}.${l.si}`} type="button" onClick={() => onSelect(l)}>
              <span className="t">{clockText(l.secs)}</span>
              <span className="who">
                <Dot c={l.color} size={10} />{names[l.color]} {positionText(l.position, view.format)}
              </span>
              <span className="where">
                End {l.end}, rock {l.number}{l.type ? ` · ${l.type.toLowerCase()}` : ""}
              </span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Cell({ cell }) {
  if (!cell) return <span className="empty">·</span>;
  return (
    <>
      <span className={cell.pct == null ? "none" : "p"}>{percent(cell.pct)}</span>
      <span className="n">{cell.graded === cell.thrown ? cell.graded : `${cell.graded}/${cell.thrown}`}</span>
    </>
  );
}

function Matrix({ table, c, names }) {
  return (
    <div>
      <h3 className="rpt-mhead"><Team c={c} names={names} size={14} /></h3>
      <div className="rpt-scroll">
        <table className="rpt-matrix">
          <thead>
            <tr>
              <td />
              {table.positions.map(p => <th key={p.id} scope="col">{p.label}</th>)}
              <th scope="col">All</th>
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className={r.kind === "group" ? "grp" : "type"}>
                <th scope="row">{r.label}</th>
                {r.cells.map((cell, k) => <td key={k}><Cell cell={cell} /></td>)}
                <td><Cell cell={r.all} /></td>
              </tr>
            ))}
            <tr className="all">
              <th scope="row">All</th>
              {table.all.cells.map((cell, k) => <td key={k}><Cell cell={cell} /></td>)}
              <td><Cell cell={table.all.all} /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Report({ view, stats, think, series, actions }) {
  const names = teamNames(view.game);
  const table = useMemo(() => byEnd(view), [view]);
  const h2h = useMemo(() => headToHead(stats, view.format), [stats, view.format]);
  const cov = useMemo(() => coverage(view), [view]);
  const detail = useMemo(() => COLORS.map(c => detailRows(stats, c, view.format)),
                         [stats, view.format]);
  const pill = coverageText(cov);
  const notes = reportNotes(cov, think, view.ends.length);
  return (
    <div className="rpt">
      <div className="rpt-head">
        <div className="grow">
          <div className="rpt-meta">{reportMeta(view.doc, view.gi)}</div>
          <h1 className="rpt-title">
            <Dot c="red" size={18} />{names.red}<span className="v">v</span>
            <Dot c="yellow" size={18} />{names.yellow}
          </h1>
        </div>
        {pill ? (
          <button type="button" className="rpt-cover" disabled={!cov.first}
                  onClick={() => cov.first && actions.goToBarFromReport(cov.first)}>
            {pill}<span className="noprint" aria-hidden="true">→</span>
          </button>
        ) : null}
        <button type="button" className="noprint" onClick={() => print()}>Print</button>
      </div>

      <div className="rpt-top">
        <section className="card rpt-end">
          <h2>By end</h2>
          <p className="sub">Score, shooting percentage and thinking time, end by end</p>
          <ByEnd table={table} names={names} />
          <ByEndKey table={table} live={liveGame(view.doc, view.game)} />
          <hr className="rpt-rule" />
          <h3>About these numbers</h3>
          <div className="rpt-notes">{notes.map((t, i) => <p key={i}>{t}</p>)}</div>
        </section>
        <section className="card rpt-h2h"><HeadToHead h2h={h2h} names={names} /></section>
      </div>

      {think.measured
        ? <Thinking view={view} series={series} names={names} onSelect={actions.goToBarFromReport} />
        : null}

      <section className="card rpt-detail">
        <h2>Detail</h2>
        <p className="sub">
          Position by shot type. Each cell: percentage, then rocks graded (of thrown, where
          some weren&rsquo;t graded).
        </p>
        <div className="rpt-detail-body">
          {COLORS.map((c, i) => <Matrix key={c} table={detail[i]} c={c} names={names} />)}
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 6: The Report button's label in `frontend/viewer/App.jsx`**

Inside the `<button id="reportBtn" ...>` element, replace the text `Report` with:

```jsx
          {ui.reporting ? "Close report" : "Report"}
```

- [ ] **Step 7: The stylesheet**

1. In both token blocks (`:root {` near line 14, and the dark `:root:not([data-theme="light"]) {` near line 33), add two tokens:
   - light: `--soft:#eee8da; --band:#f7f3ea;`
   - dark: `--soft:#2c251a; --band:#262017;`
2. Replace the block from `/* --- report --- */` through `.pct { font-weight:650; }` (lines ~249–254) with the block below. Then delete:
   - every `.clockcard` rule (`.clockcard .clockchart.bars` near line 310 and the `.clockcard .key …` rules near lines 324–331);
   - `td.sub {…}` and `tr.total td {…}` (lines ~332–333).
   
   Nothing else uses them: grep `clockcard\|td.sub\|tr.total\|reportgrid` across `frontend/` and `style.css`, and expect no hits once the old Report.jsx is gone.
3. In the phone block, change `  h1, #src { display: none; }` to `  header > h1, #src { display: none; }`.

```css
/* --- report ------------------------------------------------------------ */
/* The game report: a by-end table, the two teams head to head, the clock and
   the detail, as on board B of the "Game Report Readability" canvas
   (docs/superpowers/specs/2026-10-01-game-report-and-entered-scores-design.md).
   Set in Source Sans 3 like the games page, with tabular figures so every
   column of numbers lines up. Team colours are SVG fills (.rpt-dot, the
   duel bars), because a CSS background is dropped when the page prints. */
#report { padding:24px 28px 40px; display:none;
          font-family:"Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif;
          font-size:15px; line-height:1.4; font-variant-numeric:tabular-nums; }
#report.show { display:block; }
body.reporting main { display:none; }
#report table, #report th, #report button { font-family:inherit; }
.rpt { max-width:1216px; margin:0 auto; display:flex; flex-direction:column; gap:20px; }
.rpt .card { border-radius:12px; padding:20px 24px; min-width:0; }
.rpt h2 { font-family:inherit; font-size:19px; font-weight:700; line-height:1.2;
          text-transform:none; letter-spacing:0; margin:0; color:var(--ink); }
.rpt h3 { font-family:inherit; font-size:15px; font-weight:700; text-transform:none;
          letter-spacing:0; color:var(--ink); margin:0 0 6px; }
.rpt .sub { font-size:14px; color:var(--muted); margin:2px 0 14px; }
.rpt-dot { flex:none; }
.rpt-dot.red circle { fill:var(--red); }
.rpt-dot.yellow circle { fill:var(--yellow); }
.rpt-team { display:inline-flex; align-items:center; gap:8px; font-weight:600; }
.rpt-head { display:flex; align-items:flex-end; gap:16px 20px; flex-wrap:wrap; }
.rpt-meta { font-size:15px; color:var(--muted); margin-bottom:4px; }
.rpt-title { font-family:inherit; font-size:34px; line-height:1.1; font-weight:700;
             text-transform:none; letter-spacing:0; white-space:normal;
             display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.rpt-title .v { color:var(--muted); font-weight:400; font-size:26px; }
.rpt-cover { display:inline-flex; align-items:center; gap:10px; min-height:40px;
             padding:6px 16px; border-radius:999px; border:0; cursor:pointer;
             background:var(--warnbg); color:var(--warn); font-size:15px; font-weight:600; }
.rpt-cover:disabled { cursor:default; }
.rpt-top { display:grid; grid-template-columns:minmax(0,7fr) minmax(0,5fr); gap:20px;
           align-items:stretch; }

/* By end. The table is as wide as its columns, not the card: numbers spread
   across a wide card are numbers the eye cannot track along a row. */
.rpt-scroll { overflow-x:auto; }
.rpt-byend { width:auto; font-size:17px; }
.rpt-byend th, .rpt-byend td { border:0; padding:0 10px; height:38px; text-align:center;
                               font-size:inherit; color:var(--ink); }
.rpt-byend thead th { height:32px; font-size:13px; font-weight:600; color:var(--muted); }
.rpt-byend th[scope=row] { text-align:left; padding-left:0; min-width:150px; font-size:16px;
                           white-space:nowrap; position:sticky; left:0; background:var(--panel); z-index:1; }
.rpt-byend tr.blk th { height:28px; vertical-align:bottom; text-align:left; padding:0 0 4px;
                       font-size:12px; font-weight:700; letter-spacing:.08em;
                       text-transform:uppercase; color:var(--muted);
                       border-bottom:1px solid var(--line); }
.rpt-byend tbody tr:not(.blk) > * { border-bottom:1px solid var(--line); }
.rpt-byend td { min-width:64px; position:relative; }
.rpt-byend td.tot { background:var(--band); font-weight:700; min-width:90px; }
.rpt-byend td.tot.partial { color:var(--muted); }
.rpt-byend .zero, .rpt-byend .none, .rpt-byend td.none { color:var(--muted); }
.rpt-byend .won, .rpt-byend td.won { font-weight:700; }
.rpt-byend .rpt-noscore { text-align:left; padding:8px 0; height:auto; color:var(--muted);
                          font-size:14px; }
.rpt .ham { width:6px; height:6px; }
.rpt .ham circle { fill:var(--muted); }
.rpt-byend .ham { position:absolute; left:8px; top:50%; margin-top:-3px; }
.rpt-key { display:flex; flex-wrap:wrap; gap:6px 18px; font-size:13px; color:var(--muted);
           margin-top:12px; }
.rpt-key .ham { margin-right:6px; vertical-align:middle; }
.rpt-rule { border:0; height:1px; background:var(--line); margin:20px 0 16px; }
.rpt-notes p { margin:0 0 8px; font-size:14px; color:var(--muted); line-height:1.45; }

/* Head to head: mirrored bars on a 0-100 scale, the number outside each. */
.rpt-h2h-body { display:flex; flex-direction:column; gap:20px; }
.rpt-h2h-body > div + div { border-top:1px solid var(--line); padding-top:16px; }
.rpt-h2h-body > div + div h2 { font-size:17px; margin-bottom:8px; }
.duel-head, .duel-row { display:grid; align-items:center;
                        grid-template-columns:3.4em minmax(0,1fr) 6.2em minmax(0,1fr) 3.4em; }
.duel-head { height:28px; margin-bottom:4px; font-size:15px; }
.duel-head > :first-child { grid-column:1 / 3; justify-self:end; }
.duel-head > :last-child { grid-column:4 / 6; }
.duel-row { min-height:46px; }
.duel-row.total { border-top:2px solid var(--line); margin-top:4px; }
.duel-label { text-align:center; font-weight:600; }
.duel-row.total .duel-label { font-weight:700; }
.duel-num { font-size:18px; font-weight:500; color:var(--muted); line-height:1.15; }
.duel-num.better { font-weight:700; color:var(--ink); }
.duel-num.r { text-align:right; }
.duel-num small { display:block; font-size:12px; font-weight:400; color:var(--muted); line-height:1; }
.duel-bar { display:block; width:100%; height:14px; }
.duel-bar .track { fill:var(--soft); }
.duel-bar .red { fill:var(--red); }
.duel-bar .yellow { fill:var(--yellow); }
.rpt-cap { font-size:13px; color:var(--muted); margin:8px 0 0; }

/* Thinking time, drawn at the card's own width (Charts.jsx ReportClock). */
.rpt-think-body { display:grid; grid-template-columns:minmax(0,1fr) 260px; gap:40px; }
.rpt-clock { min-width:0; }
.rclock { display:block; height:auto; overflow:visible; }
.rclock.bars { margin-top:18px; }
.rclock text { font-size:13px; fill:var(--muted); }
.rclock .yl { text-anchor:end; }
.rclock .xl { text-anchor:middle; }
.rclock .g { stroke:var(--line); stroke-width:1; }
.rclock .b { stroke:var(--line); stroke-width:1; stroke-dasharray:3 4; }
.rclock .ln { fill:none; stroke-width:2.5; stroke-linejoin:round; }
.rclock .ln.red { stroke:var(--red); }
.rclock .ln.yellow { stroke:var(--yellow); }
.rclock .est { fill:var(--panel); stroke-width:1.5; }
.rclock .est.red { stroke:var(--red); }
.rclock .est.yellow { stroke:var(--yellow); }
.rclock .dot.red { fill:var(--red); }
.rclock .dot.yellow { fill:var(--yellow); }
.rclock .tot { fill:var(--ink); font-size:14px; font-weight:600; }
.rclock .bar { cursor:pointer; }
.rclock .bar:hover { opacity:.65; }
.rclock .bar.red { fill:var(--red); }
.rclock .bar.yellow { fill:var(--yellow); }
.rclock .bar.est { fill:none; stroke-width:1.5; pointer-events:all; }
.rclock .bar.est.red { stroke:var(--red); }
.rclock .bar.est.yellow { stroke:var(--yellow); }
.rclock .median { stroke:var(--ink); stroke-width:1; stroke-dasharray:5 4; opacity:.7; }
.rclock .mlabel, .rclock .blabel { fill:var(--ink); }
.rclock .blabel { font-size:12px; font-weight:600; text-anchor:middle; }
.rpt-clockkey { display:flex; flex-wrap:wrap; gap:6px 16px; font-size:14px; margin:8px 0; }
.rpt-clockkey span { display:inline-flex; align-items:center; gap:6px; font-weight:600; }
.rpt-clockkey.screenonly { font-weight:400; color:var(--muted); font-size:13px; }
.rpt-long h3 { margin-bottom:4px; }
.rpt-long button { all:unset; box-sizing:border-box; width:100%; cursor:pointer;
                   display:grid; grid-template-columns:auto 1fr; column-gap:12px;
                   grid-template-areas:"t who" "t where"; padding:9px 0;
                   border-bottom:1px solid var(--line); }
.rpt-long button:hover .who { text-decoration:underline; }
.rpt-long button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.rpt-long .t { grid-area:t; font-size:17px; font-weight:700; min-width:40px; }
.rpt-long .who { grid-area:who; display:inline-flex; align-items:center; gap:6px; font-weight:600; }
.rpt-long .where { grid-area:where; font-size:13px; color:var(--muted); }

/* Detail: one position-by-type table per team, the header said once. */
.rpt-detail-body { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:48px; }
.rpt-mhead { font-size:17px; margin:0 0 10px; }
.rpt-matrix { width:100%; font-size:16px; }
.rpt-matrix th, .rpt-matrix td { height:36px; padding:0 6px; text-align:center; white-space:nowrap;
                                 border-bottom:1px solid var(--line); font-size:inherit; color:var(--ink); }
.rpt-matrix thead th { height:28px; font-size:13px; font-weight:600; color:var(--muted); }
.rpt-matrix th[scope=row] { text-align:left; font-weight:400; padding-left:26px; }
.rpt-matrix tr.grp > * { background:var(--band); font-weight:700; }
.rpt-matrix tr.grp th[scope=row], .rpt-matrix tr.all th[scope=row] { padding-left:10px; font-weight:700; }
.rpt-matrix tr.all > * { border-top:2px solid var(--line); border-bottom:0; font-weight:700; }
.rpt-matrix .p { font-weight:600; }
.rpt-matrix .none { color:var(--muted); }
.rpt-matrix .n { font-size:13px; color:var(--muted); font-weight:400; margin-left:5px; }
.rpt-matrix .empty { color:var(--line); }

/* Phone and narrow windows: one column, tighter cells, counts under the
   percentage. The by-end table scrolls inside its card (.rpt-scroll) with
   the team column pinned, so a ten-end game never widens the page. */
@media (max-width: 820px) {
  .rpt-top, .rpt-think-body, .rpt-detail-body { grid-template-columns:minmax(0,1fr); }
  .rpt-detail-body { gap:28px; }
}
@media (max-width: 640px) {
  #report { padding:16px; }
  .rpt { gap:16px; }
  .rpt .card { padding:16px; }
  .rpt-title { font-size:28px; }
  .rpt-cover { width:100%; min-height:44px; }
  .rpt-byend { font-size:16px; }
  .rpt-byend th[scope=row] { min-width:86px; font-size:15px; }
  .rpt-byend td { min-width:48px; padding:0 4px; height:40px; }
  .rpt-byend td.tot { min-width:56px; }
  .duel-head, .duel-row { grid-template-columns:2.9em minmax(0,1fr) 4.4em minmax(0,1fr) 2.9em; }
  .duel-num { font-size:17px; }
  .rpt-matrix { font-size:15px; }
  .rpt-matrix th, .rpt-matrix td { height:46px; padding:0 2px; }
  .rpt-matrix th[scope=row] { padding-left:12px; white-space:normal; }
  .rpt-matrix .n { display:block; margin:0; line-height:1.1; }
}
```

- [ ] **Step 8: Run the tests, build, and check lint**

```bash
.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheReportPage or FlagButton or PhoneTabsCss or JsGate"
(cd frontend && npm run build)
.venv/bin/python -m pytest tests/test_frontend_build.py tests/test_viewer_server.py
```

Expected: all PASS, and `npm run build` exits 0 with no eslint errors. If eslint flags a hook dependency in `Charts.jsx`, fix the dependency list rather than disabling the rule.

- [ ] **Step 9: Look at it**

Serve this game (`PYTHONPATH=src .venv/bin/python scripts/devserve.py /home/tcuser/src/curling_score/out/mockups/report/timeline.json`). Open the view-only link, press Report, and compare it with canvas board B at 1280 px and board E at 390 px. The headless check that guards this comes in Task 5. Here, fix anything that plainly differs from the mockup.

- [ ] **Step 10: Commit**

```bash
git add frontend/viewer/Report.jsx frontend/viewer/Charts.jsx frontend/viewer/App.jsx \
        src/curling_score/viewer/index.html src/curling_score/viewer/style.css \
        tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "report: by end, head to head, the clock at full width, one table per team

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Print on one page, and a headless check that holds it there

**Files:**
- Modify: `src/curling_score/viewer/style.css` (the `@media print` block, near line 417)
- Modify: `scripts/devserve.py` (an `--overrides PATH` option)
- Create: `scripts/reportcheck.mjs`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: Task 4's classes.
- Produces: `node scripts/reportcheck.mjs <view-only url>`, which exits 0 when the report fits at 1280 and 390 px and prints on one letter page.

- [ ] **Step 1: Write the failing stylesheet tests**

```python
class TestTheReportPrints:
    ROOT = Path(__file__).resolve().parents[1]

    def print_css(self):
        css = (self.ROOT / "src/curling_score/viewer/style.css").read_text()
        start = css.index("@media print {")
        return css[start:css.index("\n}\n", start)]

    def test_print_forces_the_light_tokens(self):
        """From a dark-mode device the ink would print cream on white paper."""
        block = self.print_css()
        assert ':root, :root:not([data-theme="light"])' in block
        assert "--ink:#16130c" in block and "--panel:#fff" in block

    def test_print_drops_the_screen_only_parts(self):
        block = self.print_css()
        assert ".rpt .screenonly" in block and ".rpt-matrix tr.type" in block

    def test_print_asks_for_letter(self):
        assert "@page { size:letter;" in self.print_css()
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheReportPrints`
Expected: FAIL.

- [ ] **Step 3: Replace the `@media print` block**

```css
@media print {
  header, main, .noprint { display:none !important; }
  #report { display:block !important; padding:0; font-size:12px; }
  @page { size:letter; margin:0.45in; }
  /* Paper is white whatever the device is doing. The dark tokens are set by
     :root:not([data-theme="light"]) at (0,2,0), so the list repeats that
     selector to win by order rather than lose by specificity. */
  :root, :root:not([data-theme="light"]) {
    --bg:#fff; --panel:#fff; --ink:#16130c; --muted:#5f574a; --line:#d9d2bf;
    --soft:#eee8da; --band:#f6f3ec; --warn:#7a440c; --warnbg:#f3e3c6; --accent:#16130c;
  }
  body { background:#fff; }
  .card { break-inside:avoid; border-color:#ccc; }
  .rpt { gap:12px; max-width:none; }
  .rpt .card { border:0; border-radius:0; padding:0; }
  .rpt .screenonly { display:none !important; }
  .rpt .sub { display:none; }
  .rpt h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; margin-bottom:4px; }
  .rpt-head { padding-bottom:8px; border-bottom:2px solid var(--ink); }
  .rpt-title { font-size:22px; }
  .rpt-meta { font-size:11px; }
  .rpt-cover { background:none; padding:0; min-height:0; color:var(--muted);
               font-size:11px; font-weight:400; }
  .rpt-top { grid-template-columns:minmax(0,1fr); gap:12px; }
  .rpt-byend { font-size:12.5px; }
  .rpt-byend th, .rpt-byend td { height:22px; }
  .rpt-byend th[scope=row] { min-width:120px; font-size:12.5px; }
  .rpt-byend tr.blk th { height:18px; font-size:9.5px; }
  .rpt-key { font-size:10px; margin-top:6px; }
  .rpt-rule { margin:8px 0; }
  .rpt-notes p { font-size:10px; margin-bottom:3px; }
  .rpt h3 { font-size:11px; }
  .rpt-h2h-body { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
  .rpt-h2h-body > div + div { border-top:0; padding-top:0; }
  .duel-head { font-size:12px; height:20px; }
  .duel-row { min-height:24px; }
  .duel-num { font-size:12.5px; }
  .duel-num small { font-size:9px; }
  .duel-bar { height:10px; }
  .rpt-cap { font-size:10px; margin-top:4px; }
  .rpt-think-body { grid-template-columns:minmax(0,1fr); }
  .rclock.lines { max-height:2.1in; width:auto; max-width:100%; }
  .rpt-detail-body { gap:24px; }
  .rpt-mhead { font-size:12px; margin-bottom:4px; }
  .rpt-matrix { font-size:11.5px; }
  .rpt-matrix th, .rpt-matrix td { height:20px; }
  .rpt-matrix .n { font-size:9.5px; }
  .rpt-matrix tr.type { display:none; }
}
```

- [ ] **Step 4: devserve takes a chart's grading**

In `scripts/devserve.py`'s `main()`, after the `--live` parsing, add:

```python
    overrides = None
    if "--overrides" in args:
        i = args.index("--overrides")
        overrides = json.loads(pathlib.Path(args[i + 1]).read_text())
        del args[i:i + 2]
```

After `work_through(w, doc=doc, games=len(doc["games"]))`, add:

```python
    if overrides is not None:
        # A chart's grading, so the report has percentages to show.
        repo.update_chart(slug, overrides=overrides, overrides_version=1)
```

Add to the module docstring's usage line: `[--overrides overrides.json]`.

- [ ] **Step 5: Write `scripts/reportcheck.mjs`**

```js
/* The game report as a person sees it: nothing scrolls sideways at 1280 or
 * 390 px, the title shows, and it prints on one letter page.
 *
 *   PYTHONPATH=src .venv/bin/python scripts/devserve.py timeline.json --overrides overrides.json
 *   google-chrome --headless=new --disable-gpu --no-sandbox \
 *     --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
 *   CDP_PORT=9333 node scripts/reportcheck.mjs http://127.0.0.1:PORT/s/SHARE/
 *
 * The view-only link, because review (/g/) hides the Report button.
 * Exits 1 naming each failure. */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { attach } from "./cdp.mjs";

const url = process.argv[2];
if (!url) { console.error("usage: node scripts/reportcheck.mjs <view-only url>"); process.exit(2); }
const cdp = await attach("");
const fails = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(expr, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await cdp.eval(expr).catch(() => false)) return;
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${expr}`);
}

async function openReport(width, height, mobile) {
  await cdp.send("Emulation.setDeviceMetricsOverride",
                 { width, height, deviceScaleFactor: 1, mobile });
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: mobile });
  await cdp.send("Page.navigate", { url });
  await waitFor(`!!document.getElementById("reportBtn")`);
  await cdp.eval(`document.getElementById("reportBtn").click()`);
  await waitFor(`!!document.querySelector("#report.show .rpt-byend")`);
  await cdp.eval("document.fonts.ready.then(() => true)");
  await sleep(600);   // the clock's ResizeObserver, then a render
}

for (const [w, h, mobile] of [[1280, 900, false], [390, 844, true]]) {
  await openReport(w, h, mobile);
  const r = await cdp.eval(`({
    sw: document.documentElement.scrollWidth, iw: innerWidth,
    tall: document.documentElement.scrollHeight,
    title: getComputedStyle(document.querySelector(".rpt-title")).display })`);
  console.log(`${w}px: ${r.sw}px wide in a ${r.iw}px window, ${r.tall}px tall, title ${r.title}`);
  if (r.sw > r.iw) fails.push(`${w}px: the page scrolls sideways (${r.sw} > ${r.iw})`);
  if (r.title === "none") fails.push(`${w}px: the report title is hidden`);
}

await openReport(1280, 900, false);
const pdf = await cdp.send("Page.printToPDF", { printBackground: false, preferCSSPageSize: true });
const file = join(mkdtempSync(join(tmpdir(), "reportcheck-")), "report.pdf");
writeFileSync(file, Buffer.from(pdf.data, "base64"));
const pages = Number(/Pages:\s+(\d+)/.exec(execFileSync("pdfinfo", [file]).toString())?.[1]);
console.log(`printed: ${pages} page(s) -> ${file}`);
if (pages !== 1) fails.push(`it prints on ${pages} pages, not 1`);

if (fails.length) { console.error(fails.join("\n")); process.exit(1); }
console.log("ok");
process.exit(0);
```

- [ ] **Step 6: Run the stylesheet tests, rebuild, then run the headless check**

```bash
.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheReportPrints or TheReportPage"
(cd frontend && npm run build)
PYTHONPATH=src .venv/bin/python scripts/devserve.py \
  /home/tcuser/src/curling_score/out/mockups/report/timeline.json \
  --overrides /home/tcuser/src/curling_score/out/mockups/report/overrides.json &
# note the printed view-only URL, then:
google-chrome --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9333 \
  --user-data-dir="$(mktemp -d)" about:blank &
CDP_PORT=9333 node scripts/reportcheck.mjs http://127.0.0.1:<port>/s/<share>/
```

Expected: `ok`, with both widths reporting width ≤ window width, and `printed: 1 page(s)`. Then look at the PDF (`pdftoppm -r 70 -png <file> /tmp/…/page`). If it prints on two pages, tighten the print block in this order, rerunning the check each time, until it fits:
1. `.rclock.lines { max-height:1.7in }`;
2. `.rpt-matrix th, .rpt-matrix td { height:18px }`;
3. `.duel-row { min-height:21px }`.

Afterwards, stop devserve and Chrome by PID (`kill <pid>`), not by pattern.

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/viewer/style.css scripts/devserve.py scripts/reportcheck.mjs \
        tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "report: one letter page in print, light on any device, with a headless check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

**Phase 1 ends here.** Summarise what was checked, then continue to Phase 2 unless the user has asked to ship Phase 1 first.

---

# Phase 2: entered scores

### Task 6: `timeline.apply_entered_scores`

**Files:**
- Modify: `src/curling_score/timeline.py` (a new function after `ends_trimmed`)
- Test: `tests/test_timeline.py` (a new class at the end)

**Interfaces:**
- Consumes: `build_game(index, start_s, end_s, ends, board=None, fmt=None)`, `format_mod.of_document` and `rules.COLORS`.
- Produces: `apply_entered_scores(document: dict, entries: dict | None) -> dict`. `entries` maps an end number (as a string) to `{red, yellow, ...}`. The function returns a new document and never mutates its input.

- [ ] **Step 1: Write the failing tests**

```python
class TestEnteredScores:
    """Scores people typed in, for the ends the wall board never gave one.
    They fill gaps and nothing else: the board's figure always wins."""

    @staticmethod
    def game(scores, *, schema=8, board=True, withheld=None):
        ends = []
        for n, sc in enumerate(scores, start=1):
            ends.append({"number": n, "start_s": 600.0 * n, "end_s": 600.0 * n + 500,
                         "hammer": "yellow" if n != 3 else "red",
                         "score": sc, "score_source": "board" if sc else None,
                         "detected_score": {"red": 0, "yellow": 0},
                         "thinking_time": {"red": 10.0, "yellow": 20.0,
                                           "measured_shots": 2, "unmeasured_shots": 1},
                         "shots": []})
        g = timeline.build_game(0, 600.0, 600.0 * len(scores) + 500, ends)
        g["teams"] = {"red": {"name": "Dimmit"}, "yellow": {"name": "Grant"}}
        if board:
            g["scoreboard"] = {
                "per_end": {str(n): sc for n, sc in enumerate(scores, start=1) if sc},
                "unread_ends": [n for n, sc in enumerate(scores, start=1) if not sc],
                "final": None, "scores_withheld": withheld}
        else:
            g["scoreboard"] = None
        return {"schema_version": schema, "games": [g]}

    def test_an_unread_end_takes_the_entry(self):
        doc = self.game([{"red": 1, "yellow": 0}, {"red": 0, "yellow": 2},
                         {"red": 3, "yellow": 0}, None])
        out = timeline.apply_entered_scores(doc, {"4": {"red": 0, "yellow": 3, "by": "u"}})
        end = out["games"][0]["ends"][3]
        assert end["score"] == {"red": 0, "yellow": 3}
        assert end["score_source"] == "entered"

    def test_the_running_score_and_final_follow(self):
        doc = self.game([{"red": 1, "yellow": 0}, {"red": 0, "yellow": 2},
                         {"red": 3, "yellow": 0}, None])
        out = timeline.apply_entered_scores(doc, {"4": {"red": 0, "yellow": 3}})
        g = out["games"][0]
        assert g["ends"][3]["running"] == {"red": 4, "yellow": 5}
        assert g["final"] == {"red": 4, "yellow": 5}
        assert g["scoreboard"]["unread_ends"] == []

    def test_the_board_wins_over_an_entry(self):
        """An end the board read -- even one read after somebody entered it,
        on a reprocess or a live update -- keeps the board's figure."""
        doc = self.game([{"red": 1, "yellow": 0}, None])
        out = timeline.apply_entered_scores(doc, {"1": {"red": 0, "yellow": 4}})
        end = out["games"][0]["ends"][0]
        assert end["score"] == {"red": 1, "yellow": 0} and end["score_source"] == "board"

    def test_an_end_still_unread_leaves_the_final_unknown(self):
        doc = self.game([None, None])
        out = timeline.apply_entered_scores(doc, {"1": {"red": 2, "yellow": 0}})
        g = out["games"][0]
        assert g["ends"][1]["score"] is None and g["ends"][1]["running"] is None
        assert g["final"] is None
        assert g["scoreboard"]["unread_ends"] == [2]

    def test_a_withheld_board_is_filled_and_still_says_so(self):
        doc = self.game([None, None], withheld="could not place")
        out = timeline.apply_entered_scores(doc, {"1": {"red": 1, "yellow": 0},
                                                  "2": {"red": 0, "yellow": 0}})
        g = out["games"][0]
        assert [e["score_source"] for e in g["ends"]] == ["entered", "entered"]
        assert g["final"] == {"red": 1, "yellow": 0}
        assert g["scoreboard"]["scores_withheld"] == "could not place"

    def test_a_board_never_read_is_filled(self):
        doc = self.game([None], board=False)
        out = timeline.apply_entered_scores(doc, {"1": {"red": 0, "yellow": 1}})
        assert out["games"][0]["ends"][0]["score"] == {"red": 0, "yellow": 1}
        assert out["games"][0]["scoreboard"] is None

    def test_a_chart_from_before_board_reading_is_untouched(self):
        doc = self.game([None], schema=3)
        assert timeline.apply_entered_scores(doc, {"1": {"red": 1, "yellow": 0}}) == doc

    def test_an_end_the_game_does_not_have_is_ignored(self):
        doc = self.game([None])
        assert timeline.apply_entered_scores(doc, {"9": {"red": 1, "yellow": 0}}) == doc

    def test_the_team_names_survive(self):
        doc = self.game([None])
        out = timeline.apply_entered_scores(doc, {"1": {"red": 1, "yellow": 0}})
        assert out["games"][0]["teams"]["red"]["name"] == "Dimmit"

    def test_the_input_is_not_changed(self):
        import copy
        doc = self.game([None])
        before = copy.deepcopy(doc)
        timeline.apply_entered_scores(doc, {"1": {"red": 1, "yellow": 0}})
        assert doc == before

    def test_on_a_trimmed_game_the_key_is_the_end_shown(self):
        """The key is the number on the board's card -- the end the report
        shows as that number, after the practice is trimmed off."""
        from tests.test_service_api import practice_doc
        doc = practice_doc()
        del doc["games"][0]["scoreboard"]["per_end"]["5"]
        trimmed = timeline.trim_to_start(doc, 1440)
        out = timeline.apply_entered_scores(trimmed, {"2": {"red": 1, "yellow": 0}})
        end = out["games"][0]["ends"][1]
        assert end["id"] == 5 and end["score_source"] == "entered"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_timeline.py -k EnteredScores`
Expected: FAIL with `AttributeError: module 'curling_score.timeline' has no attribute 'apply_entered_scores'`.

- [ ] **Step 3: Implement**

Add to `src/curling_score/timeline.py`, after `ends_trimmed`. `deepcopy`, `rules` and `format_mod` are already imported there; check the imports at the top, and add any that are missing.

```python
def apply_entered_scores(document: dict, entries: dict | None) -> dict:
    """Fill the ends the board gave no score with the ones people entered.

    ``entries`` is ``Source.entered_scores``: the game's end number, as a
    string, to ``{red, yellow, by, at}``. The key is the number on the board's
    own cards, so it names the end the report shows as that number -- after
    :func:`trim_to_start` has taken any practice off, which is why this runs
    after it.

    Only ends whose score is still None are filled. The board's figure always
    wins, including one the board gave after somebody typed theirs in -- a
    reprocess, or a live game's card going up late. An entry for an end the
    game does not have is ignored. A chart from before board reading
    (schema < 4) is left alone: its ends hold the detector's guesses, which
    the viewer refuses to show, and an entered score among them would total
    with those.

    The running score, the hammer check and ``final`` are rebuilt the way
    :func:`trim_to_start` rebuilds them; keys :func:`build_game` does not own
    (the scoreboard block, the team names) are kept.
    """
    if not entries or int(document.get("schema_version") or 0) < 4:
        return document
    document = deepcopy(document)
    fmt = format_mod.of_document(document)
    for i, game in enumerate(document.get("games", [])):
        ends = [dict(e) for e in game.get("ends") or []]
        filled = []
        for end in ends:
            entry = entries.get(str(end["number"]))
            if end.get("score") is None and isinstance(entry, dict):
                end["score"] = {c: int(entry.get(c) or 0) for c in rules.COLORS}
                end["score_source"] = "entered"
                filled.append(end["number"])
        if not filled:
            continue
        rebuilt = build_game(game["index"], game["start_s"], game["end_s"], ends, fmt=fmt)
        rebuilt.pop("teams", None)
        known = bool(rebuilt["ends"]) and all(e.get("score") is not None
                                              for e in rebuilt["ends"])
        rebuilt["final"] = (deepcopy(rebuilt["ends"][-1]["running"]) if known
                            else deepcopy(game.get("final")))
        game = document["games"][i] = {**game, **rebuilt}
        board = game.get("scoreboard")
        if isinstance(board, dict) and isinstance(board.get("unread_ends"), list):
            game["scoreboard"] = {**board, "unread_ends": [n for n in board["unread_ends"]
                                                           if n not in filled]}
    return document
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_timeline.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py tests/test_timeline.py
git commit -m "timeline: entered scores fill the ends the board gave none

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `Source.entered_scores` and `set_entered_score`

**Files:**
- Modify: `src/curling_score/service/records.py` (a `Source` field)
- Modify: `src/curling_score/service/repo.py` (the protocol method and `MemoryRepo`)
- Modify: `src/curling_score/service/firestore_repo.py` (`FirestoreRepo`)
- Test: `tests/test_repo_contract.py` (`TestSources`)

**Interfaces:**
- Produces:
  - `Source.entered_scores: dict`, defaulting to `{}`;
  - `repo.set_entered_score(source_id: str, end: int, entry: dict | None) -> Source | None`. `None` clears that end, and only that end is touched.

- [ ] **Step 1: Write the failing contract tests**

Add to `class TestSources`:

```python
    def _source(self, repo):
        repo.put_source(Source(id="s_1", video_id="vidA", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_1", game_index=0, created_at=T0))

    def test_an_entered_score_round_trips(self, repo):
        self._source(repo)
        assert repo.get_source("s_1").entered_scores == {}
        entry = {"red": 0, "yellow": 3, "by": "uid-sarah", "at": "2026-10-01T12:00:00+00:00"}
        got = repo.set_entered_score("s_1", 4, entry)
        assert got.entered_scores == {"4": entry}
        assert repo.get_source("s_1").entered_scores == {"4": entry}

    def test_two_ends_are_kept_apart(self, repo):
        """Two people filling different ends at once must not overwrite each other."""
        self._source(repo)
        repo.set_entered_score("s_1", 3, {"red": 1, "yellow": 0})
        repo.set_entered_score("s_1", 4, {"red": 0, "yellow": 2})
        assert set(repo.get_source("s_1").entered_scores) == {"3", "4"}

    def test_clearing_one_end_leaves_the_others(self, repo):
        self._source(repo)
        repo.set_entered_score("s_1", 3, {"red": 1, "yellow": 0})
        repo.set_entered_score("s_1", 4, {"red": 0, "yellow": 2})
        repo.set_entered_score("s_1", 3, None)
        assert repo.get_source("s_1").entered_scores == {"4": {"red": 0, "yellow": 2}}

    def test_an_unknown_game_is_none(self, repo):
        assert repo.set_entered_score("s_nope", 1, {"red": 1, "yellow": 0}) is None
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_repo_contract.py -k TestSources`
Expected: FAIL with `AttributeError: 'Source' object has no attribute 'entered_scores'`. The Firestore half skips unless the emulator is running.

- [ ] **Step 3: Implement**

`records.py`, in `Source`, after `merged_into`:

```python
    # Scores people typed in for the ends the wall board never gave one, by
    # the game's end number as a string: {"4": {"red", "yellow", "by", "at"}}.
    # On the game, like the team names, so every chart of it shows them.
    # Gaps only: timeline.apply_entered_scores never lets one beat the board.
    entered_scores: dict = field(default_factory=dict)
```

`repo.py`, in the `Repo` protocol after `update_source`:

```python
    def set_entered_score(self, source_id: str, end: int,
                          entry: dict | None) -> Source | None: ...
```

`MemoryRepo`, after `update_source`:

```python
    def set_entered_score(self, source_id, end, entry):
        with self._lock:
            src = self.sources.get(source_id)
            if src is None:
                return None
            scores = dict(src.entered_scores or {})
            if entry is None:
                scores.pop(str(end), None)
            else:
                scores[str(end)] = dict(entry)
            src.entered_scores = scores
            return src
```

`FirestoreRepo`, after `update_source`:

```python
    def set_entered_score(self, source_id, end, entry):
        """One end's entered score, set or cleared on its own.

        A field path rather than a read-modify-write of the whole map, so two
        people filling different ends at the same moment both land. The key
        is a bare digit, which Firestore would read as a walk into a nested
        map; FieldPath quotes it, as it does the override keys above.
        """
        from google.cloud.firestore_v1.field_path import FieldPath

        ref = self._col(SOURCES).document(source_id)
        if not ref.get().exists:
            return None
        path = FieldPath("entered_scores", str(end)).to_api_repr()
        ref.update({path: self._fs.DELETE_FIELD if entry is None else dict(entry)})
        snap = ref.get()
        return Source.from_dict(snap.to_dict()) if snap.exists else None
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_repo_contract.py`
Expected: PASS for the memory half. If `FIRESTORE_EMULATOR_HOST` is set, the Firestore half passes too. If not, say so in the summary; don't claim it.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/records.py src/curling_score/service/repo.py \
        src/curling_score/service/firestore_repo.py tests/test_repo_contract.py
git commit -m "repo: a game keeps the scores people entered, one end at a time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Serving entered scores, and `POST /api/games/{source_id}/scores`

**Files:**
- Modify: `src/curling_score/service/api.py`:
  - `game_doc` (around line 440);
  - a new route after `api_set_teams` (around line 1016).
- Create: `tests/test_entered_scores.py`

**Interfaces:**
- Consumes: `timeline.apply_entered_scores` (Task 6), `repo.set_entered_score` (Task 7), and the existing `require_user`, `game_doc` and `_iso`.
- Produces: `POST /api/games/{source_id}/scores`.
  - Request body: `{end, red, yellow}` or `{end, clear: true}`.
  - Success (200): `{"ok": true, "entered_scores": {...without "by"}}`.
  - Errors:
    - 401: signed out.
    - 404: no such game.
    - 409: no finished run, a run from before board reading, or a board-read end. A board-read end's body is `{"detail": {"message", "score"}}`.
    - 422: a bad end or score.

- [ ] **Step 1: Write the failing tests**

`tests/test_entered_scores.py`:

```python
"""Any signed-in person can fill in an end the wall board didn't read.

The scores live on the game, so every chart and link of it shows them; they
fill gaps and never beat the board."""

import copy
import logging

import pytest

from tests.test_service_api import VID, sample_doc, work_through
from tests.test_service_auth import ALEX, SARAH, post, w  # noqa: F401  (w is a fixture)


def scored_doc(unread=(3,), schema=8):
    """Three ends; the board read all but ``unread``."""
    d = sample_doc(games=1)
    d["schema_version"] = schema
    g = d["games"][0]
    base = g["ends"][0]
    board = {1: {"red": 1, "yellow": 0}, 2: {"red": 0, "yellow": 2}, 3: {"red": 0, "yellow": 1}}
    g["ends"] = []
    for n in (1, 2, 3):
        e = copy.deepcopy(base)
        sc = None if n in unread else board[n]
        e.update(number=n, start_s=base["start_s"] + 900.0 * (n - 1),
                 end_s=base["start_s"] + 900.0 * n - 60, hammer="yellow",
                 score=sc, score_source="board" if sc else None)
        g["ends"].append(e)
    g["final"] = None
    g["scoreboard"] = {"per_end": {str(n): board[n] for n in (1, 2, 3) if n not in unread},
                       "unread_ends": list(unread), "final": None, "scores_withheld": None}
    return d


def a_scored_game(w, doc=None):  # noqa: F811  (w: the fixture, passed through)
    slug = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}).json()["slug"]
    work_through(w, doc=doc or scored_doc(), games=1)
    chart = w["repo"].get_chart(slug)
    return slug, chart.share_slug, chart.source_id


def end_of(w, path, n):  # noqa: F811
    return w["client"].get(path).json()["games"][0]["ends"][n - 1]


class TestEnteringAScore:
    def test_signed_out_is_turned_away(self, w):  # noqa: F811
        _, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 3}).status_code == 401

    def test_an_unread_end_takes_a_score(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w)
        r = post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 3}, SARAH)
        assert r.status_code == 200, r.text
        assert r.json()["entered_scores"]["3"]["yellow"] == 3
        assert "by" not in r.json()["entered_scores"]["3"]
        end = end_of(w, f"/c/{slug}/timeline.json", 3)
        assert end["score"] == {"red": 0, "yellow": 3} and end["score_source"] == "entered"
        game = w["client"].get(f"/c/{slug}/timeline.json").json()["games"][0]
        assert game["final"] == {"red": 1, "yellow": 5}

    def test_every_link_to_the_game_shows_it(self, w):  # noqa: F811
        _, share, src = a_scored_game(w)
        post(w, f"/api/games/{src}/scores", {"end": 3, "red": 2, "yellow": 0}, SARAH)
        for path in (f"/s/{share}/timeline.json", f"/g/{src}/timeline.json"):
            assert end_of(w, path, 3)["score"] == {"red": 2, "yellow": 0}, path

    def test_a_blank_end(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 0},
                    SARAH).status_code == 200
        assert end_of(w, f"/c/{slug}/timeline.json", 3)["score"] == {"red": 0, "yellow": 0}

    def test_an_end_the_board_read_is_refused_with_the_boards_score(self, w):  # noqa: F811
        _, _, src = a_scored_game(w)
        r = post(w, f"/api/games/{src}/scores", {"end": 1, "red": 0, "yellow": 4}, SARAH)
        assert r.status_code == 409
        assert r.json()["detail"]["score"] == {"red": 1, "yellow": 0}

    @pytest.mark.parametrize("body", [
        {"end": 3, "red": 1, "yellow": 1},      # both teams scored
        {"end": 3, "red": 9, "yellow": 0},      # more than a team has stones
        {"end": 3, "red": -1, "yellow": 0},
        {"end": 3, "red": "2", "yellow": 0},
        {"end": 3, "red": True, "yellow": 0},
        {"end": 0, "red": 1, "yellow": 0},
        {"end": 4, "red": 1, "yellow": 0},      # the game has three ends
        {"red": 1, "yellow": 0},
    ])
    def test_a_bad_score_is_refused(self, w, body):  # noqa: F811
        _, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", body, SARAH).status_code == 422

    def test_eight_is_the_most_in_fours(self, w):  # noqa: F811
        _, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 8, "yellow": 0},
                    SARAH).status_code == 200

    def test_clearing_puts_the_end_back_to_unread(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w)
        post(w, f"/api/games/{src}/scores", {"end": 3, "red": 1, "yellow": 0}, SARAH)
        r = post(w, f"/api/games/{src}/scores", {"end": 3, "clear": True}, ALEX)
        assert r.status_code == 200 and r.json()["entered_scores"] == {}
        assert end_of(w, f"/c/{slug}/timeline.json", 3)["score"] is None

    def test_two_ends_are_kept_apart(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w, scored_doc(unread=(2, 3)))
        post(w, f"/api/games/{src}/scores", {"end": 2, "red": 1, "yellow": 0}, SARAH)
        post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 2}, ALEX)
        game = w["client"].get(f"/c/{slug}/timeline.json").json()["games"][0]
        assert [e["score_source"] for e in game["ends"]] == ["board", "entered", "entered"]
        assert game["final"] == {"red": 2, "yellow": 2}

    def test_an_unknown_game_is_404(self, w):  # noqa: F811
        assert post(w, "/api/games/s_nope/scores", {"end": 1, "red": 1, "yellow": 0},
                    SARAH).status_code == 404

    def test_a_game_analysed_before_board_reading_is_refused(self, w):  # noqa: F811
        _, _, src = a_scored_game(w, scored_doc(schema=3))
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 1, "yellow": 0},
                    SARAH).status_code == 409

    def test_the_change_is_logged_with_who(self, w, caplog):  # noqa: F811
        _, _, src = a_scored_game(w)
        with caplog.at_level(logging.INFO):
            post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 3}, SARAH)
        assert any("score for end 3" in r.getMessage() and "uid-sarah" in r.getMessage()
                   for r in caplog.records)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_entered_scores.py -x`
Expected: FAIL. The first route call returns 405 or 404, because the route doesn't exist yet.

- [ ] **Step 3: Apply entered scores in `game_doc`**

In `game_doc`, change:

```python
        doc = timeline.trim_to_start(doc, start_s)
        game = doc["games"][0] if doc["games"] else None
```

to:

```python
        doc = timeline.trim_to_start(doc, start_s)
        # Scores people entered for the ends the board never gave one. After
        # the trim, because the key is the end number the trimmed game shows;
        # before the team names, because rebuilding a game resets those.
        if src is not None:
            doc = timeline.apply_entered_scores(doc, src.entered_scores)
        game = doc["games"][0] if doc["games"] else None
```

- [ ] **Step 4: Add the route after `api_set_teams`**

```python
    @app.post("/api/games/{source_id}/scores")
    def api_enter_score(source_id: str, body: dict,
                        authorization: str | None = Header(default=None)):
        """An end's score, typed in by anyone signed in, where the board gave none.

        The wall board is read off the video and misses ends: a card never
        hung, a camera that lost the board, a recording that stops before the
        last card goes up. Somebody who was there knows the score, and the
        report's by-end table shows a "?" until they say.

        It lives on the source, like the team names, so every chart and link
        of the game shows it. Gaps only: an end the board read is refused here
        (with the board's figure, so the page can say what it says), and
        ``timeline.apply_entered_scores`` lets the board win if it reads one
        later. ``end`` is the end number the review link shows -- the game as
        ``game_doc`` serves it from the current run and play start.
        """
        me = require_user(authorization)
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        run = repo.get_run(src.current_run_id)
        if run is None or run.timeline_key is None:
            raise HTTPException(409, "that game has no finished run")
        doc = game_doc(run, src.game_index, src, src.play_start_s)
        if int(doc.get("schema_version") or 0) < 4:
            raise HTTPException(409, "that game was analysed before the wall board was read")
        ends = doc["games"][0]["ends"] if doc.get("games") else []
        number = body.get("end")
        served = (next((e for e in ends if e["number"] == number), None)
                  if isinstance(number, int) and not isinstance(number, bool) else None)
        if served is None:
            raise HTTPException(422, f"end must be one of the game's {len(ends)} ends")
        if served.get("score_source") == "board":
            raise HTTPException(409, {"message": "the wall board has a score for that end",
                                      "score": served["score"]})
        if body.get("clear") is True:
            entry = None
        else:
            most = format_mod.of_document(doc).stones_per_team
            red, yellow = body.get("red"), body.get("yellow")
            whole = all(isinstance(v, int) and not isinstance(v, bool) for v in (red, yellow))
            if not whole or min(red, yellow) < 0 or (red and yellow) or max(red, yellow) > most:
                raise HTTPException(422, f"one team scores 1 to {most}, or neither")
            entry = {"red": red, "yellow": yellow, "by": me.id, "at": _iso(now())}
        src = repo.set_entered_score(src.id, number, entry)
        log.info("score for end %d set to %s on %s by %s", number,
                 None if entry is None else {"red": entry["red"], "yellow": entry["yellow"]},
                 source_id, me.id)
        return {"ok": True,
                "entered_scores": {k: {f: v for f, v in e.items() if f != "by"}
                                   for k, e in (src.entered_scores or {}).items()}}
```

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_entered_scores.py tests/test_service_auth.py \
  tests/test_service_api.py -k "not live"
```

Expected: PASS. Then run `tests/test_service_api.py` whole (no `-k`) once, and check the exit code is 0.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/service/api.py tests/test_entered_scores.py
git commit -m "api: anyone signed in can fill in an end the board didn't read

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Entering a score from the report

**Files:**
- Create: `frontend/runtime/auth.mjs` (moved out of `flag.mjs`)
- Modify: `frontend/runtime/flag.mjs` (use `auth.mjs`)
- Create: `frontend/runtime/scores.mjs`
- Modify: `frontend/core/report.mjs` (`scoreChoices` and `scoreError`)
- Modify: `frontend/core/watch.mjs` (`endSummary` gains `entered`)
- Modify: `frontend/viewer/main.jsx` (`reload`)
- Modify: `frontend/viewer/App.jsx` (pass `reload` and `config`)
- Modify: `frontend/viewer/Report.jsx` (the picker and entered style)
- Modify: `frontend/viewer/ChartPanel.jsx` (`Scoreboard`: entered style; show it for withheld or unread boards with entries)
- Modify: `frontend/viewer/Timing.jsx` (`EndHead`: entered style)
- Modify: `src/curling_score/viewer/style.css`
- Modify: `tests/js/singleton.mjs`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes:
  - `POST /api/games/{source_id}/scores` (Task 8);
  - `doc.chart.source_id` (Task 3);
  - `end.score_source === "entered"` (Task 6);
  - `site/auth.js`'s `onUser`, `currentUser`, `enabled`, `whenReady` and `signIn`.
- Produces:
  - `scoreChoices(fmt) -> [{label?, color?, n?, red, yellow}]`
  - `scoreError(status) -> string`
  - `runtime/auth.mjs`: `SIGN_IN_WAIT_MS`, `accountsOn()`, `whoIsSignedIn()` and `signInNow()`
  - `runtime/scores.mjs`: `sendScore(sourceId, body, token)`
  - `App({ doc, config, cursor, reload })`

- [ ] **Step 1: Write the failing tests**

```python
class TestEnteringScores:
    ROOT = Path(__file__).resolve().parents[1]

    def src(self, rel):
        return (self.ROOT / rel).read_text()

    def test_fours_offers_a_blank_and_one_to_eight_each(self):
        got = run_js("out(scoreChoices(FOURS_FORMAT).map(c => [c.red, c.yellow]));")
        assert got[0] == [0, 0] and len(got) == 17
        assert got[1:9] == [[n, 0] for n in range(1, 9)]
        assert got[9:] == [[0, n] for n in range(1, 9)]

    def test_doubles_offers_one_to_six(self):
        from curling_score.game import format as F
        got = run_js(f"out(scoreChoices({json.dumps(F.DOUBLES.to_json())}).length);")
        assert got == 13

    def test_an_entered_end_is_marked(self):
        d = doc([shot(1, "red", "lead")])
        d["games"][0]["ends"][0].update(score={"red": 0, "yellow": 3}, score_source="entered")
        assert report_js("out(byEnd(view).ends[0].entered);", d) is True

    def test_the_errors_people_see(self):
        assert run_js("out([scoreError(409), scoreError(401), scoreError(0), scoreError(500)]);") == [
            "The board has a score for this end now", "Sign in again to save",
            "Couldn't save. Try again.", "Couldn't save. Try again."]

    def test_sign_in_is_asked_on_demand_from_one_place(self):
        auth = self.src("frontend/runtime/auth.mjs")
        assert 'import("../site/auth.js")' in auth
        assert "settled ??= settledUser(auth.onUser)" in auth
        assert "u.getIdToken()" in auth[auth.index("export async function whoIsSignedIn"):]
        flag = self.src("frontend/runtime/flag.mjs")
        assert 'from "./auth.mjs"' in flag

    def test_the_picker_needs_a_game_and_a_person(self):
        report = self.src("frontend/viewer/Report.jsx")
        assert 'who === "in" && !!view.doc.chart?.source_id' in report
        assert '<dialog ref={ref} id="scoreDialog"' in report

    def test_a_save_refetches_the_chart(self):
        main = self.src("frontend/viewer/main.jsx")
        assert "reload={reload}" in main
        report = self.src("frontend/viewer/Report.jsx")
        assert "await actions.reloadDoc()" in report

    def test_the_picker_buttons_are_big_enough_on_a_phone(self):
        css = self.src("src/curling_score/viewer/style.css")
        assert "#scoreDialog .sc-nums button { min-width:44px; min-height:44px;" in css
```

Then make three edits so the old tests follow the moved code:
- In `TestTheFlagButton.test_send_asks_who_again_with_a_fresh_token`, replace the last three lines (from `run = self.src("frontend/runtime/flag.mjs")` on) with:

  ```python
          run = self.src("frontend/runtime/auth.mjs")
          assert "settled ??= settledUser(auth.onUser)" in run
          assert "u.getIdToken()" in run[run.index("export async function whoIsSignedIn"):]
  ```

- In `test_firebase_is_only_ever_imported_on_demand`, change `self.src("frontend/runtime/flag.mjs")` to `self.src("frontend/runtime/auth.mjs")`.
- In `tests/js/singleton.mjs`, add `export const scoreChoices = core.scoreChoices;`, `export const scoreError = core.scoreError;` and `export const FOURS_FORMAT = core.FOURS;`.

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "EnteringScores or FlagButton"`
Expected: FAIL, because `scoreChoices` and `runtime/auth.mjs` don't exist yet.

- [ ] **Step 3: Core helpers**

Append to `frontend/core/report.mjs`:

```js
/* What the score picker offers for one end: a blank end, then each team
 * scoring one up to every stone it has (8 in fours, 6 in doubles). Only one
 * team scores in an end, so there is nothing to type. */
export function scoreChoices(fmt = FOURS) {
  const most = fmt.stones_per_team || FOURS.stones_per_team;
  const out = [{ label: "Blank end", red: 0, yellow: 0 }];
  for (const c of COLORS)
    for (let n = 1; n <= most; n++)
      out.push({ color: c, n, red: c === "red" ? n : 0, yellow: c === "yellow" ? n : 0 });
  return out;
}

/* What the picker says when a save does not land. */
export function scoreError(status) {
  if (status === 409) return "The board has a score for this end now";
  if (status === 401) return "Sign in again to save";
  return "Couldn't save. Try again.";
}
```

In `frontend/core/watch.mjs`'s `endSummary`, add to the returned object after `running:`:

```js
    // Whether the running score leans on a score somebody typed in.
    entered: readable && view.ends.slice(0, ei + 1).some(x => x.end.score_source === "entered"),
```

- [ ] **Step 4: `runtime/auth.mjs`, `runtime/scores.mjs`, and `flag.mjs` slimmed down**

`frontend/runtime/auth.mjs`:

```js
/* Who is signed in, asked for on demand.
 *
 * The viewer does not load Firebase (see site/auth.js, lines 1-12). Asking
 * who is signed in imports that module on demand -- esbuild inlines it but
 * does not evaluate it until this runs -- so a charting session that never
 * asks never fetches the SDK. The session is per origin, so someone signed
 * in on the site is signed in here. All of it fails soft: no answer within
 * SIGN_IN_WAIT_MS is "not known", never a stuck page.
 *
 * Moved out of flag.mjs when the score picker came to need the same answer. */
import { settleWithin, settledUser } from "../core/index.mjs";

export const SIGN_IN_WAIT_MS = 3000;

// One wait for auth to decide, shared by every call: a second call after a
// slow first one picks up the answer that has arrived since.
let settled = null;

async function signedIn() {
  const auth = await import("../site/auth.js");
  await (settled ??= settledUser(auth.onUser));
  return auth.currentUser();
}

/* Whether accounts exist here at all: not under `curling-score serve`, nor
 * when the site's config turns them off. */
export async function accountsOn(ms = SIGN_IN_WAIT_MS) {
  const auth = await settleWithin(import("../site/auth.js"), ms, null);
  if (!auth) return false;
  await settleWithin(auth.whenReady(), ms, null);
  return auth.enabled();
}

/* {email, token} for a signed-in person, null for nobody, undefined when auth
 * has not answered within `ms`. The token is asked for on every call:
 * Firebase hands back a fresh one when it is near expiry, and a page left
 * open past the hour would otherwise send a dead token, which the server can
 * only read as anonymous. */
export async function whoIsSignedIn(ms = SIGN_IN_WAIT_MS) {
  const u = await settleWithin(signedIn(), ms, undefined);
  if (!u) return u;
  const token = await settleWithin(u.getIdToken(), ms, null);
  return token ? { email: u.email ?? null, token } : undefined;
}

/* Google's sign-in popup. Resolves once signed in; throws if it was shut. */
export async function signInNow() {
  const auth = await import("../site/auth.js");
  return auth.signIn();
}
```

`frontend/runtime/flag.mjs`: replace everything from the first `import` line down to the end of `whoIsFlagging` with the following, and keep `FAILED` and `sendFlag` as they are:

```js
/* Sending a flag, and finding out who is sending it (runtime/auth.mjs). */
import { SIGN_IN_WAIT_MS, whoIsSignedIn } from "./auth.mjs";

export { SIGN_IN_WAIT_MS };

/* {email, token} for a signed-in person, null for nobody, undefined when auth
 * has not answered in time. The dialog asks again at Send, so a slow first
 * answer costs the display, never the attribution. */
export const whoIsFlagging = whoIsSignedIn;
```

`frontend/runtime/scores.mjs`:

```js
/* Sending an end's score, entered by hand, to the game it belongs to. The
 * server keeps it on the game, so every chart of it shows it. */
export async function sendScore(sourceId, body, token) {
  try {
    const r = await fetch(`/api/games/${encodeURIComponent(sourceId)}/scores`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    return r.ok ? { ok: true, entered: data.entered_scores } : { ok: false, status: r.status };
  } catch {
    return { ok: false, status: 0 };
  }
}
```

- [ ] **Step 5: Refetch the document after a save**

In `frontend/viewer/main.jsx`, change the import to `import { useCallback, useEffect, useState } from "react";`. In `Live`, before `return`, add:

```jsx
  /* After a change made somewhere other than the overrides -- an entered
   * score, which the server merges into the game -- ask again rather than
   * patch the document here, so the page shows what every link shows. */
  const reload = useCallback(async () => {
    try {
      const r = await fetch("timeline.json", { cache: "no-cache" });
      if (r.ok) setDoc(await r.json());
    } catch { /* the next look, or a reload, will catch it up */ }
  }, []);
```

Change the return to `return <App doc={doc} config={config} cursor={cursor} reload={reload} />;`.

In `frontend/viewer/App.jsx`:
- change the signature to `export function App({ doc, config, cursor, reload })`;
- add `reloadDoc: reload,` to the `actions` object (after `goToBarFromReport`), and `reload` to that `useMemo`'s dependency list;
- change the Report element to `<Report view={view} stats={stats} think={think} series={series} actions={actions} config={config} />`.

- [ ] **Step 6: The picker in `Report.jsx`**

Change the imports at the top to:

```jsx
import { useEffect, useMemo, useRef, useState } from "react";
import {
  byEnd, clockText, coverage, coverageText, detailRows, endList, headToHead, liveGame,
  longestThinks, positionText, reportMeta, reportNotes, scoreChoices, scoreError, teamNames,
} from "../core/index.mjs";
import { accountsOn, signInNow, whoIsSignedIn } from "../runtime/auth.mjs";
import { sendScore } from "../runtime/scores.mjs";
import { Dot, ReportClock } from "./Charts.jsx";
```

Add these components above `Report`:

```jsx
/* Who may enter a score: "in", "out", or null while unknown or where
 * accounts are off -- then there is no button at all, only the "?". */
function useWho(config) {
  const [who, setWho] = useState(null);
  useEffect(() => {
    if (!config?.hosted) return undefined;
    let live = true;
    (async () => {
      if (!(await accountsOn())) return;
      const w = await whoIsSignedIn();
      if (live && w !== undefined) setWho(w ? "in" : "out");
    })();
    return () => { live = false; };
  }, [config?.hosted]);
  return [who, setWho];
}

/* The picker for one end. A modal <dialog>, like the flag dialog: in the
 * top layer, above the phone shell, and never inside a hidden parent. */
function ScoreDialog({ at, names, fmt, busy, error, onPick, onClear, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (at && !d.open) d.showModal();
    if (!at && d.open) d.close();
  }, [at]);
  const choices = scoreChoices(fmt);
  return (
    <dialog ref={ref} id="scoreDialog" onClose={onClose} aria-labelledby="scoreTitle">
      {at ? (
        <>
          <h2 id="scoreTitle">End {at.number} score</h2>
          <p className="sc-why">
            For an end the wall board didn&rsquo;t read. Everyone who opens this game sees it,
            marked as entered by hand.
          </p>
          <button type="button" className="sc-blank" disabled={busy}
                  onClick={() => onPick(choices[0])}>Blank end</button>
          {["red", "yellow"].map(c => (
            <div key={c} className="sc-row">
              <Team c={c} names={names} />
              <div className="sc-nums">
                {choices.filter(x => x.color === c).map(x => (
                  <button key={x.n} type="button" disabled={busy} aria-label={`${names[c]} ${x.n}`}
                          className={at.score && at.score[c] === x.n ? "on" : undefined}
                          onClick={() => onPick(x)}>{x.n}</button>
                ))}
              </div>
            </div>
          ))}
          {error ? <p className="sc-error" role="alert">{error}</p> : null}
          <div className="sc-foot">
            {at.entered ? <button type="button" disabled={busy} onClick={onClear}>Clear</button> : null}
            <span className="grow" />
            <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
          </div>
        </>
      ) : null}
    </dialog>
  );
}
```

Replace `ScoreCell` with this version, which can open the picker:

```jsx
function ScoreCell({ end, c, entry }) {
  const n = end.score ? (end.score[c] || 0) : null;
  const shown = n == null ? "?" : n;
  const cls = `${n == null ? "none" : n ? "won" : "zero"}${end.entered ? " entered" : ""}`;
  if (entry?.can && (n == null || end.entered))
    return (
      <button type="button" className={`sc-q ${cls}`} onClick={() => entry.open(end)}
              aria-label={n == null ? `Add end ${end.number}’s score` : `Change end ${end.number}’s score`}>
        {shown}
      </button>
    );
  if (entry?.signIn && n == null)
    return (
      <button type="button" className="sc-q none" title="Sign in to add the score"
              aria-label="Sign in to add the score" onClick={entry.signIn}>?</button>
    );
  return <span className={cls}>{shown}</span>;
}
```

In `ByEnd`:
- change the signature to `function ByEnd({ table, names, entry })`;
- pass `entry={entry}` to each `<ScoreCell end={e} c={c} entry={entry} />`;
- change the `status !== "ok"` test to `!showsScores(table, entry)`;
- add above `ByEnd`:

```jsx
/* Whether the score rows show: always where the board can speak, and on a
 * withheld board once there is something entered or a way to enter it. */
const showsScores = (table, entry) => table.status === "ok"
  || (table.status === "withheld" && (!!entry?.can || table.ends.some(e => e.entered)));
```

In `ByEndKey`, add `entry` to its props. After the "had the hammer" span, add:

```jsx
      {table.ends.some(e => e.entered) ? <span><u className="entered">3</u> entered by hand</span> : null}
      {ok && unread.length && entry?.signIn ? <span>sign in to add missing scores</span> : null}
      {table.status === "withheld" && showsScores(table, entry)
        ? <span>the wall board&rsquo;s scores couldn&rsquo;t be matched to these ends</span> : null}
```

In `Report`, change the signature to `export function Report({ view, stats, think, series, actions, config })`. Before the `return`, add:

```jsx
  const [who, setWho] = useWho(config);
  const [at, setAt] = useState(null);       // the end being entered, or null
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const canEnter = who === "in" && !!view.doc.chart?.source_id && table.status !== "predates";
  const close = () => { setAt(null); setError(null); };
  const save = async body => {
    setBusy(true);
    setError(null);
    const w = await whoIsSignedIn();
    if (!w) {
      setBusy(false);
      setError(scoreError(401));
      if (w === null) setWho("out");
      return;
    }
    const r = await sendScore(view.doc.chart.source_id, { end: at.number, ...body }, w.token);
    if (r.ok || r.status === 409) await actions.reloadDoc();
    setBusy(false);
    if (r.ok) close();
    else setError(scoreError(r.status));
  };
  const entry = canEnter
    ? { can: true, open: e => { setError(null); setAt(e); } }
    : who === "out" && view.doc.chart?.source_id && table.status !== "predates"
      ? { signIn: async () => { try { await signInNow(); setWho("in"); } catch { /* shut */ } } }
      : null;
```

Change `<ByEnd table={table} names={names} />` to `<ByEnd table={table} names={names} entry={entry} />` and `<ByEndKey … />` to add `entry={entry}`. As the last child of `.rpt`, add:

```jsx
      <ScoreDialog at={at} names={names} fmt={view.format} busy={busy} error={error}
                   onPick={x => save({ red: x.red, yellow: x.yellow })}
                   onClear={() => save({ clear: true })} onClose={close} />
```

The source test in Step 1 looks for `who === "in" && !!view.doc.chart?.source_id`, and that string sits inside `canEnter`.

- [ ] **Step 7: The entered style in the sidebar and on the phone's Timing tab**

In `frontend/viewer/ChartPanel.jsx`'s `Scoreboard`:

1. After `const unread = …`, add:

   ```jsx
     const entered = game.ends.some(e => e.score_source === "entered");
   ```

2. Change `) : board?.scores_withheld ? (` to `) : board?.scores_withheld && !entered ? (`.
3. Change `) : board ? (` to `) : board || entered ? (`.
4. Give each score cell the entered class:

   ```jsx
                       <td key={e.number} className={e.score == null ? "unread"
                         : e.score_source === "entered" ? "entered" : ""}>
   ```

5. In the `scorekey` paragraph, add `{entered ? <span><u className="entered">2</u> entered by hand</span> : null}`.

In `frontend/viewer/Timing.jsx`'s `EndHead`:
- add `entered` to the destructured summary fields;
- change the running span to:

  ```jsx
          <span className={`wsc${entered ? " entered" : ""}`}
                title={entered ? "Includes a score entered by hand" : undefined}>
  ```

- [ ] **Step 8: The picker's and entered marks' CSS**

Append after the report block in `style.css`:

```css
/* Entered by hand: a dotted underline wherever a score shows, so a typed-in
   figure never passes for the board's. */
.rpt .entered, #score td.entered, .wsc.entered, .scorekey u.entered {
  text-decoration:underline dotted; text-decoration-thickness:1.5px; text-underline-offset:3px; }
.rpt-byend .sc-q { all:unset; box-sizing:border-box; cursor:pointer; min-width:32px; min-height:32px;
                   display:inline-flex; align-items:center; justify-content:center;
                   border-radius:6px; border:1px dashed var(--line); padding:0 6px; }
.rpt-byend .sc-q:hover { border-color:var(--muted); }
.rpt-byend .sc-q:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
#scoreDialog { width:min(460px, calc(100vw - 32px)); padding:16px 18px; border:1px solid var(--line);
               border-radius:10px; background:var(--panel); color:var(--ink);
               font-family:"Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif; }
#scoreDialog::backdrop { background:rgb(0 0 0 / .35); }
#scoreDialog h2 { margin:0 0 4px; font:inherit; font-size:19px; font-weight:700;
                  text-transform:none; letter-spacing:0; }
#scoreDialog .sc-why { margin:0 0 12px; font-size:14px; color:var(--muted); }
#scoreDialog .sc-blank { width:100%; min-height:44px; margin-bottom:12px; font:inherit; font-weight:600; }
#scoreDialog .sc-row { margin-bottom:12px; }
#scoreDialog .sc-row .rpt-team { margin-bottom:6px; }
#scoreDialog .sc-nums { display:flex; flex-wrap:wrap; gap:6px; }
#scoreDialog .sc-nums button { min-width:44px; min-height:44px; font:inherit; font-size:17px;
                               font-weight:600; font-variant-numeric:tabular-nums; }
#scoreDialog .sc-nums button.on { background:var(--accent); color:var(--onaccent); }
#scoreDialog .sc-error { margin:4px 0 8px; color:var(--warn); font-weight:600; }
#scoreDialog .sc-foot { display:flex; gap:8px; align-items:center; margin-top:4px; }
#scoreDialog .sc-foot button { min-height:44px; padding:0 16px; font:inherit; }
```

Then add two lines inside the existing `@media print` block, after `.rpt-matrix tr.type { display:none; }`. A second `@media print` block earlier in the file would become the first one, and the print tests read the first:

```css
  .rpt-byend .sc-q { border:0; padding:0; }
  #scoreDialog { display:none; }
```

- [ ] **Step 9: Run the tests and build**

```bash
.venv/bin/python -m pytest tests/test_viewer_js.py
(cd frontend && npm run build)
.venv/bin/python -m pytest tests/test_frontend_build.py tests/test_viewer_server.py
```

Expected: PASS, and lint clean.

- [ ] **Step 10: Look at it**

Run devserve plus the report check (Task 5, Step 6) again: it must still say `ok` and print one page. Devserve has no accounts, so the "?" stays a plain mark there. That is the signed-out-and-no-accounts path, and it should render exactly as in Phase 1.

To see the picker, open the dialog by hand in a devserve session's devtools:

```js
document.querySelector("#scoreDialog").showModal()
```

The dialog renders empty unless an end is chosen. That's expected: the real path needs a signed-in user, which the API tests in Task 8 cover. Confirm the dialog is styled and doesn't overflow at 390 px. A real signed-in check happens after deploy, which isn't part of this plan.

- [ ] **Step 11: Commit**

```bash
git add frontend/runtime/auth.mjs frontend/runtime/flag.mjs frontend/runtime/scores.mjs \
        frontend/core/report.mjs frontend/core/watch.mjs frontend/viewer/main.jsx \
        frontend/viewer/App.jsx frontend/viewer/Report.jsx frontend/viewer/ChartPanel.jsx \
        frontend/viewer/Timing.jsx src/curling_score/viewer/style.css tests/js/singleton.mjs \
        tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "report: a signed-in person fills in a score the board didn't read

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the last task

- Run every test file the plan touched, one command per file, and check each exit code:

  ```bash
  for f in tests/test_viewer_js.py tests/test_timeline.py tests/test_entered_scores.py \
           tests/test_repo_contract.py tests/test_service_api.py tests/test_service_auth.py \
           tests/test_frontend_build.py tests/test_viewer_server.py; do
    .venv/bin/python -m pytest "$f" > /tmp/claude-1000/pytest-$(basename $f).log 2>&1; echo "$f $?"; done
  ```

- Run the report check once more on the final build.
- Don't merge, push or deploy. Report the branch, the commits and what was and wasn't checked (the Firestore half of the contract tests; a real signed-in save). Then ask about merging and deploying. Deploying is its own step (see the deploying-curling-chart notes).
