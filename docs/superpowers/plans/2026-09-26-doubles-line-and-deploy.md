# Doubles Thrown Line, and Deploy for Submissions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a doubles rock that nobody held a broom for still gets its thrown line, its hack call and its curl. Then everything since the last deploy ships, so a doubles video submitted to the live service is charted.

**Architecture:**
- **Python.** `linetime.time_lines` stops requiring a target broom when the game format says so; `DOUBLES` does, `FOURS` does not. A broomless `Line` has every broom figure as `None`. It is pinned instead by `at_tee_x`: the thrown line's x where it crosses the destination tee.
- **Serialisation.** `timeline._line` writes `"at_broom": null` and an extra `"at_tee"` key, only for such a line.
- **Viewer.** `frontend/core/line.mjs` draws and measures a broomless line through `at_hog` and `at_tee`, and it dashes out the figures that need a broom.
- **Deploy.** Phase 7 checks four-player parity twice, with the full pipeline on 3 videos and the shot lists on 16 games. It then deploys the worker and the API from a clean worktree with `DOUBLES_ENABLED` on, and submits one cached doubles game through the live API.

**Tech Stack:** Python 3 (pytest, numpy), framework-free ES modules plus React (esbuild via `npm run build`), Docker Compose on the GPU worker, Cloud Run.

**Spec:** `docs/superpowers/specs/2026-09-25-mixed-doubles-design.md` (phases 6–7). Phase 0 changed phase 6's direction: `docs/superpowers/specs/2026-09-25-mixed-doubles-phase0.md` (§ "The broom, checked").

## Global Constraints

- **Four-player charts must not change.** Every field of a four-player document stays as it is. Parity is judged as in phases 1–3. With `--no-longview` the output is identical. With the long camera, head must equal one of the two base runs per video, because the base pipeline's end boundaries jitter by ±5 s.
- **The broomless line is doubles only** (the user's decision, 2026-09-25). A doubles rock that has a target broom keeps today's broom line unchanged.
- A broomless line has `at_broom: null`, `at_hog.offset_m: null` and `side: null`. Its start, fit, `hog_path`, `path`, `curl` and `confirmed` are computed as they are today.
- The document's `format` block does not change. `line_without_broom` is a code-side property of the format, and the viewer derives it from the format's name.
- Build the viewer only with `cd frontend && npm run build`. Commit the rebuilt `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js` (if it changed) and `frontend/.buildstamp.json` with the sources.
- Run tests with `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider <files>`, as named subsets only. The whole suite OOMs this machine.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage explicit paths, never `git add -A`.
- Worker: `administrator@10.0.0.182`, with `ssh -o BatchMode=yes`.
  - Until Task 5, never stop, restart, rebuild or modify the live worker (`/data/wdd/curling_score`, compose project `deploy`).
  - Files the container writes are root-owned. Read them with `docker compose -f docker-compose.worker.yml exec -T worker cat <path>`, or chmod them in a throwaway container.
- `PIPELINE_VERSION` is not bumped. Four-player output does not change, and no doubles run exists on the live service yet.

## Review Focus

1. **A doubles rock that has a broom** must get exactly the line it gets today, not a broomless one. Task 1 pins this with `test_a_doubles_rock_with_a_broom_keeps_the_broom_line`.
2. **A broomless rock whose stone was not seen before the push** (`start` is None) still gets a line, hack call from the player's other rocks. Task 1 pins this with `test_no_broom_and_no_start_is_still_a_line`.
3. **A four-player chart with a line but no broom** (hand-edited, or a malformed document) keeps today's reason, "No broom was held still before the release", and draws no line. Task 2 pins this with `test_a_fours_line_without_a_broom_is_still_refused`.
4. **A broomless line with no rest position and no path** shows curl as "–" and does not throw. Task 2 pins this with `test_a_broomless_line_with_nowhere_to_measure_to`.
5. **A broomless line missing `at_tee`** (a malformed document) measures and draws nothing that needs it, and does not throw. Task 2 pins this with `test_a_broomless_line_without_at_tee_draws_no_line`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/curling_score/game/format.py` | game formats | `GameFormat.line_without_broom` (FOURS False, DOUBLES True), not in `to_json` |
| `src/curling_score/game/linetime.py` | the thrown line | `measure` accepts `broom=None`; `Line.at_tee_x`; `time_lines(..., without_broom=False)` |
| `src/curling_score/analyze.py` | the pipeline | passes `without_broom=fmt.line_without_broom` |
| `src/curling_score/timeline.py` | the document | `_line` writes `at_broom: null` and `at_tee` for a broomless line |
| `frontend/core/format.mjs` | format helpers | `lineWithoutBroom(fmt)` |
| `frontend/core/line.mjs` | Detail pane maths | `lineX` via `at_tee`; `lineReason` format-aware; broomless figures; broomless strip |
| `tests/test_format.py`, `tests/test_linetime.py`, `tests/test_timeline.py`, `tests/test_viewer_js.py` | tests | new cases |
| `deploy/cloudrun.yaml` | Cloud Run env | `DOUBLES_ENABLED: "1"` (Task 5) |
| `docs/superpowers/plans/2026-09-26-doubles-line-and-deploy.acceptance.txt` | the record | Tasks 3–5 results |

---

### Task 1: A broomless thrown line in the pipeline (doubles only)

**Files:**
- Modify: `src/curling_score/game/format.py` (the `GameFormat` dataclass, `DOUBLES`)
- Modify: `src/curling_score/game/linetime.py:1-16` (docstring), `:58-71` (`Line`), `:136-149` (`measure`), `:347-387` (`time_lines`)
- Modify: `src/curling_score/analyze.py:459-461` (the `time_lines` call)
- Modify: `src/curling_score/timeline.py:36-47` (`_line`)
- Test: `tests/test_format.py`, `tests/test_linetime.py`, `tests/test_timeline.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `GameFormat.line_without_broom: bool`;
  - `linetime.Line.at_tee_x: float | None` (the last field, default None);
  - `linetime.measure(fit, track, start, broom, rest=None, path=())`, where `broom` may be None;
  - `linetime.time_lines(shots, video, hog_view, dest_view, *, model=None, decode=None, detect=None, without_broom=False) -> int`;
  - the document line JSON gains `"at_tee": {"x": float}` and `"at_broom": null` only when there was no broom.

- [ ] **Step 1: Write the failing tests**

In `tests/test_format.py` (add at the end; `F` is how that file imports `curling_score.game.format`; check the top of the file and use its alias):

```python
class TestLineWithoutBroom:
    def test_only_doubles_draws_a_line_without_a_broom(self):
        assert F.DOUBLES.line_without_broom is True
        assert F.FOURS.line_without_broom is False

    def test_the_document_block_does_not_carry_it(self):
        assert "line_without_broom" not in F.DOUBLES.to_json()
        assert "line_without_broom" not in F.FOURS.to_json()
```

In `tests/test_linetime.py`, add to `class TestMeasure`:

```python
    def test_no_broom_is_a_line_with_no_broom_figures(self):
        track = self.aimed(0.30)
        fit = L.fit_line(track)
        line = L.measure(fit, track, self.START, None, rest=(0.2, 0.5))
        assert (line.at_broom_x, line.miss, line.at_hog_offset, line.side) == (None, None, None, None)
        assert line.curl == "left"
        assert line.at_hog_x == pytest.approx(fit.x(L.HOG_Y))
        assert line.at_tee_x == pytest.approx(fit.x(0.0))
        assert line.fit_n == 40

    def test_no_broom_and_no_start_is_still_a_line(self):
        track = self.aimed()
        line = L.measure(L.fit_line(track), track, None, None)
        assert line.start is None and line.at_tee_x is not None

    def test_a_broom_line_has_no_tee_point(self):
        track = self.aimed()
        assert L.measure(L.fit_line(track), track, self.START, self.BROOM).at_tee_x is None
```

In `tests/test_linetime.py`, add to `class TestTimeLines`. Change `run` so it forwards keyword arguments to `time_lines`. Replace the `self.n = L.time_lines(...)` line in `run` with the version below and add `**kw` to `run`'s signature: `def run(self, shots, extra=(), **kw):`.

```python
        self.n = L.time_lines(shots, "v.mp4", HOG_VIEW, DEST_VIEW, model=object(),
                              decode=decode, detect=detect, **kw)
```

Then add:

```python
    def test_without_broom_a_shot_with_no_broom_gets_a_broomless_line(self):
        s = self.shot(crossing_for(self.stone_to(11.5)), target_broom=None)
        self.run([s], without_broom=True)
        assert s.line is not None
        assert s.line.at_broom_x is None and s.line.at_tee_x is not None
        assert self.n == 1

    def test_by_default_no_broom_is_still_no_line(self):
        s = self.shot(crossing_for(self.stone_to(11.5)), target_broom=None)
        self.run([s])
        assert s.line is None and self.n == 0

    def test_a_doubles_rock_with_a_broom_keeps_the_broom_line(self):
        c = crossing_for(self.stone_to(11.5))
        plain, doubles = self.shot(c), self.shot(c)
        self.run([plain])
        self.run([doubles], without_broom=True)
        assert doubles.line == plain.line
        assert doubles.line.at_tee_x is None

    def test_without_broom_still_needs_a_release_and_a_seen_rock(self):
        c = crossing_for(self.stone_to(11.5))
        shots = [self.shot(c, target_broom=None, release=None),
                 self.shot(c, target_broom=None, missing=True)]
        self.run(shots, without_broom=True)
        assert all(s.line is None for s in shots)

    def test_the_pipeline_asks_for_broomless_lines_by_format(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        assert "without_broom=fmt.line_without_broom" in src
```

In `tests/test_timeline.py`, add to `class TestLine`:

```python
    def test_a_broomless_line_publishes_no_broom_and_its_tee_point(self):
        from curling_score.game.linetime import Line
        line = Line(start=(-0.23151, 38.0712), at_hog_x=-0.75712, at_hog_offset=None,
                    at_broom_x=None, miss=None, curl="right", side=None,
                    confirmed=None, hog_path=((28.3461, -0.75712),), path=(),
                    fit_n=41, fit_rms=0.00312, at_tee_x=-1.50049)
        got = self._end(line)["shots"][0]["line"]
        assert got == {"start": {"x": -0.2315, "y": 38.071},
                       "at_hog": {"x": -0.7571, "offset_m": None},
                       "at_broom": None,
                       "side": None, "curl": "right", "confirmed": None,
                       "hog_path": [[28.35, -0.757]], "path": [],
                       "fit": {"n": 41, "rms_m": 0.0031},
                       "at_tee": {"x": -1.5005}}

    def test_a_broom_line_has_no_tee_key(self):
        from curling_score.game.linetime import Line
        line = Line(start=None, at_hog_x=-0.75712, at_hog_offset=None,
                    at_broom_x=-2.35912, miss=-0.71234, curl=None, side=None,
                    confirmed=None, hog_path=(), path=(), fit_n=20, fit_rms=0.01)
        assert "at_tee" not in self._end(line)["shots"][0]["line"]
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_format.py tests/test_linetime.py tests/test_timeline.py`
Expected: FAIL. You should see `AttributeError: ... 'line_without_broom'`, `TypeError: ... unexpected keyword argument 'without_broom'`, `TypeError: cannot unpack non-iterable NoneType` (in `measure`), and `TypeError: ... unexpected keyword argument 'at_tee_x'`.

- [ ] **Step 3: Implement**

`src/curling_score/game/format.py`: in the `GameFormat` dataclass, add this field after `swappable`. Do **not** add it to `to_json`.

```python
    # Does a rock nobody held a broom for still get its thrown line? In
    # doubles the partner is usually sweeping, not holding a broom in the
    # house (phase 0: 44 of 45 rocks of brMO74e6ZZU), so the start, the line,
    # the path and the curl are measured without one. Not in the document:
    # it is what this code does with a format, not a fact about the game.
    line_without_broom: bool = False
```

and in `DOUBLES = GameFormat(...)` add `line_without_broom=True,`.

If `GameFormat` has a `from_json` or a constructor call that passes every field positionally, pass `line_without_broom` by keyword and leave its default for FOURS. Check with `grep -n "GameFormat(" src/curling_score`.

`src/curling_score/game/linetime.py`:
- Add a paragraph to the module docstring, after the first:

```
In doubles the partner is usually sweeping rather than holding a broom in the
house, and a rock nobody held a broom for is still measured: its start, line,
path and curl need none. Only the figures that measure against a broom -- the
offset at the hog line, the miss, wide or narrow -- are None, and the line is
pinned instead by where it crosses the destination tee. Four-player games keep
asking for a broom (``without_broom`` is the game format's call).
```

- `Line`: change the types of `at_broom_x` and `miss` to `float | None`, and add a last field:

```python
    at_broom_x: float | None     # None: nobody held a broom (doubles)
    miss: float | None           # at_broom_x - broom x, signed
    ...
    fit_rms: float
    at_tee_x: float | None = None  # the line's x at the destination tee, only without a broom
```

- Replace `measure` with:

```python
def measure(fit: Fit, track, start, broom, rest=None, path=()) -> Line:
    """Everything the Detail pane says about one rock, from its pieces.

    ``broom`` None is a rock nobody held a broom for (doubles): the offset at
    the hog line, the miss and the side are None, and ``at_tee_x`` pins the
    line in their place."""
    at_hog_x = fit.x(HOG_Y)
    end = rest if rest is not None else ((path[-1][1], path[-1][0]) if path else None)
    curl = curl_of(fit, end)
    if broom is None:
        offset = at_broom_x = miss = side = None
        at_tee_x = fit.x(0.0)
    else:
        bx, by = broom
        offset = None if start is None else at_hog_x - aim_x(start, broom, HOG_Y)
        at_broom_x = fit.x(by)
        miss = at_broom_x - bx
        side = side_of(miss, curl)
        at_tee_x = None
    return Line(start=start, at_hog_x=at_hog_x, at_hog_offset=offset,
                at_broom_x=at_broom_x, miss=miss, curl=curl, side=side,
                confirmed=confirmed_by(list(path), fit),
                hog_path=thin([(y, x) for _t, x, y, _yp in track]),
                path=thin(list(path)), fit_n=fit.n, fit_rms=fit.rms, at_tee_x=at_tee_x)
```

- In `time_lines`: add `without_broom=False` to the keyword-only parameters, and add this sentence to the docstring: "``without_broom`` measures a rock nobody held a broom for too (doubles); otherwise such a rock keeps None." Replace the skip condition and the `measure` call:

```python
        if getattr(shot, "missing", False) or rel is None or crossing is None:
            continue
        if broom is None and not without_broom:
            continue
```

```python
            shot.line = measure(fit, track, start,
                                None if broom is None else (broom.x_m, broom.y_m),
                                rest=_rest(shot), path=path)
```

`src/curling_score/analyze.py`: add the keyword to the `time_lines` call, and extend the comment above it with "In doubles, a rock nobody held a broom for is measured too."

```python
                n_lines = linetime.time_lines(
                    shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
                    sideviews[hogtime.CAMERA_FOR[end.house]], model=line_model,
                    without_broom=fmt.line_without_broom)
```

`src/curling_score/timeline.py`: replace `_line`'s body after the `r = ...` line with:

```python
    out = {"start": None if l.start is None else {"x": r(l.start[0]), "y": r(l.start[1], 3)},
           "at_hog": {"x": r(l.at_hog_x), "offset_m": r(l.at_hog_offset)},
           # null, not absent: a doubles rock nobody held a broom for.
           "at_broom": None if l.at_broom_x is None else {"x": r(l.at_broom_x), "miss_m": r(l.miss)},
           "side": l.side, "curl": l.curl, "confirmed": l.confirmed,
           "hog_path": [[r(y, 2), r(x, 3)] for y, x in l.hog_path],
           "path": [[r(y, 2), r(x, 3)] for y, x in l.path],
           "fit": {"n": int(l.fit_n), "rms_m": r(l.fit_rms)}}
    # Only a broomless line has one; a four-player line is unchanged.
    tee = getattr(l, "at_tee_x", None)
    if tee is not None:
        out["at_tee"] = {"x": r(tee)}
    return out
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_format.py tests/test_linetime.py tests/test_timeline.py tests/test_analyze_write.py tests/test_analyze_side.py tests/test_viewer_js.py`
Expected: PASS. `test_viewer_js.py` runs Python/JS parity over documents, and it must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/format.py src/curling_score/game/linetime.py src/curling_score/analyze.py src/curling_score/timeline.py tests/test_format.py tests/test_linetime.py tests/test_timeline.py
git commit -m "linetime: in doubles a rock nobody held a broom for still gets its line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The viewer shows a broomless line

**Files:**
- Modify: `frontend/core/format.mjs` (add `lineWithoutBroom`)
- Modify: `frontend/core/line.mjs:1-4` (header comment), `:34-39` (`lineX`), `:171-178` (`lineReason`), `:192-240` (`lineFigures`), `:309-362` (`stripGeometry`)
- Modify: `tests/js/singleton.mjs` (a pass-through export only if a test needs one that is missing; `lineX`, `lineFigures`, `lineReason`, `stripGeometry` and `playerHacks` are already there)
- Test: `tests/test_viewer_js.py`
- Rebuild: `src/curling_score/viewer/app.js`, `frontend/.buildstamp.json`

**Interfaces:**
- Consumes: Task 1's JSON. A broomless line has `at_broom: null`, `at_hog: {x, offset_m: null}`, `side: null` and `at_tee: {x}`, and the shot's `target_broom` is null.
- Produces: `lineWithoutBroom(fmt) -> boolean` from `format.mjs` (re-exported by `core/index.mjs`, which already does `export * from "./format.mjs"`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viewer_js.py`, after `class TestStripAndTrack` (which uses `run_js`, `shot` and `TestLineFigures` defined above it):

```python
DOUBLES_FORMAT = {"name": "doubles", "stones_per_team": 6, "placed_per_team": 1,
                  "delivered_per_team": 5, "delivered_per_end": 10,
                  "positions": ["A", "B"], "throw_table": [1, 2, 2, 2, 1],
                  "blank_passes_hammer": True, "swappable": True}
DOUBLES7 = {"schema_version": 7, "format": DOUBLES_FORMAT}


class TestBroomlessLine:
    """A doubles rock nobody held a broom for: its line, pinned at the tee."""

    def broomless(self, **line):
        base = {"start": {"x": -0.23, "y": 38.07},
                "at_hog": {"x": -0.757, "offset_m": None},
                "at_broom": None, "at_tee": {"x": -1.5},
                "side": None, "curl": "right", "confirmed": True,
                "hog_path": [[28.35, -0.757], [25.0, -0.85]],
                "path": [[20.0, -1.0], [1.35, -1.15]], "fit": {"n": 41, "rms_m": 0.003}}
        base.update(line)
        return shot(4, "red", "B", target_broom=None, long_split_s=13.79,
                    delivered_stone_index=0,
                    stones=[{"color": "red", "x": -1.1529, "y": 1.3486}], line=base)

    def figs(self, s, doc=DOUBLES7):
        got = run_js(f"out(lineFigures({json.dumps(s)}, {json.dumps(doc)}));")
        return got, {f["key"]: f for f in got["figures"]}

    def test_the_line_runs_through_the_hog_line_and_the_tee(self):
        # From (-0.757, 28.346) to (-1.5, 0): at the rest's depth 1.3486 the
        # line is at -1.4647, and the rock stopped 0.3118 m to its right.
        assert run_js(f"out(lineX({json.dumps(self.broomless())}, 0));") == pytest.approx(-1.5)
        got, f = self.figs(self.broomless())
        assert got["reason"] is None
        assert (f["curl"]["value"], f["curl"]["note"]) == ("1 ft", "from its line to where it stopped")

    def test_broom_figures_are_dashes_and_the_rest_are_measured(self):
        _, f = self.figs(self.broomless())
        assert (f["broom"]["value"], f["broom"]["note"], f["broom"]["tick"]) == (
            "–", "no broom held in the house", None)
        assert (f["hog"]["value"], f["hog"]["note"]) == ("–", "no broom to aim at")
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "stone set 9 in left of centre")
        assert f["weight"]["value"] == "13.8 s"
        assert f["rest"]["value"] == "12-foot"

    def test_a_doubles_rock_with_no_line_blames_the_camera_not_the_broom(self):
        s = self.broomless()
        s["line"] = None
        got, _ = self.figs(s)
        assert got["reason"] == "The hog-line camera lost this rock"

    def test_a_fours_line_without_a_broom_is_still_refused(self):
        got, f = self.figs(self.broomless(), {"schema_version": 7})
        assert got["reason"] == "No broom was held still before the release"
        assert f["curl"]["value"] == "–"
        g = run_js(f"out(stripGeometry({json.dumps(dict(self.broomless(), line=dict(self.broomless()['line'], at_tee=None)))}));")
        assert g["thrown"] is None

    def test_a_broomless_line_with_nowhere_to_measure_to(self):
        s = self.broomless(path=[])
        s["delivered_stone_index"] = None
        _, f = self.figs(s)
        assert (f["curl"]["value"], f["curl"]["note"]) == ("–", "no rest position")

    def test_a_broomless_line_without_at_tee_draws_no_line(self):
        s = self.broomless(at_tee=None)
        _, f = self.figs(s)
        assert f["curl"]["value"] == "–"
        g = run_js(f"out(stripGeometry({json.dumps(s)}));")
        assert (g["thrown"], g["ext"], g["aim"], g["miss"]) == (None, None, None, None)

    def test_the_strip_draws_the_line_to_the_tee_and_nothing_about_a_broom(self):
        g = run_js(f"out(stripGeometry({json.dumps(self.broomless())}));")
        assert g["broom"] is None and g["aim"] is None and g["miss"] is None
        assert g["thrown"] and g["path"] and g["start"] is not None
        last_y = float(g["ext"].split()[-1].split(",")[1])
        assert last_y == pytest.approx((0 + 2.3) * 420 / 41.2, abs=0.1)

    def test_a_player_s_hack_comes_from_broomless_starts(self):
        a = self.broomless(start={"x": -0.2, "y": 38.07})
        b = self.broomless(start={"x": -0.25, "y": 38.07})
        for s in (a, b):
            s["thrower_slot"] = 2
        got = run_js(f"out(playerHacks({json.dumps([a, b])}));")
        assert got["red|2"]["side"] == "left"
```

If `shot(...)` in this file does not accept `"B"` as a position or sets `thrower_slot` itself, read its definition (search `def shot(` in the file) and pass what it expects. The position string does not affect any of these assertions.

- [ ] **Step 2: Run the tests and see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_viewer_js.py -k TestBroomlessLine`
Expected: FAIL. For example, `lineX` returns null, and `reason` is "No broom was held still before the release".

- [ ] **Step 3: Implement**

`frontend/core/format.mjs`, after `formatOf`:

```js
/* Whether a rock nobody held a broom for still gets its thrown line: doubles
 * only, where the partner is usually sweeping rather than holding a broom in
 * the house. Mirrors GameFormat.line_without_broom, which the document does
 * not carry -- so it goes by the format's name. */
export const lineWithoutBroom = fmt => fmt?.name === "doubles";
```

`frontend/core/line.mjs`:
- Imports: add `import { formatOf, lineWithoutBroom } from "./format.mjs";`.
- Header comment: after "...where the camera behind the thrower saw it go." add "In doubles a rock nobody held a broom for has a line too, pinned where it crosses the tee (`at_tee`) instead of at the broom."
- Replace `lineX`:

```js
/* The thrown line's x at depth y, through the two points the timeline gives:
 * the hog line and the broom -- or, for a doubles rock nobody held a broom
 * for, the hog line and the tee. */
export function lineX(shot, y) {
  const l = shot?.line, b = shot?.target_broom;
  if (!l || l.at_hog?.x == null) return null;
  if (b && l.at_broom?.x != null) {
    const k = (l.at_broom.x - l.at_hog.x) / (b.y - HOG_Y);
    return l.at_hog.x + k * (y - HOG_Y);
  }
  if (!b && l.at_tee?.x != null) {
    const k = (l.at_tee.x - l.at_hog.x) / (0 - HOG_Y);
    return l.at_hog.x + k * (y - HOG_Y);
  }
  return null;
}
```

- In `lineReason`, replace the broom line:

```js
  // A doubles rock nobody held a broom for still has its line.
  if (!shot?.target_broom && !lineWithoutBroom(formatOf(doc))) return "No broom was held still before the release";
```

- Pull the curl figure out of `lineFigures` into a helper above it, and use it in the existing path (`const curl = curlFig(c);`):

```js
const curlFig = c => c?.m != null
  ? fig("curl", "Curl", feetInches(c.m), c.hit ? "from its line to where it hit a stone" : "from its line to where it stopped")
  : fig("curl", "Curl", "–", c?.hit ? "hit a stone before it was seen" : "no rest position");
```

- In `lineFigures`, right after the `if (!l) { ... }` block, add:

```js
  if (!shot.target_broom) {
    // A doubles rock nobody held a broom for: nothing measures against one,
    // but the hack, the weight, the curl and the rest do not need it.
    return { predates, reason, figures: [
      fig("broom", "At the broom", "–", "no broom held in the house"),
      hackFig(shot),
      fig("hog", "At the hog line", "–", "no broom to aim at"),
      weight, curlFig(curlOf(shot)), restFig] };
  }
```

- In `stripGeometry`, replace the guard and its comment:

```js
  // The aim line, the extension to the broom and the miss all measure against
  // the broom. A doubles rock nobody held a broom for still has its thrown
  // line, extended to the tee (`at_tee`), and where it went.
  const broomless = l && !b && l.at_tee?.x != null;
  if (!l || (!b && !broomless)) return { ...sheet, aim: null, thrown: null, ext: null, path: null, start: null, miss: null };
  const hp = (l.hog_path || []).map(([y, x]) => [x, y]);
  const lastY = hp.length ? hp[hp.length - 1][1] : HOG_Y - 3.6;
  if (broomless) {
    return {
      ...sheet, aim: null, miss: null,
      thrown: pts(hp),
      ext: pts([[lineX(shot, lastY), lastY], [lineX(shot, 0), 0]]),
      path: l.path?.length >= 2 ? pts(l.path.map(([y, x]) => [x, y])) : null,
      start: l.start ? pt(l.start.x, l.start.y) : null,
    };
  }
```

Leave the rest of `stripGeometry`, from `const miss = l.at_broom.miss_m;` on, as it is. Delete the two lines it had for `hp` and `lastY`, since they now come before the `broomless` branch.

- [ ] **Step 4: Run the tests, build, and run them again**

```bash
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_viewer_js.py -k "TestBroomlessLine or TestLineFigures or TestStripAndTrack"
cd frontend && npm run build && cd ..
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_viewer_js.py tests/test_frontend_build.py tests/test_timeline.py
```
Expected: all PASS, and `npm run build` lints clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/core/format.mjs frontend/core/line.mjs tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
# and tests/js/singleton.mjs and site.js only if they changed
git commit -m "viewer: a doubles rock nobody held a broom for shows its line, hack and curl

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: On the worker — the four doubles games, the brooms they do have, and four-player parity

**Files:**
- Create: `docs/superpowers/plans/2026-09-26-doubles-line-and-deploy.acceptance.txt`

**Interfaces:**
- Consumes: the branch HEAD after Tasks 1–2, and the phase 3 outputs for comparison. On the laptop those are `/tmp/claude-1000/-home-tcuser-src-curling-score/8ad4ca23-f106-41d7-964f-3701cc57b355/scratchpad/p3r/p3r-<VID>.json`; on the worker they are under `/data/cache/doubles/out/`. Also `scripts/doubles/fours_parity.sh` and `scripts/doubles/compare_docs.py`.
- Produces: the committed acceptance record.

- [ ] **Step 1: Ship HEAD's code to the worker**

```bash
SHA=$(git rev-parse HEAD)
ssh -o BatchMode=yes administrator@10.0.0.182 "mkdir -p /data/wdd/curling/doubles-parity/code-$SHA"
git archive $SHA src | ssh -o BatchMode=yes administrator@10.0.0.182 "tar -x -C /data/wdd/curling/doubles-parity/code-$SHA"
```

- [ ] **Step 2: Analyse the four doubles games with HEAD** (about 15–20 min each, one after another, with `run_in_background`)

For each `VID` in `brMO74e6ZZU n7ifEk4Zfl8 8J3r5FhFFd4 ih59IKFUHXk`:

```bash
ssh -o BatchMode=yes administrator@10.0.0.182 "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml run --rm --no-deps -v /data/wdd/curling/doubles-parity/code-$SHA:/code:ro -e PYTHONPATH=/code/src worker sh -c 'curling-score -v analyze https://www.youtube.com/watch?v=$VID --format doubles --cache-root /data/cache/doubles --out /data/cache/doubles/out/p6-$VID > /data/cache/doubles/out/p6-$VID.log 2>&1'"
ssh -o BatchMode=yes administrator@10.0.0.182 "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml exec -T worker cat /data/cache/doubles/out/p6-$VID/timeline.json" > <scratchpad>/p6-$VID.json
```

- [ ] **Step 3: Measure, per game, and compare with phase 3**

Write a short script, `<scratchpad>/p6_lines.py`, that for each game prints:
- non-missing rocks;
- rocks with a broom line (`target_broom` set, `line.at_broom` set);
- rocks with a broomless line (`target_broom` null, `line.at_tee` set);
- rocks with no line;
- the share of broomless lines that are `confirmed` true, false or null;
- per player (`color|thrower_slot`), the number of rocks with a start and the median start x.

Then check each of the following against the phase 3 output `p3r-<VID>.json`, matching rocks by `t_release_s` within 0.5 s:
- every rock that had a broom line in phase 3 has an identical `line` object now (there are 13: 12 in ih59 and 1 in 8J3r);
- the shot list is unchanged (same count per end, same colours, same `t_enter_s`), since only lines may differ;
- `hammer`, `power_play` and `placement` per end are unchanged.

Also run the phase 3 acceptance check on the new outputs. It must still give 23/23:

```bash
python3 scripts/doubles/placement_acceptance.py datasets/doubles/marks/placements.json <scratchpad>/p6-*.json
```

For reference, measure the same line share on the four-player parity outputs (`/data/wdd/curling/doubles-parity/out/base-a/*/timeline.json`): rocks with a line among non-missing rocks.

- [ ] **Step 4: Hand-check the 13 target brooms against the camera**

For each of the 13 rocks with a broom, save the full composite frame 1.0 s before `t_release_s`. Its long side views include the camera facing the destination house: the end's `house`, seen by `hogtime.CAMERA_FOR[end.house]`. Use ffmpeg in a throwaway container:

```bash
ssh -o BatchMode=yes administrator@10.0.0.182 "mkdir -p /data/wdd/curling/doubles-parity/frames && docker run --rm -v /data/wdd/curling-cache/doubles:/c -v /data/wdd/curling/doubles-parity/frames:/o --entrypoint ffmpeg curling-worker:local -v error -ss <t-1> -i /c/videos/<VID>.mp4 -frames:v 1 -q:v 3 /o/<VID>-e<end>-r<rock>.jpg"
scp administrator@10.0.0.182:/data/wdd/curling/doubles-parity/frames/*.jpg <scratchpad>/frames/
```

Look at each image (the Read tool shows images), then record in the acceptance file whether a person is holding a broom still in the destination house near the recorded spot. If any turns out not to be a held broom, report it; do not change code in this task.

Pick 5 broomless rocks, spread over games and both teams, and save the same camera's frame at `t_release_s − 1.0`. Confirm that nobody is holding a broom in the house for them.

- [ ] **Step 5: Four-player parity (full pipeline, 3 videos)**

The base outputs from the phases 1–2 check are on the worker under `/data/wdd/curling/doubles-parity/out/{base-a,base-b,base-nl-a,base-nl-b}`.

```bash
bash scripts/doubles/fours_parity.sh run p6-head-nl $SHA --no-longview
bash scripts/doubles/fours_parity.sh run p6-head $SHA
ssh -o BatchMode=yes administrator@10.0.0.182 "docker run --rm -v /data/wdd/curling/doubles-parity/out:/mnt --entrypoint chmod curling-worker:local -R a+rX /mnt"
bash scripts/doubles/fours_parity.sh compare base-nl-a p6-head-nl      # identical on all three
bash scripts/doubles/fours_parity.sh compare base-a p6-head
bash scripts/doubles/fours_parity.sh compare base-b p6-head             # one of the two identical per video
```

Also check that no four-player line has an `at_tee` key and every one has a non-null `at_broom`:

```bash
ssh -o BatchMode=yes administrator@10.0.0.182 "python3 -c \"import json,glob; bad=[(p,s['number']) for p in glob.glob('/data/wdd/curling/doubles-parity/out/p6-head*/*/timeline.json') for g in json.load(open(p))['games'] for e in g['ends'] for s in e['shots'] if s.get('line') and ('at_tee' in s['line'] or s['line'].get('at_broom') is None)]; print(len(bad), bad[:5])\""
```
Expected: `0 []`.

- [ ] **Step 6: Write and commit the record**

Write `docs/superpowers/plans/2026-09-26-doubles-line-and-deploy.acceptance.txt` with:
- the per-game line counts;
- the phase 3 comparisons: the 13 broom lines, the shot lists, hammer and power play, and the 23/23;
- the four-player line share for reference;
- the broom hand-check with one line per rock, and the 5 broomless checks;
- the parity results, verbatim;
- the no-`at_tee` check.

Targets:
- all 13 broom lines identical;
- shot lists, hammer, power play and placement identical;
- 23/23;
- parity as in the Global Constraints;
- no four-player `at_tee`;
- broomless lines on at least 70% of non-missing doubles rocks that have no broom.

If a target is missed, do not tune code in this task. Report DONE_WITH_CONCERNS with the specifics.

```bash
git add docs/superpowers/plans/2026-09-26-doubles-line-and-deploy.acceptance.txt
git commit -m "doubles: broomless lines on four games, the brooms checked, and four-player parity

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4 (controller): the 16-game four-player harness, deployed commit against head

This runs after the branch is merged to main. It covers the shot lists of 16 hosted four-player games, which Task 3's three videos do not. `BASE=a63495b` is the pipeline the worker runs today. `HEAD` is main after the merge.

- [ ] **Step 1: Code for both commits on the worker**

```bash
for c in a63495b $(git rev-parse main); do
  ssh -o BatchMode=yes administrator@10.0.0.182 "mkdir -p /data/wdd/curling/ds15/games/code-$c"
  git archive $c src scripts | ssh -o BatchMode=yes administrator@10.0.0.182 "tar -x -C /data/wdd/curling/ds15/games/code-$c"
done
```

- [ ] **Step 2: Replay the 16 games at each commit, with the deployed overhead model**

The model is `ds15a`. Find its file in `/data/wdd/curling_score/weights/` (the worker image's copy); that path is `$W` below. On the worker, for each commit `c`, split `timelines/*.json` into 4 streams. In each stream, for each timeline `tl`:

```bash
B=/data/wdd/curling/ds15/games
CURLING_SCORE_CACHE=$B/root PYTHONPATH=$B/code-$c/src timeout 5400 \
  /data/wdd/curling/.venv/bin/python $B/code-$c/scripts/phase3/split_report.py $tl \
  --cache-root $B/root --video $B/root/videos/$vid.mp4 --before $B/empty.json \
  --weights $W --out $B/out-parity/$c/$name.json > $B/out-parity/$c/$name.log 2>&1
```

Here `vid` is `source.video_id` of `tl`, and `name` is the file's base name. Run the base commit first, then head, as background jobs, and wait for the notices.

- [ ] **Step 3: Compare**

For each game, `cmp` the two JSON outputs. For any that differ, list the per-end shot lists `[(t_enter, color)]` side by side, matching rocks by time (never by shot number). Parity means byte-identical outputs, or differences that the base itself shows between two runs of the same commit. Check the latter by rerunning base on a differing game before ruling.

Record the result in the acceptance file (append a "16-game harness" section) and commit it on main.

---

### Task 5 (controller): deploy, turn doubles on, and chart one doubles game live

- [ ] **Step 1: Turn doubles on in Cloud Run's config (on main)**

In `deploy/cloudrun.yaml`, after the `DOUBLES_ENABLED` comment block, add:

```yaml
            - { name: DOUBLES_ENABLED,    value: "1" }
```

Edit the comment's last sentence to say the setting is on now that the viewer charts doubles games. Commit it on main with only that file staged.

- [ ] **Step 2: A clean deploy worktree of main**

```bash
SHA=$(git rev-parse --short main)
git worktree add --detach .claude/worktrees/deploy-$SHA main
cp firebase_api_key.json .claude/worktrees/deploy-$SHA/
```

- [ ] **Step 3: The worker**

```bash
cd .claude/worktrees/deploy-$SHA
rsync -a --exclude 'deploy/worker.env' --exclude .git --exclude .venv --exclude node_modules --exclude 'out*' ./ administrator@10.0.0.182:/data/wdd/curling_score/
ssh -o BatchMode=yes administrator@10.0.0.182 "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml up -d --build"
```

Wait for `docker compose ps` to show `Up N seconds`. Then confirm the running container has the new code:

```bash
ssh -o BatchMode=yes administrator@10.0.0.182 "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml exec -T worker python -c 'from curling_score.game import format as F; print(F.DOUBLES.line_without_broom)'"
```
Expected: `True`.

- [ ] **Step 4: The API**

In the deploy worktree, run the script's `MODEL_ID` one-liner and compare it with the live service's `MODEL_ID` (`~/google-cloud-sdk/bin/gcloud run services describe curling-chart --region us-west1 --project curling-stats-508323`). Then:

```bash
PATH=$HOME/google-cloud-sdk/bin:$PATH PROJECT_ID=curling-stats-508323 REGION=us-west1 PUBLIC_BASE_URL=https://curling.dimmit.net ./deploy/deploy-api.sh
```

Check for `accounts: sign-in is live`. If the tool call is refused, give the user this exact command, with the deploy worktree as the working directory, to run with `!`.

After it lands:
- `curl -s https://curling.dimmit.net/api/features` must give `{"doubles":true}`;
- the live `app.js` hash must match `frontend/.buildstamp.json`.

- [ ] **Step 5: One doubles game, submitted live, without a new download**

Use `8J3r5FhFFd4` (4/2, sheet 3: seven clean ends and one held broom). The worker would otherwise download it again. Hard-link the cached copy and its proxy into the worker's cache instead, on the same filesystem:

```bash
ssh -o BatchMode=yes administrator@10.0.0.182 "cd /data/wdd/curling-cache && ls doubles/videos/8J3r5FhFFd4.mp4 doubles/proxies/ | head; ln -n doubles/videos/8J3r5FhFFd4.mp4 videos/8J3r5FhFFd4.mp4"
```

Link the proxy too, if `doubles/proxies/` holds one for this video under the name `proxies/` uses.

Submit it:

```bash
curl -s -X POST https://curling.dimmit.net/api/submissions -H 'Content-Type: application/json' \
  -d '{"url": "https://www.youtube.com/watch?v=8J3r5FhFFd4", "format": "doubles"}'
```

Follow the run until it is done, using the status link or id the response gives. Watch the worker log for "downloading"; there should be none. Then fetch the chart's timeline and check:
- `format.name` is `"doubles"`;
- ten shots per end;
- `hammer` and `power_play` per end agree with Task 3's `p6-8J3r5FhFFd4.json`;
- lines are present (broomless, plus the one broom line).

Also open the chart headless at 390×844 once, using the `headless-phone-check` recipe, and confirm the labels, the End box and a Detail pane with a broomless line.

- [ ] **Step 6: Record and clean up**

Append a "Deploy" section to the acceptance file with:
- the deployed commit;
- the worker check;
- `/api/features`;
- the live `app.js` hash;
- the submission's run id and chart link;
- the checks from Step 5.

Commit it on main. Remove the deploy worktree with `git worktree remove`.

---

## Left for later

- Reading the guard from the long camera when it is above the panel (phase 0 never saw it).
- Final-review residual I1 from phase 3: a guard never seen at all.
- The thin format-check margin (n7if's median of 13 against the cut-off of 14).
- The post-game practice segmented as a second game.
- A dismiss control for a format warning over the house on a phone edit link.
- The charting transport shown in landscape watch mode (older than this work).
