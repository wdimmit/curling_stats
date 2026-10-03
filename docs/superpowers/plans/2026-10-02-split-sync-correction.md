# Split sync correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Published hog-to-hog splits are corrected, shot by shot, for the
offsets between the composite's four camera feeds. This holds live and on
reprocess, and the raw split and the readings stay in the timeline.

**Architecture:**
- **The read.** One shared read after the hog line decodes each frame once and
  crops both long cameras. It is stored on the shot as `dest_read`.
- **Readings.** `game/sync.py` turns that read into two per-shot readings: far
  hog line (arriving panel − camera behind the thrower) and mid-sheet (hog
  camera − camera behind the thrower).
- **Correction.** `sync.correct(document)` runs inside `timeline.build_document`.
  It fits the mid-sheet role term per recording, from ends thrown both ways,
  and rewrites `long_split_s` from `long_split_raw_s`. Live publishes call the
  same function, so earlier ends refine as later ends arrive.

**Tech Stack:** Python 3.12, numpy, ffmpeg (filter_complex), the ds13c side
model through `detect/sidemodel.py`, and pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-split-sync-correction-design.md`
(main 4d98c20). Read it first. The study it rests on is in
`~/curling-work/sync-1001`.

## Global Constraints

**Working setup**
- Work on branch `split-sync` in worktree `.claude/worktrees/split-sync`.
  Another Claude session shares the main checkout, so never commit from it and
  stage only files you changed.
- Run tests from the worktree with
  `/home/tcuser/src/curling_score/.venv/bin/python -m pytest <files>`.
  pytest's `pythonpath = ["src"]` puts the worktree's code first. Never run the
  whole suite (it OOMs this box; exit 137). Don't add `-q`, since `addopts`
  already has it. Deselect `tests/test_longview.py::TestAgainstHandMarkedCrossings`
  wherever it is collected, because it fails on main already.
- Commit messages follow the repo's style (`area: sentence`) and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**What must not change**
- Nothing may add, drop, renumber or re-time a shot. Only split fields, the new
  `sync` blocks, and `draw_through`/`flashed` flips may change.
- `SCHEMA_VERSION` stays 8. `PIPELINE_VERSION` bumps once, in Task 7.

**Readings**
- Mid-sheet span: y ∈ [13.0, 21.75] m from the playing tee, levels every
  0.25 m, at least 5 levels, span at least 2.0 m.
- Hog-camera rows: `row_for(13.0) − 40` to `row_for(21.75) + 20`, clipped to the
  view. Rows above `PATH_SPLIT_ROW` (640) are read at imgsz 800, below at 416,
  conf 0.3, 5 fps (`PATH_FPS`), the rock's colour only.
- Far-hog fit: the trailing edge (box bottom) over 5.5–8.5 m from the playing
  tee. Crossing = `t_trail + c − 2·STONE_RADIUS_M / v`. `STONE_RADIUS_M` is
  0.142, so the diameter is 0.284 m (the spec says so too; 0.291 appears only
  as `path_points`' width-check constant). `c` is 0 until Task 9.
- Panel far crossing used only when `far_reach` ≤ 0.05.

**Correction**
- Reference speed 1.6 m/s.
- Defaults until a pair exists: old framing R0 0.20 s and S 0.22 m; re-aimed
  R0 0.56 s and S 0.36 m. Re-aimed = every side view's `tee_row` < 430.
- Running median over 5 shots (±2). Drop readings over 0.3 s from the window
  median.
- Fewer than 3 usable readings in an end → borrow; none → `uncorrected`. MAD
  over 0.08 s → `low_confidence`.

## Review Focus

1. **No side views, or no lateral calibration** (a calibration failure, or an
   old recording): splits publish raw, marked `uncorrected`, and nothing
   raises. Tested in Task 6
   (`test_no_side_views_leaves_splits_raw_and_uncorrected`) and Task 7
   (`test_build_document_without_side_views_keeps_raw_splits`).
2. **Live end 1, or any one-direction document:** the framing default is used
   and marked `default`. When a second end arrives, end 1's numbers are
   recomputed from stored raw fields. Tested in Task 6
   (`test_one_direction_uses_the_framing_default`,
   `test_end_one_is_recomputed_once_end_two_arrives`).
3. **Two ends in a row to the same house** (segmentation joins and cuts happen,
   e.g. 10/01 Morning sheet 2): never used as a role-term pair, and no crash.
   Tested in Task 6 (`test_adjacent_same_house_ends_are_not_a_pair`).
4. **A shot with no reading of its own** (its far crossing was reached for, or
   the stone died before mid-sheet): it takes its window's value and its
   `cam_split_s` is null. Tested in Task 5
   (`test_a_reached_for_far_crossing_gives_no_far_reading`) and Task 6
   (`test_a_shot_without_readings_takes_its_windows_value`).
5. **Doubles ends of 10 rocks** with few readings: borrow from the nearest end
   to the same house, marked `borrowed`. Tested in Task 6
   (`test_a_thin_end_borrows_from_the_nearest_end_to_the_same_house`).

---

### Task 0: Worktree

**Files:** none

- [ ] **Step 1: Create the worktree from main**

```bash
cd /home/tcuser/src/curling_score
git worktree add .claude/worktrees/split-sync -b split-sync main
cd .claude/worktrees/split-sync && git log --oneline -1
```

Expected: the branch is at 4d98c20 or later, with the spec present.

- [ ] **Step 2: Check the tests run from the worktree**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_mark_hog.py tests/test_linetime.py`
Expected: PASS.

---

### Task 1: Marking tool, receding and arriving modes

The hand-mark round (spec section 5) needs `scripts/mark_hog.py` to show the
FAR hog line in two ways:
- **receding:** the camera behind the thrower; the stone moves away from it.
- **arriving:** the arriving overhead panel.

Crops come from a served timeline's `calibration` block. Two helper scripts
pick the rocks and fold the marks into the dataset.

**Files:**
- Modify: `scripts/mark_hog.py`
- Create: `scripts/sync/mark_events.py`
- Create: `scripts/sync/collect_marks.py`
- Test: `tests/test_mark_hog.py` (extend)
- Test: `tests/test_sync_marks.py` (new)

**Interfaces:**
- Produces:
  - `mark_hog.crop_for(view_name, view=None, above=CROP_ROWS_ABOVE, below=CROP_ROWS_BELOW)`
  - `mark_hog.panel_crop(panel: dict) -> (y0, y1, x0, x1)`
  - `mark_hog.event_view(mode, house, panel) -> str`
  - `mark_hog.event_crop(mode, view_name, cal) -> tuple | None`
  - `mark_hog.MODES`, `MODE_WINDOWS`, `INSTRUCTIONS`
  - `mark_events.candidates(doc) -> list[dict]`
  - `mark_events.pick(cands, per_direction) -> list[dict]`
  - `collect_marks.collect(receding, arriving) -> dict[video_id, dict]`
- Event dicts carry `video, end, shot, house, color, t, kind, label`. `t` is
  the served `t_enter_s`, the panel's first sighting, which sits at the far
  hog crossing within about 1 s.

- [ ] **Step 1: Write the failing tests for the tool**

Append to `tests/test_mark_hog.py`:

```python
CAL = {
    "top": {"rect": [807, 10, 300, 534],
            "hog_line": {"outer_edge_row_coef": [-0.00076266, 0.24660555, 494.94534577]}},
    "bottom": {"rect": [807, 554, 300, 516],
               "hog_line": {"outer_edge_row_coef": [0.00081267, -0.27146739, 39.5015641]}},
    "left": {"rect": [0, 0, 807, 1080], "tee_row": 472.25, "hog_row": 559.0},
    "right": {"rect": [1107, 0, 813, 1080], "tee_row": 482.55, "hog_row": 570.0},
}


class TestFarHogModes:
    """The camera-sync calibration marks the FAR hog line twice per rock: from
    behind the thrower (the stone going away) and in the arriving panel."""

    def test_receding_is_the_camera_behind_the_thrower(self):
        # Played to the bottom house: thrown from the top end, whose camera
        # (the right one) looks down the sheet at the bottom house.
        assert mark_hog.event_view("receding", "bottom", None) == "right"
        assert mark_hog.event_view("receding", "top", None) == "left"

    def test_arriving_is_the_panel_over_the_house_played_to(self):
        assert mark_hog.event_view("arriving", "bottom", None) == "bottom"

    def test_throwing_keeps_its_old_meaning(self):
        assert mark_hog.event_view("throwing", None, "top") == "left"

    def test_a_receding_crop_has_more_ice_below_the_line(self):
        """The stone comes up from below the paint, toward the far house."""
        y0, y1, _x0, _x1 = mark_hog.event_crop("receding", "right", CAL)
        assert y0 < 570 < y1
        assert (y1 - 570) > (570 - y0)

    def test_an_arriving_crop_straddles_the_panels_hog_row_inside_the_panel(self):
        y0, y1, x0, x1 = mark_hog.event_crop("arriving", "bottom", CAL)
        hog = 554 + 39.5015641 - 0.27146739 * 150 + 0.00081267 * 150 ** 2
        assert y0 < hog < y1
        assert 554 <= y0 and y1 <= 554 + 516
        assert (x0, x1) == (807, 807 + 300)

    def test_a_crop_near_the_panels_far_edge_is_clipped_to_the_panel(self):
        y0, y1, _x0, _x1 = mark_hog.event_crop("arriving", "top", CAL)
        assert y1 <= 10 + 534

    def test_each_mode_has_its_own_window_and_words(self):
        assert mark_hog.MODE_WINDOWS["throwing"] == mark_hog.WINDOW_S
        assert mark_hog.MODE_WINDOWS["receding"][0] < 0 < mark_hog.MODE_WINDOWS["receding"][1]
        assert "bottom of the stone" in mark_hog.INSTRUCTIONS["receding"]
        assert "leading edge" in mark_hog.INSTRUCTIONS["arriving"]

    def test_the_page_carries_the_modes_words(self):
        assert mark_hog.INSTRUCTIONS["receding"] in mark_hog.page_for("receding")
        assert "%INSTRUCTIONS%" not in mark_hog.page_for("throwing")
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_mark_hog.py`
Expected: FAIL with `AttributeError: module 'mark_hog' has no attribute 'event_view'`.

- [ ] **Step 3: Implement the modes in `scripts/mark_hog.py`**

Below `CROP_ROWS_ABOVE, CROP_ROWS_BELOW = 50, 50`, add:

```python
# The receding stone comes up the frame from below the paint toward the far
# house, so its crop has more ice under the line than over it.
RECEDING_ROWS_ABOVE, RECEDING_ROWS_BELOW = 40, 70
# Rows either side of an overhead panel's painted hog line.
PANEL_ROWS = 50
```

Replace `crop_for` with:

```python
def crop_for(view_name: str, view=None, above: int = CROP_ROWS_ABOVE,
             below: int = CROP_ROWS_BELOW):
    """``(y0, y1, x0, x1)`` in FULL-FRAME coordinates for the scrubber.

    With a fitted ``view`` the rows follow its own hog row and the columns
    follow its rect, narrowed to the middle where a delivery actually travels;
    the outer thirds are the neighbouring sheets and the wall.
    """
    if view is None:
        return CROPS[view_name]
    x, _y, w, _h = view.rect
    hog = int(round(view.hog_row))
    return (hog - above, hog + below, x + int(w * 0.20), x + int(w * 0.80))


def panel_crop(panel: dict):
    """``(y0, y1, x0, x1)`` around an overhead panel's painted hog line, from a
    timeline's ``calibration`` block. The paint's row is read at the panel's
    middle column, and the crop never leaves the panel, so the other panel
    never shows."""
    x, y, w, h = (int(v) for v in panel["rect"])
    row = int(round(float(np.polyval(panel["hog_line"]["outer_edge_row_coef"], w / 2.0))))
    return (y + max(0, row - PANEL_ROWS), y + min(h, row + PANEL_ROWS), x, x + w)
```

After `WINDOW_S = (1.5, 7.5)`, add:

```python
# "throwing" is the original: a delivery crossing its own end's hog line, seen
# by the camera at the other end. The other two serve the camera-sync
# calibration (docs/superpowers/specs/2026-10-02-split-sync-correction-design.md,
# section 5): the same rock at the FAR hog line, from behind the thrower and
# from above in the arriving panel. Their events are anchored on the panel's
# first sighting, which sits at that crossing to within about a second.
MODES = ("throwing", "receding", "arriving")
MODE_WINDOWS = {"throwing": WINDOW_S, "receding": (-1.5, 2.0), "arriving": (-1.5, 2.0)}
INSTRUCTIONS = {
    "throwing": ("Step until the <em>leading edge of the stone</em> first touches the "
                 "near edge of the paint, then mark."),
    "receding": ("The stone is moving away from the camera. Step until the <em>bottom of "
                 "the stone</em>, where it meets the ice nearest you, has just cleared the "
                 "near edge of the paint, so ice shows between them, then mark."),
    "arriving": ("Seen from above. Step until the <em>leading edge of the stone</em>, the "
                 "side heading into the house, first touches the hog line's paint, then "
                 "mark."),
}


def event_view(mode: str, house, panel) -> str:
    """Which part of the frame an event is marked in. ``house`` is the house
    the rock was played to; ``panel`` the throwing panel (throwing mode)."""
    if mode == "throwing":
        return CAMERA_FOR[panel]
    if mode == "receding":
        return CAMERA_FOR[house]          # at the throwing end, facing that house
    return house                          # the arriving panel


def event_crop(mode: str, view_name: str, cal):
    """The scrubber's crop from a timeline's calibration block, or None to use
    the old measured window (throwing mode without a calibration)."""
    if cal is None:
        if mode != "throwing":
            raise SystemExit(f"--timeline is needed for {mode} mode")
        return None
    if mode == "arriving":
        return panel_crop(cal[view_name])
    from curling_score.geometry.sideview import SideView

    c = cal[view_name]
    view = SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"])
    if mode == "receding":
        return crop_for(view_name, view, RECEDING_ROWS_ABOVE, RECEDING_ROWS_BELOW)
    return crop_for(view_name, view)
```

In `PAGE`, replace the sentence
`Step until the\n <em>leading edge of the stone</em> first touches the near edge of the paint,\n then mark.`
with `%INSTRUCTIONS%`. Also replace the `who` line in `show()` with

```javascript
  document.getElementById('who').textContent=
    d.color+' stone, '+(d.label||('released '+d.release.toFixed(1)+' s'));
```

and add:

```python
def page_for(mode: str) -> str:
    return PAGE.replace("%INSTRUCTIONS%", INSTRUCTIONS[mode])
```

Change `serve(root: Path, port: int)` to `serve(root: Path, port: int, mode: str = "throwing")`,
and in `do_GET` use `body, ctype = page_for(mode).encode(), "text/html"`.

In `main()`:
- Make `--events`, `--video` and `--panel` `required=False`.
- Add `ap.add_argument("--mode", choices=MODES, default="throwing")` and
  `ap.add_argument("--timeline", help="a served timeline.json; crops come from its calibration")`.
- Replace everything from `root = Path(args.out)` to the end of `main` with:

```python
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    if args.mode == "throwing" and not args.prepared and args.panel is None:
        raise SystemExit("--panel is needed for throwing mode")
    cal = None
    if args.timeline:
        cal = json.loads(Path(args.timeline).read_text())["calibration"]
    banked = None
    if args.views:
        from curling_score.harvest import sideviews as SV

        vid = args.video_id or Path(args.video).stem
        banked = dict(SV.usable_views(SV.from_json(json.loads(Path(args.views).read_text())[vid])))
    if not args.prepared:
        if args.events is None or args.video is None:
            raise SystemExit("--events and --video are needed unless --prepared")
        events = json.loads(Path(args.events).read_text())
        window = tuple(args.window) if args.window else MODE_WINDOWS[args.mode]
        print(f"  {args.mode}: scrubber window {window[0]} .. {window[1]} s about each event")
        index = []
        for n, e in enumerate(events):
            view = event_view(args.mode, e.get("house"), args.panel)
            crop = (crop_for(view, banked[view]) if banked is not None and args.mode == "throwing"
                    else event_crop(args.mode, view, cal))
            t0, t1 = e["t"] + window[0], e["t"] + window[1]
            print(f"  extracting {n + 1}/{len(events)}: {e['color']} at {e['t']:.1f} ({view})")
            frames = extract(args.video, view, t0, t1, root, f"d{n:02d}", crop=crop)
            index.append({"id": f"d{n:02d}", "color": e["color"], "release": e["t"],
                          "view": view, "mode": args.mode, "frames": frames,
                          # Carried so a mark can be traced back to the shot it
                          # came from without re-deriving it from the timestamp.
                          **{k: e[k] for k in ("video", "end", "shot", "house", "label",
                                               "kind", "note") if k in e}})
        (root / "index.json").write_text(json.dumps(index, indent=1))
        if not (root / "marks.json").exists():
            (root / "marks.json").write_text("{}")
    if args.extract_only:
        print(f"  extracted to {root}; serve it elsewhere with --prepared")
        return
    serve(root, args.port, args.mode)
```

- [ ] **Step 4: Run the tool tests**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_mark_hog.py`
Expected: PASS, including the five existing `TestCropFollowsTheCalibration` tests.

- [ ] **Step 5: Write the failing tests for the helpers**

Create `tests/test_sync_marks.py`:

```python
"""The camera-sync hand-mark round's helpers, loaded by path like mark_hog."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "scripts" / "sync"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mark_events = load("mark_events")
collect_marks = load("collect_marks")


def shot(n, t, kind="draw", split=14.0, reach=0.0, missing=False, color="red"):
    return {"number": n, "color": color, "t_enter_s": t, "missing": missing,
            "shot_type": kind, "long_split_s": split, "long_split_far_reach_u": reach}


def doc(ends):
    return {"source": {"video_id": "vid"},
            "games": [{"ends": [{"number": i + 1, "house": h, "shots": s}
                                for i, (h, s) in enumerate(ends)]}]}


class TestCandidates:
    def test_only_rocks_with_a_split_the_panel_saw_qualify(self):
        d = doc([("top", [shot(1, 10.0), shot(2, 20.0, split=None),
                          shot(3, 30.0, reach=0.1), shot(4, 40.0, missing=True)])])
        got = mark_events.candidates(d)
        assert [c["shot"] for c in got] == [1]
        assert got[0] | {"label": None} == {
            "video": "vid", "end": 1, "shot": 1, "house": "top", "color": "red",
            "t": 10.0, "kind": "draw", "label": None}

    def test_hits_flashes_and_draws_through_count_as_hits(self):
        d = doc([("top", [shot(1, 1.0, "hit"), shot(2, 2.0, "flashed"),
                          shot(3, 3.0, "draw_through"), shot(4, 4.0, "guard")])])
        assert [c["kind"] for c in mark_events.candidates(d)] == ["hit", "hit", "hit", "draw"]


class TestPick:
    def cands(self):
        out = []
        for house in ("top", "bottom"):
            for i in range(20):
                out.append({"house": house, "t": float(i) + (0 if house == "top" else 100),
                            "kind": "hit" if i % 3 == 0 else "draw", "shot": i})
        return out

    def test_each_direction_gets_its_share_mixing_draws_and_hits(self):
        got = mark_events.pick(self.cands(), 7)
        for house in ("top", "bottom"):
            mine = [c for c in got if c["house"] == house]
            assert len(mine) == 7
            assert sum(c["kind"] == "hit" for c in mine) == 3

    def test_the_pick_spreads_across_the_game(self):
        top = [c for c in mark_events.pick(self.cands(), 7) if c["house"] == "top"]
        ts = sorted(c["t"] for c in top)
        assert ts[0] < 5 and ts[-1] > 14

    def test_a_short_supply_of_hits_is_topped_up_with_draws(self):
        cands = [c for c in self.cands() if c["kind"] == "draw" or c["shot"] == 0]
        top = [c for c in mark_events.pick(cands, 7) if c["house"] == "top"]
        assert len(top) == 7 and sum(c["kind"] == "hit" for c in top) == 1


class TestCollect:
    def test_both_marks_of_a_rock_are_one_row_and_a_skip_stays_null(self):
        rec = ([{"id": "d00", "video": "vid", "end": 2, "shot": 5, "house": "top",
                 "color": "red", "view": "left", "release": 100.0}], {"d00": 101.2})
        arr = ([{"id": "d00", "video": "vid", "end": 2, "shot": 5, "house": "top",
                 "color": "red", "view": "top", "release": 100.0}], {"d00": None})
        got = collect_marks.collect(rec, arr)
        rock = got["vid"]["rocks"][0]
        assert rock["receding_s"] == 101.2 and rock["arriving_s"] is None
        assert rock["receding_view"] == "left" and rock["arriving_view"] == "top"
        assert rock["anchor_s"] == 100.0

    def test_an_unmarked_rock_is_left_out(self):
        rec = ([{"id": "d00", "video": "v", "end": 1, "shot": 1, "house": "top",
                 "color": "red", "view": "left", "release": 5.0}], {})
        assert collect_marks.collect(rec, ([], {}))["v"]["rocks"] == []
```

- [ ] **Step 6: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync_marks.py`
Expected: FAIL with `FileNotFoundError` for `scripts/sync/mark_events.py`.

- [ ] **Step 7: Write `scripts/sync/mark_events.py`**

```python
#!/usr/bin/env python
"""Pick the rocks for the camera-sync hand-mark round from served timelines.

    python scripts/sync/mark_events.py TL.json [TL2.json ...] --per-direction 7 --out ev.json

All timelines must be of one recording, e.g. its two games. A rock qualifies
when it has a split whose far crossing the panel saw (reach 0) and an entry
time. The pick spreads over the recording, in both throwing directions, and
mixes draws with hits so both speeds get marked. ``t`` is the panel's first
sighting, which ``mark_hog.py``'s receding and arriving windows are about.
"""
import argparse
import json
from pathlib import Path

HITS = {"hit", "flashed", "draw_through"}


def candidates(doc) -> list[dict]:
    vid = doc["source"]["video_id"]
    out = []
    for game in doc["games"]:
        for end in game["ends"]:
            for s in end["shots"]:
                if s.get("missing") or s.get("long_split_s") is None or s.get("t_enter_s") is None:
                    continue
                if (s.get("long_split_far_reach_u") or 0.0) > 0.0:
                    continue
                out.append({"video": vid, "end": end["number"], "shot": s["number"],
                            "house": end["house"], "color": s["color"], "t": s["t_enter_s"],
                            "kind": "hit" if s.get("shot_type") in HITS else "draw",
                            "label": f"end {end['number']} rock {s['number']} ({s.get('shot_type')})"})
    return out


def spread(items, n):
    """``n`` of ``items`` evenly across them (all of them when there are fewer)."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step + step / 2)] for i in range(n)]


def pick(cands, per_direction) -> list[dict]:
    out = []
    for house in ("top", "bottom"):
        pool = sorted((c for c in cands if c["house"] == house), key=lambda c: c["t"])
        draws = [c for c in pool if c["kind"] == "draw"]
        hits = [c for c in pool if c["kind"] == "hit"]
        n_hits = min(len(hits), per_direction // 2)
        n_draws = min(len(draws), per_direction - n_hits)
        n_hits = min(len(hits), per_direction - n_draws)
        out += sorted(spread(draws, n_draws) + spread(hits, n_hits), key=lambda c: c["t"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("timelines", nargs="+")
    ap.add_argument("--per-direction", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cands = [c for p in args.timelines for c in candidates(json.loads(Path(p).read_text()))]
    events = pick(cands, args.per_direction)
    Path(args.out).write_text(json.dumps(events, indent=1))
    print(f"{len(events)} rocks of {len(cands)} candidates -> {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Write `scripts/sync/collect_marks.py`**

```python
#!/usr/bin/env python
"""Fold a receding round and an arriving round into the hand-mark dataset.

    python scripts/sync/collect_marks.py RECEDING_DIR ARRIVING_DIR --out datasets/hogmarks

Each directory is a ``mark_hog.py`` output (``index.json`` + ``marks.json``)
over the same events. Writes ``<video>-receding.json`` per video. A mark of
null is a rock the person could not call; a rock marked in neither round is
left out.
"""
import argparse
import json
from datetime import date
from pathlib import Path

WHAT = ("Far hog line, hand-marked twice per rock for the camera-sync split "
        "correction (docs/superpowers/specs/2026-10-02-split-sync-correction-design.md "
        "section 5). receding_s: the frame the stone's bottom (trailing edge) cleared "
        "the near edge of the paint, in the long camera behind the thrower. "
        "arriving_s: the frame its leading edge touched the paint, in the arriving "
        "overhead panel. anchor_s is the served t_enter_s the windows were cut about.")


def collect(receding, arriving) -> dict:
    rows = {}
    for (index, marks), side in ((receding, "receding"), (arriving, "arriving")):
        for d in index:
            key = (d["video"], d["end"], d["shot"])
            row = rows.setdefault(key, {"video": d["video"], "end": d["end"], "shot": d["shot"],
                                        "house": d["house"], "color": d["color"],
                                        "anchor_s": d["release"],
                                        "receding_view": None, "receding_s": None,
                                        "arriving_view": None, "arriving_s": None,
                                        "_marked": False})
            row[f"{side}_view"] = d["view"]
            if d["id"] in marks:
                row[f"{side}_s"] = marks[d["id"]]
                row["_marked"] = True
    out = {}
    for row in sorted(rows.values(), key=lambda r: r["anchor_s"]):
        vid = row["video"]
        out.setdefault(vid, {"video_id": vid, "what": WHAT,
                             "marked_on": date.today().isoformat(), "rocks": []})
        if row.pop("_marked"):
            out[vid]["rocks"].append({k: v for k, v in row.items() if k != "video"})
    return out


def load(d):
    d = Path(d)
    return json.loads((d / "index.json").read_text()), json.loads((d / "marks.json").read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("receding")
    ap.add_argument("arriving")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    for vid, data in collect(load(args.receding), load(args.arriving)).items():
        path = Path(args.out) / f"{vid}-receding.json"
        path.write_text(json.dumps(data, indent=1))
        print(f"{len(data['rocks'])} rocks -> {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Run both test files**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_mark_hog.py tests/test_sync_marks.py`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add scripts/mark_hog.py scripts/sync/mark_events.py scripts/sync/collect_marks.py tests/test_mark_hog.py tests/test_sync_marks.py
git commit -m "mark_hog: receding and arriving modes for the far hog line, and the sync round's rock picker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The marking round (the user marks; the worker cuts frames)

This is operational, and it needs the user. It can run while Tasks 3–7 are
built.

**Files:**
- Create: `datasets/hogmarks/0SWB4g3SJoE-receding.json`, `32Dkqsf2t2M-receding.json`, `dea75KXakFc-receding.json`

- [ ] **Step 1: Fetch the current served timelines of the three recordings**

All three have been reprocessed since 10/01, so take the newest ready sources.

```bash
mkdir -p ~/curling-work/marks && cd ~/curling-work/marks
curl -s https://curling.dimmit.net/api/games | python3 -c "
import json,sys
rows=json.load(sys.stdin); rows=rows if isinstance(rows,list) else rows.get('games',[])
for r in rows:
    if r.get('video_id') in ('0SWB4g3SJoE','32Dkqsf2t2M','dea75KXakFc') and r.get('status')=='ready':
        print(r['video_id'], r['source_id'])" | while read vid sid; do
  curl -s -o tl_${vid}_${sid}.json https://curling.dimmit.net/g/$sid/timeline.json; done
ls tl_*
```

Expected: two timelines per video (the 7 pm and 9 pm games).

- [ ] **Step 2: Pick the rocks**

```bash
cd ~/curling-work/marks
for vid in 0SWB4g3SJoE 32Dkqsf2t2M dea75KXakFc; do
  python3 /home/tcuser/src/curling_score/.claude/worktrees/split-sync/scripts/sync/mark_events.py \
    tl_${vid}_*.json --per-direction 7 --out ev_$vid.json; done
```

Expected: `14 rocks of N candidates` per video.

- [ ] **Step 3: Cut the frames on the worker (no VOD download)**

The image has ffmpeg, numpy and Pillow.

```bash
cd ~/curling-work/marks
ssh administrator@10.0.0.182 'mkdir -p /data/wdd/scratch/marks && chmod 777 /data/wdd/scratch/marks'
scp -q /home/tcuser/src/curling_score/.claude/worktrees/split-sync/scripts/mark_hog.py ev_*.json tl_*.json \
  administrator@10.0.0.182:/data/wdd/scratch/marks/
ssh administrator@10.0.0.182 'cd /data/wdd/scratch/marks && for vid in 0SWB4g3SJoE 32Dkqsf2t2M dea75KXakFc; do
  tl=$(ls tl_${vid}_*.json | head -1)
  for mode in receding arriving; do
    docker run --rm -v /data/wdd/curling-cache:/data/cache -v /data/wdd/scratch:/scratch \
      --entrypoint python curling-worker:local /scratch/marks/mark_hog.py --mode $mode \
      --events /scratch/marks/ev_$vid.json --timeline /scratch/marks/$tl \
      --video /data/cache/videos/$vid.mp4 --out /scratch/marks/$vid-$mode --extract-only
  done; done
docker run --rm -v /data/wdd/scratch:/scratch --entrypoint sh curling-worker:local -c "chmod -R a+rwX /scratch/marks"'
rsync -a administrator@10.0.0.182:/data/wdd/scratch/marks/ ~/curling-work/marks/
```

Expected: six directories under `~/curling-work/marks/`, each with
`index.json`, `marks.json` and about 14 × 105 JPEGs. Both games share one
calibration, so the first timeline's calibration serves the recording.

- [ ] **Step 4: Serve each round over HTTP for the user**

Use HTTP, not `file://`. Serve one at a time, or on different ports.

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/split-sync
python3 scripts/mark_hog.py --mode receding --prepared --out ~/curling-work/marks/0SWB4g3SJoE-receding --port 8781
```

Tell the user the URL (`http://localhost:8781/`) and what to mark (the
page says it). Repeat for the other five directories: arriving rounds on
8782, and so on. Marks save as they are made.

- [ ] **Step 5: Fold the marks into the dataset and commit at once**

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/split-sync
for vid in 0SWB4g3SJoE 32Dkqsf2t2M dea75KXakFc; do
  python3 scripts/sync/collect_marks.py ~/curling-work/marks/$vid-receding ~/curling-work/marks/$vid-arriving \
    --out datasets/hogmarks; done
git add datasets/hogmarks/*-receding.json
git commit -m "hogmarks: far hog line marked from behind the thrower and in the arriving panel, 10/01 Mens sheets 1, 2, 5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: about 40 rocks over three files, each mostly with both marks.

---

### Task 3: One decode, several views (`longview.decode_views`)

**Files:**
- Modify: `src/curling_score/detect/longview.py` (after `decode`, ~line 415)
- Test: `tests/test_decode_views.py` (new)

**Interfaces:**
- Produces: `longview.decode_views(video, rects, t0, t1, fps=30.0) -> (list[np.ndarray], list[float])`.
  - One `(n, h_i, w_i, 3)` RGB array per rect, in order.
  - The times are shared: `t0 + i/fps`.
  - Each array is what `longview.decode(video, rect, t0, t1, fps)` returns
    for that rect.

- [ ] **Step 1: Write the failing test**

```python
"""Several views of one window from a single decode equal decoding each alone."""
import shutil
import subprocess

import numpy as np
import pytest

from curling_score.detect import longview

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

# Odd widths, heights and offsets on purpose: the composite's view rects land
# wherever the overhead strip's edges fall.
RECTS = [(0, 0, 161, 240), (171, 37, 149, 113)]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("views") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30", "-t", "20", "-g", "150",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.mark.parametrize("fps", [30.0, 5.0])
def test_each_view_is_what_decoding_it_alone_gives(clip, fps):
    views, times = longview.decode_views(clip, RECTS, 11.0, 14.0, fps=fps)
    assert len(views) == len(RECTS)
    for rect, got in zip(RECTS, views):
        want, want_t = longview.decode(clip, rect, 11.0, 14.0, fps=fps)
        assert times == want_t
        assert got.shape == want.shape
        assert np.array_equal(got, want)


def test_one_view_alone(clip):
    views, times = longview.decode_views(clip, RECTS[:1], 5.0, 6.0, fps=10.0)
    want, want_t = longview.decode(clip, RECTS[0], 5.0, 6.0, fps=10.0)
    assert times == want_t and np.array_equal(views[0], want)


def test_past_the_end_is_no_frames(clip):
    views, times = longview.decode_views(clip, RECTS, 30.0, 31.0, fps=5.0)
    assert times == [] and [v.shape[0] for v in views] == [0, 0]
```

- [ ] **Step 2: Run it to see it fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_decode_views.py`
Expected: FAIL with `AttributeError: module ... has no attribute 'decode_views'`.

- [ ] **Step 3: Implement `decode_views`**

Add it after `decode` in `src/curling_score/detect/longview.py`:

```python
def decode_views(video, rects, t0: float, t1: float, fps: float = 30.0):
    """One window of several views from a single decode: per rect, the frames
    ``decode`` would give for it, and the times they share.

    Every side read decodes the whole frame whatever it crops, and the live
    lane is bound by that decode, so a second view of the same window should
    cost a crop, not a second decode. The crops are converted to RGB, padded
    to the tallest and stacked side by side in one output, then split here.
    """
    rects = [tuple(int(v) for v in r) for r in rects]
    n = len(rects)
    tall = max(h for _x, _y, _w, h in rects)
    width = sum(w for _x, _y, w, _h in rects)
    split = f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n))
    chains = [f"[s{i}]crop={w}:{h}:{x}:{y}:exact=1,fps={fps},format=rgb24,"
              f"pad={w}:{tall}:0:0[v{i}]" for i, (x, y, w, h) in enumerate(rects)]
    stack = ("".join(f"[v{i}]" for i in range(n)) + f"hstack=inputs={n}[out]"
             if n > 1 else "[v0]null[out]")
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *_seek(video, t0),
         "-t", f"{t1 - t0 + 0.05}", "-filter_complex", ";".join([split, *chains, stack]),
         "-map", "[out]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, tall, width, 3)
    out, col = [], 0
    for _x, _y, w, h in rects:
        out.append(frames[:, :h, col:col + w])
        col += w
    return out, [t0 + i / fps for i in range(frames.shape[0])]
```

- [ ] **Step 4: Run the test**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_decode_views.py tests/test_longview_ts.py`
Expected: PASS.

If `array_equal` fails for the odd-offset rect only, by chroma rounding
(`np.abs(got.astype(int) - want).max() <= 2`), the auto-inserted converter
differs from the `format` filter. Then move `format=rgb24` after `pad` is
not possible, because pad needs even sizes in yuv420p. Instead add
`-sws_flags` matching ffmpeg's output default (`bicubic+accurate_rnd+full_chroma_int`)
before `-filter_complex` and rerun. Only if that still differs, relax the
assertion to `<= 2` and say so in the docstring.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/longview.py tests/test_decode_views.py
git commit -m "longview: several views of one window from a single decode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: One read after the hog line, shared by the line and sync passes

**Files:**
- Modify: `src/curling_score/game/linetime.py` (`find_path` at ~384-400, `time_lines` at ~416-475)
- Modify: `src/curling_score/game/shots.py` (`Shot`, after `target_broom`, ~line 68)
- Test: `tests/test_linetime.py` (extend)

**Interfaces:**
- Consumes: `longview.decode_views` (Task 3).
- Produces:
  - `linetime.path_window(t_hog, t_rest) -> (t0, t1)`.
  - `linetime.MID_Y = (13.0, 21.75)`.
  - `linetime.hog_mid_rows(view) -> (lo, hi) | None`.
  - `linetime.DestRead(times, per, other, hog_mid)`: frozen dataclass of
    tuples.
    - `per`/`other`: per frame, tuples of `path_points` entries
      `(t, x, y, cx, row, conf)`.
    - `hog_mid`: per frame, tuples of `(cx, row, w, conf)` in the hog
      camera's view rows.
  - `linetime.read_destination(model, video, dest_view, hog_view, color, t_hog, t_rest, *, decode_views, detect, detect_one) -> DestRead | None`.
  - `linetime.read_destinations(shots, video, hog_view, dest_view, *, model=None, decode_views=None, detect=None, detect_one=None) -> int`.
    It sets `shot.dest_read`.
  - `Shot.dest_read` and `Shot.sync` fields (default None).
  - `time_lines` uses `shot.dest_read` when set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_linetime.py`. `HOG_VIEW` and `DEST_VIEW` are already
defined in this file.

```python
import numpy as np


def views_decoder(seen):
    def decode_views(video, rects, t0, t1, fps):
        seen.append(list(rects))
        times = [t0 + i / fps for i in range(3)]
        return [np.zeros((3, r[3], r[2], 3), np.uint8) for r in rects], times
    return decode_views


def no_boxes(model, frames, times, lo, hi, colors, imgsz=800, conf=0.35):
    return [[[] for _ in frames] for _ in colors]


class TestReadDestination:
    def run(self, hog_view=HOG_VIEW):
        seen, bands = [], []

        def detect_one(model, frames, times, lo, hi, color, imgsz=800, conf=0.35):
            bands.append((lo, hi, imgsz, frames[0].shape[0]))
            return [[(400.0, hi - 1.0, 60.0, 0.9)] if i == 0 else [] for i in range(len(frames))]

        read = L.read_destination(object(), "v.mp4", DEST_VIEW, hog_view, "red", 100.0, 120.0,
                                  decode_views=views_decoder(seen), detect=no_boxes,
                                  detect_one=detect_one)
        return read, seen, bands

    def test_the_hog_camera_rows_span_mid_sheet_from_its_calibration(self):
        lo, hi = L.hog_mid_rows(HOG_VIEW)
        assert lo < HOG_VIEW.row_for(L.MID_Y[0]) < HOG_VIEW.row_for(L.MID_Y[1]) < hi
        assert hi <= HOG_VIEW.rect[3]

    def test_a_reaimed_camera_reads_higher_rows(self):
        reaimed = SideView(rect=(0, 0, 810, 1080), tee_row=361.0, hog_row=433.0,
                           centre_col=405.0, lat_px_per_m_at_tee=140.0, centre_line=(405.0, 0.0))
        assert L.hog_mid_rows(reaimed)[0] < L.hog_mid_rows(HOG_VIEW)[0]

    def test_one_decode_gives_both_cameras(self):
        _read, seen, _bands = self.run()
        lo, hi = L.hog_mid_rows(HOG_VIEW)
        x, _y, w, _h = HOG_VIEW.rect
        assert seen == [[DEST_VIEW.rect, (x, lo, w, hi - lo)]]

    def test_the_window_is_the_paths(self):
        read, _seen, _bands = self.run()
        assert read.times[0] == pytest.approx(L.path_window(100.0, 120.0)[0])

    def test_hog_boxes_come_back_in_the_views_own_rows(self):
        read, _seen, _bands = self.run()
        lo, hi = L.hog_mid_rows(HOG_VIEW)
        rows = [b[1] for b in read.hog_mid[0]]
        assert rows and all(lo <= r <= hi for r in rows)

    def test_without_a_hog_camera_only_the_destination_is_read(self):
        read, seen, bands = self.run(hog_view=None)
        assert seen == [[DEST_VIEW.rect]] and read.hog_mid == () and bands == []

    def test_no_crossing_is_no_read(self):
        assert L.read_destination(object(), "v.mp4", DEST_VIEW, HOG_VIEW, "red", None, 120.0,
                                  decode_views=views_decoder([]), detect=no_boxes,
                                  detect_one=no_boxes) is None


class TestReadDestinations:
    def test_every_shot_with_a_crossing_gets_a_read_and_one_failure_costs_one_shot(self):
        ok = SimpleNamespace(missing=False, color="red", t_hog_s=100.0, t_rest_s=120.0, dest_read=None)
        bad = SimpleNamespace(missing=False, color="red", t_hog_s=200.0, t_rest_s=220.0, dest_read=None)
        none = SimpleNamespace(missing=False, color="red", t_hog_s=None, t_rest_s=None, dest_read=None)

        def decode_views(video, rects, t0, t1, fps):
            if t0 > 150:
                raise RuntimeError("boom")
            return views_decoder([])(video, rects, t0, t1, fps)

        n = L.read_destinations([ok, bad, none], "v.mp4", HOG_VIEW, DEST_VIEW, model=object(),
                                decode_views=decode_views, detect=no_boxes, detect_one=no_boxes)
        assert n == 1 and ok.dest_read is not None
        assert bad.dest_read is None and none.dest_read is None


class TestTheLinePassUsesTheSharedRead:
    def test_a_stored_read_is_chained_without_decoding_the_destination_again(self):
        t = TestTimeLines()
        s = t.shot(crossing_for(t.stone_to(11.5)))
        times, per = TestChain().frames()
        s.dest_read = L.DestRead(tuple(times), tuple(tuple(p) for p in per),
                                 tuple(() for _ in per), ())
        decoded = []

        def decode(video, rect, t0, t1, fps):
            decoded.append(rect)
            return [object()] * 3, [t0, t0 + 0.1, t0 + 0.2]

        def detect(model, frames, times, lo, hi, color, imgsz=800, conf=0.35):
            return [[] for _ in frames]

        L.time_lines([s], "v.mp4", HOG_VIEW, DEST_VIEW, model=object(), decode=decode, detect=detect)
        assert DEST_VIEW.rect not in decoded
        assert s.line is not None and len(s.line.path) > 5

    def test_the_shared_read_gives_the_path_find_path_gives(self):
        times, per = TestChain().frames()

        def decode(video, rect, t0, t1, fps):
            return [object()] * len(times), list(times)

        def detect(model, frames, times_, lo, hi, colors, imgsz=800, conf=0.35):
            # the far band carries the rock; the near band nothing
            if lo < L.PATH_SPLIT_ROW:
                rock = [[box_for(DEST_VIEW, p[1], p[2]) for p in frame] for frame in per]
                return [rock, [[] for _ in per]]
            return [[[] for _ in per] for _ in colors]

        direct = L.find_path(object(), "v.mp4", DEST_VIEW, "red", 100.0, 120.0, TestChain.FIT,
                             decode=decode, detect=detect)
        pts, other = L._dest_points(object(), [None] * len(times), list(times), DEST_VIEW, "red", detect)
        assert L.chain(list(times), pts, TestChain.FIT, other) == direct
```

`box_for(view, x_dest, yp)` in this file builds a hog-camera box. For the
destination camera, the last test only needs the same boxes to go through both
paths, so any consistent box works.

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_linetime.py`
Expected: FAIL with `AttributeError: ... has no attribute 'hog_mid_rows'` (and siblings).

- [ ] **Step 3: Add the `Shot` fields**

In `src/curling_score/game/shots.py`, after `target_broom: object = None`:

```python
    # The read after the hog line, both long cameras from one decode
    # (`linetime.read_destinations`): the line pass's path and the sync pass
    # (`game/sync.py`) work from it. None until that pass has run.
    dest_read: object = None
    # The rock's camera-sync readings, a `sync.ShotSync`, or None.
    sync: object = None
```

- [ ] **Step 4: Refactor `find_path` and add the shared read in `linetime.py`**

Add `from dataclasses import dataclass` to the imports if it's not already
there. Replace `find_path` (lines ~384-400) with:

```python
def path_window(t_hog: float, t_rest: float | None) -> tuple[float, float]:
    """The read after the hog line: from a second past the crossing to a
    second past the rest, or 24 s on when there is no rest."""
    return t_hog + 1.0, (t_rest if t_rest is not None else t_hog + 24.0) + 1.0


def _dest_points(model, frames, times, view, color, detect):
    """The camera behind the thrower's points per frame, the rock's colour and
    the other, from one pass of the detector per band. ``detect`` is
    ``sidemodel.detect_bands``."""
    colors = (color, other_color(color))
    far = detect(model, frames, times, int(view.tee_row) - 80, PATH_SPLIT_ROW + 20, colors,
                 imgsz=800, conf=PATH_CONF)
    near_boxes = detect(model, frames, times, PATH_SPLIT_ROW - 20, view.rect[3], colors,
                        imgsz=416, conf=PATH_CONF)
    per, other = (path_points([a + b for a, b in zip(f, n)], times, view)
                  for f, n in zip(far, near_boxes))
    return per, other


def find_path(model, video, view, color, t_hog, t_rest, fit, *, decode, detect):
    """Where the rock went, seen from behind the thrower. ``detect`` is
    ``sidemodel.detect_bands``: the other colour's boxes come from the same pass."""
    if view is None or not view.has_lateral or t_hog is None:
        return []
    t0, t1 = path_window(t_hog, t_rest)
    frames, times = decode(video, view.rect, t0, t1, PATH_FPS)
    if not len(frames):
        return []
    per, other = _dest_points(model, frames, times, view, color, detect)
    return chain(times, per, fit, other)


# The hog camera's own view of mid-sheet, where the camera behind the thrower
# sees the rock too: 13.0-21.75 m from the playing tee, which is the same
# span measured from the hog camera's far (throwing) tee. Centred on the
# sheet's midpoint, so ends thrown either way are timed at the same spots
# (`game/sync.py`). Padded for the box rising above its bottom edge.
MID_Y = (13.0, 21.75)
MID_PAD_ROWS = (40, 20)


@dataclass(frozen=True)
class DestRead:
    """One read after the hog line, shared by the line pass and the sync pass.

    ``per`` and ``other``: per frame, the camera behind the thrower's points
    (``path_points``) for the rock's colour and the other. ``hog_mid``: per
    frame, the hog camera's boxes ``(cx, row, w, conf)`` for the rock's colour
    over mid-sheet, in that view's own rows -- empty without a hog camera."""
    times: tuple
    per: tuple
    other: tuple
    hog_mid: tuple = ()


def hog_mid_rows(view) -> tuple[int, int] | None:
    """The hog camera's rows for mid-sheet, clipped to its view; None without
    a laterally calibrated view."""
    if view is None or not view.has_lateral:
        return None
    lo = max(0, int(view.row_for(MID_Y[0])) - MID_PAD_ROWS[0])
    hi = min(view.rect[3], int(view.row_for(MID_Y[1])) + MID_PAD_ROWS[1])
    return (lo, hi) if hi - lo > 40 else None


def _hog_mid_boxes(model, frames, times, band, color, detect_one):
    """Boxes of the rock's colour in a crop starting at view row ``band[0]``:
    above PATH_SPLIT_ROW at imgsz 800, below it at 416, as the camera behind
    the thrower is read. ``detect_one`` is ``sidemodel.detect_band``."""
    lo, hi = band
    per = [[] for _ in times]
    if lo < PATH_SPLIT_ROW + 20:
        top = min(hi, PATH_SPLIT_ROW + 20)
        for got, boxes in zip(per, detect_one(model, frames, times, 0, top - lo, color,
                                              imgsz=800, conf=PATH_CONF)):
            got.extend(boxes)
    if hi > PATH_SPLIT_ROW - 20:
        start = max(lo, PATH_SPLIT_ROW - 20)
        for got, boxes in zip(per, detect_one(model, frames, times, start - lo, hi - lo, color,
                                              imgsz=416, conf=PATH_CONF)):
            got.extend(boxes)
    return tuple(tuple((cx, row + lo, w, cf) for cx, row, w, cf in got) for got in per)


def read_destination(model, video, dest_view, hog_view, color, t_hog, t_rest, *,
                     decode_views, detect, detect_one) -> DestRead | None:
    """Both long cameras over the path's window from one decode."""
    if dest_view is None or not dest_view.has_lateral or t_hog is None:
        return None
    t0, t1 = path_window(t_hog, t_rest)
    band = hog_mid_rows(hog_view)
    rects = [dest_view.rect]
    if band is not None:
        x, _y, w, _h = hog_view.rect
        rects.append((x, band[0], w, band[1] - band[0]))
    views, times = decode_views(video, rects, t0, t1, PATH_FPS)
    if not len(times):
        return None
    per, other = _dest_points(model, views[0], times, dest_view, color, detect)
    hog_mid = () if band is None else _hog_mid_boxes(model, views[1], times, band, color,
                                                     detect_one)
    return DestRead(tuple(times), tuple(tuple(p) for p in per),
                    tuple(tuple(p) for p in other), hog_mid)


def read_destinations(shots, video, hog_view, dest_view, *, model=None, decode_views=None,
                      detect=None, detect_one=None) -> int:
    """Give every shot with a throwing-end hog crossing its ``dest_read``, in
    place; return how many got one. Attach-only, and a failure on one rock
    costs that rock its read, as in ``time_lines``."""
    if model is None or dest_view is None or not dest_view.has_lateral:
        return 0
    if decode_views is None:
        from curling_score.detect import longview
        decode_views = longview.decode_views
    if detect is None or detect_one is None:
        from curling_score.detect import sidemodel
        detect, detect_one = sidemodel.detect_bands, sidemodel.detect_band
    n = 0
    for shot in shots:
        if getattr(shot, "missing", False) or getattr(shot, "t_hog_s", None) is None:
            continue
        try:
            shot.dest_read = read_destination(
                model, video, dest_view, hog_view, shot.color, shot.t_hog_s,
                getattr(shot, "t_rest_s", None), decode_views=decode_views,
                detect=detect, detect_one=detect_one)
            n += shot.dest_read is not None
        except Exception:
            log.exception("destination read failed on shot %s; it keeps none",
                          getattr(shot, "number", "?"))
    return n
```

In `time_lines`, replace

```python
            path = find_path(model, video, dest_view, shot.color, getattr(shot, "t_hog_s", None),
                             getattr(shot, "t_rest_s", None), fit, decode=decode, detect=detect_bands)
```

with

```python
            read = getattr(shot, "dest_read", None)
            if read is not None:
                path = chain(list(read.times), list(read.per), fit, list(read.other))
            else:
                path = find_path(model, video, dest_view, shot.color, getattr(shot, "t_hog_s", None),
                                 getattr(shot, "t_rest_s", None), fit, decode=decode,
                                 detect=detect_bands)
```

- [ ] **Step 5: Run the linetime and shots tests**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_linetime.py tests/test_shots.py`
Expected: PASS, including every existing `TestChain`/`TestFindPath`/`TestTimeLines` test.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/game/linetime.py src/curling_score/game/shots.py tests/test_linetime.py
git commit -m "linetime: one read after the hog line, both long cameras, kept on the shot for the line and sync passes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Per-shot sync readings (`game/sync.py`, part 1)

**Files:**
- Create: `src/curling_score/game/sync.py`
- Test: `tests/test_sync.py` (new)

**Interfaces:**
- Consumes: `linetime.DestRead`, `linetime.MID_Y`, `linetime.to_destination`,
  `linetime.PATH_WIDTH` (Task 4), and `split.FarCrossing` (`t`, `reach`).
- Produces:
  - `sync.ShotSync(far_s, mid_s, v_mid_m_s, t_far_panel_s, t_trail_s, v_far_m_s)`
  - `sync.Track`
  - `sync.moving_track(points, lo, hi) -> Track | None`
  - `sync.time_at(track, y) -> float | None`
  - `sync.speed_at(track, t) -> float`
  - `sync.dest_points(read) -> [(t, x, y)]`
  - `sync.hog_points(read, view) -> [(t, x, y)]`
  - `sync.far_reading(read, framing, c=None) -> (t_trail, v, t_lead) | None`
  - `sync.mid_reading(hog_pts, dest_pts) -> (mid_s, v_mid) | None`
  - `sync.framing_of(tee_rows) -> "old" | "reaimed" | None`
  - `sync.measure_shots(shots, hog_view, framing) -> int`
  - `sync.RECEDING_C_S`, `sync.DEFAULTS`, `sync.REAIMED_TEE_ROW`

- [ ] **Step 1: Write the failing tests**

```python
"""Splits corrected for the composite's camera offsets: the readings."""
from types import SimpleNamespace

import pytest

from curling_score.game import linetime as L
from curling_score.game import split
from curling_score.game import sync as Y
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView

TEE = C.TEE_TO_TEE_M
R = C.STONE_RADIUS_M
HOG_CAM = SideView(rect=(0, 0, 810, 1080), tee_row=472.0, hog_row=559.0,
                   centre_col=405.0, lat_px_per_m_at_tee=145.0, centre_line=(405.0, 0.0))


def motion(t0, y0=24.0, v=1.8, decel=0.05, n=60, fps=5.0):
    """(t, y) of a stone heading for the playing tee, starting at y0 at t0."""
    out = []
    for i in range(n):
        dt = i / fps
        out.append((t0 + dt, y0 - v * dt + 0.5 * decel * dt * dt))
    return out


def dest_frames(track, lag=0.0, x=0.1):
    """The camera behind the thrower's per-frame points, as path_points gives
    them: (t, x, y, cx, row, conf), shown ``lag`` seconds late."""
    return tuple(((t + lag, x, y, 0.0, 0.0, 0.9),) for t, y in track)


def hog_boxes(track, view=HOG_CAM, lag=0.0, x=0.1):
    """The hog camera's boxes for the same stone, shown ``lag`` seconds late,
    so that linetime.to_destination reads them back to (x, y)."""
    times, frames = [], []
    for t, y in track:
        yp = TEE - y
        if not 0 < yp < view.d_m - 1:
            continue
        row_c = view.row_for(yp)
        edge = view.row_for(yp + R)
        if edge > view.rect[3] - 7:
            continue
        cx = view.centre_col_at(row_c) - x * view.lateral_px_per_m(row_c)
        w = 0.291 * view.lateral_px_per_m(edge)
        times.append(t + lag)
        frames.append(((cx, edge, w, 0.9),))
    return times, frames


def read_of(track, hog_lag=0.0, dest_lag=0.0):
    times, hog = hog_boxes(track, lag=hog_lag)
    dest = dest_frames(track, lag=dest_lag)
    # one shared clock: align by index where both exist
    return L.DestRead(tuple(times), dest[:len(times)], tuple(() for _ in times), tuple(hog))


class TestMovingTrack:
    def test_it_finds_the_moving_stone_and_ignores_one_at_rest(self):
        pts = [(t, 0.1, y) for t, y in motion(0.0)] + [(i * 0.2, 0.3, 17.0) for i in range(60)]
        tr = Y.moving_track(pts, *L.MID_Y)
        assert tr is not None
        # 24 - 1.8 t + 0.025 t^2 = 17
        want = (1.8 - (1.8 ** 2 - 4 * 0.025 * 7.0) ** 0.5) / (2 * 0.025)
        assert Y.time_at(tr, 17.0) == pytest.approx(want, abs=0.05)

    def test_a_stone_off_the_sheet_is_not_the_track(self):
        pts = [(t, 3.0, y) for t, y in motion(0.0)]
        assert Y.moving_track(pts, *L.MID_Y) is None

    def test_a_big_weight_hit_is_tracked(self):
        pts = [(t, 0.0, y) for t, y in motion(0.0, v=3.6, decel=0.0)]
        tr = Y.moving_track(pts, *L.MID_Y)
        assert tr is not None and Y.speed_at(tr, Y.time_at(tr, 17.0)) == pytest.approx(3.6, rel=0.05)


class TestMidReading:
    def test_it_recovers_the_hog_cameras_lag(self):
        track = motion(100.0)
        hog = [(t + 0.3, x, y) for t, x, y in [(t, 0.1, y) for t, y in track]]
        dest = [(t, 0.1, y) for t, y in track]
        mid, v = Y.mid_reading(hog, dest)
        assert mid == pytest.approx(0.3, abs=0.01)
        assert v == pytest.approx(1.8 - 0.05 * ((24.0 - 17.4) / 1.8), abs=0.1)

    def test_too_short_an_overlap_is_no_reading(self):
        track = motion(100.0)
        hog = [(t, 0.1, y) for t, y in track if y > 20.5]
        dest = [(t, 0.1, y) for t, y in track]
        assert Y.mid_reading(hog, dest) is None

    def test_hog_points_read_the_boxes_back_where_the_stone_was(self):
        track = motion(100.0)
        read = read_of(track)
        pts = Y.hog_points(read, HOG_CAM)
        assert pts and all(abs(x - 0.1) < 0.02 for _t, x, _y in pts)
        t, _x, y = pts[5]
        want = dict(track)[t]
        assert y == pytest.approx(want, abs=0.02)


class TestFarReading:
    def far_track(self, t_cross=50.0, v=1.6):
        # the trailing edge (box bottom) reaches 6.401 m at t_cross
        return [(t_cross + dt, 0.0, C.TEE_TO_HOGLINE_M - v * dt - R)
                for dt in [i / 5.0 - 2.0 for i in range(20)]]

    def read(self, pts):
        return L.DestRead(tuple(t for t, *_ in pts), tuple(((t, x, y, 0, 0, 0.9),) for t, x, y in pts),
                          tuple(() for _ in pts), ())

    def test_the_leading_edge_touches_a_diameter_before_the_trailing_edge_clears(self):
        t_trail, v, t_lead = Y.far_reading(self.read(self.far_track()), "old", c=0.0)
        assert t_trail == pytest.approx(50.0, abs=0.01)
        assert v == pytest.approx(1.6, abs=0.02)
        assert t_lead == pytest.approx(50.0 - 2 * R / 1.6, abs=0.01)

    def test_the_calibrated_offset_moves_it(self):
        _t, _v, t_lead = Y.far_reading(self.read(self.far_track()), "old", c=0.05)
        assert t_lead == pytest.approx(50.05 - 2 * R / 1.6, abs=0.01)


class TestFraming:
    def test_reaimed_only_when_every_view_is(self):
        assert Y.framing_of([361.0, 364.0]) == "reaimed"
        assert Y.framing_of([361.0, 480.0]) == "old"
        assert Y.framing_of([472.0, 483.0]) == "old"
        assert Y.framing_of([]) is None


class TestMeasureShots:
    def shot(self, read, far_t=60.0, reach=0.0, **kw):
        return SimpleNamespace(missing=False, dest_read=read, sync=None,
                               far_crossing=split.FarCrossing(t=far_t, reach=reach), **kw)

    def test_a_shot_gets_both_readings(self):
        track = motion(100.0)
        s = self.shot(read_of(track, hog_lag=0.25))
        assert Y.measure_shots([s], HOG_CAM, "old") >= 0
        assert s.sync.mid_s == pytest.approx(0.25, abs=0.03)

    def test_a_reached_for_far_crossing_gives_no_far_reading(self):
        s = self.shot(read_of(motion(100.0)), reach=0.12)
        Y.measure_shots([s], HOG_CAM, "old")
        assert s.sync.far_s is None and s.sync.t_far_panel_s is None

    def test_no_read_no_readings(self):
        s = self.shot(None)
        Y.measure_shots([s], HOG_CAM, "old")
        assert s.sync is None

    def test_without_a_hog_camera_there_is_no_mid_reading(self):
        s = self.shot(read_of(motion(100.0)))
        Y.measure_shots([s], None, "old")
        assert s.sync.mid_s is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync.py`
Expected: FAIL with `ImportError: cannot import name 'sync'`.

- [ ] **Step 3: Write `src/curling_score/game/sync.py` (readings)**

```python
"""Splits corrected for the composite's camera offsets.

The club's composite carries four feeds per sheet -- the two overhead panels
and the two long cameras -- and does not keep them in step: up to half a
second apart on 2026-10-01, sometimes jumping mid-game. A split is the
arriving panel's far hog crossing minus the hog camera's throwing hog
crossing, so it carries those two feeds' offset in full, with a sign that
follows the throwing direction.

The two long cameras both see the middle of the sheet, which closes a chain
through all four feeds in every end (docs/superpowers/specs/2026-10-02-split-
sync-correction-design.md). Per shot:

  far = arriving panel - camera behind the thrower, at the far hog line
  mid = hog camera - camera behind the thrower, mid-sheet, + a role term

and the split's error is ``far - (mid - role)``. The role term is a position
bias between the two cameras' mid-sheet readings whose sign follows which
camera is the hog camera, so ends thrown in opposite directions cancel it;
``correct`` fits it per recording and rewrites every split from its raw
value, in place, on every document it builds. Attach-only like ``hogtime``:
nothing here adds, drops or renumbers a shot.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

# The mid-sheet stretch both long cameras see -- `linetime.MID_Y`, repeated
# here so this module reads without importing the line pass.
MID_Y = (13.0, 21.75)
MID_STEP_M = 0.25
MID_MIN_LEVELS = 5
MID_MIN_SPAN_M = 2.0
# The camera behind the thrower's far hog line: its trailing edge (the box
# bottom) fitted over this stretch, which brackets the paint at 6.401 m.
FAR_FIT_M = (5.5, 8.5)
# A far crossing the panel reached back for is no reading.
FAR_MAX_REACH_U = 0.05
DIAMETER_M = 2 * C.STONE_RADIUS_M
# Hand marks against the detector's box bottom for a receding stone (spec
# section 5), per framing. Zero until fitted.
RECEDING_C_S = {"old": 0.0, "reaimed": 0.0}
# The re-aimed sheets 2-4 put the far tee above this row.
REAIMED_TEE_ROW = 430.0
SPEED_M_S = (0.6, 5.0)
FIT_TOL_M = 0.15
MIN_SAMPLES = 8
# Until a recording has ends thrown both ways: (role term at 1.6 m/s in s,
# speed term in m), measured on 2026-10-01's Mens sheets 1 and 5 (old) and
# 2 (re-aimed). Revisit as more nights are measured.
DEFAULTS = {"old": (0.20, 0.22), "reaimed": (0.56, 0.36)}


@dataclass(frozen=True)
class ShotSync:
    """One rock's camera-sync readings; any may be None."""
    far_s: float | None
    mid_s: float | None
    v_mid_m_s: float | None
    t_far_panel_s: float | None
    t_trail_s: float | None
    v_far_m_s: float | None


@dataclass(frozen=True)
class Track:
    """A fitted y(t), polynomial in ``t - t0``, over the samples it kept."""
    coef: tuple
    t0: float
    t_lo: float
    t_hi: float
    y_lo: float
    y_hi: float


def moving_track(points, lo: float, hi: float, *, tol: float = FIT_TOL_M) -> Track | None:
    """The one stone running toward the playing tee through ``[lo, hi]``, from
    ``(t, x, y)`` points: a RANSAC line, then a quadratic through its inliers.
    A stone at rest, or one off the sheet, is not it."""
    P = np.array([(t, y) for t, x, y in points if lo - 1.0 <= y <= hi + 1.0 and abs(x) < 2.0],
                 dtype=float)
    if len(P) < MIN_SAMPLES:
        return None
    rng = np.random.default_rng(0)
    best = None
    for _ in range(300):
        i, j = rng.choice(len(P), 2, replace=False)
        dt = P[j, 0] - P[i, 0]
        if dt == 0:
            continue
        b = (P[j, 1] - P[i, 1]) / dt
        if not -SPEED_M_S[1] <= b <= -SPEED_M_S[0]:
            continue
        inl = np.abs(P[:, 1] - (P[i, 1] + b * (P[:, 0] - P[i, 0]))) < 2 * tol
        if best is None or inl.sum() > best.sum():
            best = inl
    if best is None or best.sum() < MIN_SAMPLES:
        return None
    Q = P[best]
    t0 = float(Q[:, 0].mean())
    for _ in range(2):
        deg = 2 if np.ptp(Q[:, 0]) > 1.5 else 1
        coef = np.polyfit(Q[:, 0] - t0, Q[:, 1], deg)
        Q = Q[np.abs(Q[:, 1] - np.polyval(coef, Q[:, 0] - t0)) < tol]
        if len(Q) < MIN_SAMPLES:
            return None
    coef = np.polyfit(Q[:, 0] - t0, Q[:, 1], 2 if np.ptp(Q[:, 0]) > 1.5 else 1)
    return Track(tuple(float(c) for c in coef), t0, float(Q[:, 0].min()), float(Q[:, 0].max()),
                 float(Q[:, 1].min()), float(Q[:, 1].max()))


def time_at(track: Track, y: float) -> float | None:
    """When the track was at ``y``, within its span (plus 0.3 s), or None."""
    ts = np.linspace(track.t_lo - 0.3, track.t_hi + 0.3, 2000)
    ys = np.polyval(track.coef, ts - track.t0)
    k = np.nonzero(np.diff(np.sign(ys - y)))[0]
    if not len(k):
        return None
    k = int(k[0])
    return float(ts[k] + (y - ys[k]) * (ts[k + 1] - ts[k]) / (ys[k + 1] - ys[k]))


def speed_at(track: Track, t: float) -> float:
    return abs(float(np.polyval(np.polyder(np.array(track.coef)), t - track.t0)))


def dest_points(read) -> list:
    """The camera behind the thrower's ``(t, x, y)``: the stone's centre, y
    from the playing tee."""
    return [(p[0], p[1], p[2]) for frame in read.per for p in frame]


def hog_points(read, view) -> list:
    """The hog camera's boxes as ``(t, x, y)``, read as ``linetime`` reads a
    hog-camera box, with ``path_points``' width check."""
    from curling_score.game.linetime import PATH_WIDTH, to_destination

    out = []
    for t, boxes in zip(read.times, read.hog_mid):
        for cx, row, w, _conf in boxes:
            if not PATH_WIDTH[0] < w / (0.291 * view.lateral_px_per_m(row)) < PATH_WIDTH[1]:
                continue
            x, y, _yp = to_destination(view, cx, row)
            out.append((t, x, y))
    return out


def far_reading(read, framing: str, *, c: float | None = None):
    """``(t_trail, v, t_lead)``: when the camera behind the thrower saw the
    stone's trailing edge clear the far hog line's paint, its speed there, and
    when its leading edge touched it -- the arriving tripwire's moment."""
    pts = [(t, x, y + C.STONE_RADIUS_M) for t, x, y in dest_points(read)]   # box bottom
    track = moving_track(pts, *FAR_FIT_M)
    if track is None:
        return None
    t_trail = time_at(track, C.TEE_TO_HOGLINE_M)
    if t_trail is None:
        return None
    v = speed_at(track, t_trail)
    if v <= 0:
        return None
    c = RECEDING_C_S.get(framing or "old", 0.0) if c is None else c
    return t_trail, v, t_trail + c - DIAMETER_M / v


def mid_reading(hog_pts, dest_pts):
    """``(mid, v)``: hog camera minus camera behind the thrower for the stone
    at the same place mid-sheet (the median over levels), and its speed."""
    a = moving_track(hog_pts, *MID_Y)
    b = moving_track(dest_pts, *MID_Y)
    if a is None or b is None:
        return None
    lo = max(MID_Y[0], a.y_lo, b.y_lo)
    hi = min(MID_Y[1], a.y_hi, b.y_hi)
    if hi - lo < MID_MIN_SPAN_M:
        return None
    ds = []
    for y in np.arange(lo, hi + 1e-6, MID_STEP_M):
        ta, tb = time_at(a, float(y)), time_at(b, float(y))
        if ta is not None and tb is not None:
            ds.append(ta - tb)
    if len(ds) < MID_MIN_LEVELS:
        return None
    tm = time_at(b, (lo + hi) / 2)
    return float(np.median(ds)), (None if tm is None else speed_at(b, tm))


def framing_of(tee_rows) -> str | None:
    """"reaimed" when every side view's far tee sits above REAIMED_TEE_ROW,
    else "old"; None with no side views."""
    rows = [float(r) for r in tee_rows if r is not None]
    if not rows:
        return None
    return "reaimed" if all(r < REAIMED_TEE_ROW for r in rows) else "old"


def measure_shots(shots, hog_view, framing) -> int:
    """Give every shot with a ``dest_read`` its ``sync`` readings, in place;
    return how many have both."""
    n = 0
    for shot in shots:
        read = getattr(shot, "dest_read", None)
        if getattr(shot, "missing", False) or read is None:
            continue
        far = getattr(shot, "far_crossing", None)
        t_panel = None
        if far is not None and far.t is not None and (far.reach or 0.0) <= FAR_MAX_REACH_U:
            t_panel = float(far.t)
        got = far_reading(read, framing)
        mid = None
        if hog_view is not None and read.hog_mid:
            mid = mid_reading(hog_points(read, hog_view), dest_points(read))
        shot.sync = ShotSync(
            far_s=None if t_panel is None or got is None else t_panel - got[2],
            mid_s=None if mid is None else mid[0],
            v_mid_m_s=None if mid is None else mid[1],
            t_far_panel_s=t_panel,
            t_trail_s=None if got is None else got[0],
            v_far_m_s=None if got is None else got[1])
        n += shot.sync.far_s is not None and shot.sync.mid_s is not None
    return n
```

- [ ] **Step 4: Run the tests**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync.py`
Expected: PASS. If `test_hog_points_read_the_boxes_back_where_the_stone_was`
is off by the stone-radius convention, check `hog_boxes`: it must put the box
bottom at `row_for(yp + R)`, matching `linetime.box_for` in
`tests/test_linetime.py`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/sync.py tests/test_sync.py
git commit -m "sync: per-shot readings, the far hog line from behind the thrower and the two long cameras mid-sheet

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The correction (`sync.correct`)

**Files:**
- Modify: `src/curling_score/game/sync.py` (append)
- Test: `tests/test_sync_correct.py` (new)

**Interfaces:**
- Consumes the timeline shot dicts' `long_split_raw_s` and `sync`
  (`far_s`, `mid_s`, `v_mid_m_s`), plus `doc["calibration"]["left"/"right"]["tee_row"]`.
- Produces:
  - `sync.correct(doc) -> doc`, which works in place and is idempotent.
  - `sync.shot_error(sy, S, R0)`
  - `sync.end_stats(errs)`
  - `sync.smooth(errs)`
  - `sync.fit_role(ends, framing) -> (S, R0, pairs, default)`
  - `sync.end_line(number, shots, framing) -> str`
- Writes these per shot:
  - `long_split_s`
  - `long_split_sync_s`
  - `long_split_sync`
  - `sync.cam_split_s`
  - a `shot_type` flip, when one applies
- And these per end and per document:
  - per end: `sync = {split_error_s, mad_s, n, source, low_confidence}`
  - per document: `sync = {role_s, speed_term_m, pairs, default}`

- [ ] **Step 1: Write the failing tests**

```python
"""Splits corrected for the composite's camera offsets: the correction."""
import copy

import pytest

from curling_score.game import sync as Y

OTHER = {"top": "bottom", "bottom": "top"}
CAM = {"top": "left", "bottom": "right"}
OLD = {"left": {"tee_row": 472.0}, "right": {"tee_row": 483.0}}
REAIMED = {"left": {"tee_row": 361.0}, "right": {"tee_row": 364.0}}


def shot_for(house, lags, true_split=14.0, v=1.6, R0=0.3, S=0.2, jitter=0.0,
             far=True, mid=True, kind="draw"):
    """A shot played to ``house`` whose readings follow from the four feeds'
    ``lags``: the split carries arriving panel - hog camera."""
    hog, behind, panel = CAM[OTHER[house]], CAM[house], house
    e = lags[panel] - lags[hog]
    return {"long_split_raw_s": round(true_split + e, 2), "long_split_s": round(true_split + e, 2),
            "shot_type": kind, "shot_type_source": "auto", "_true": true_split,
            "sync": {"far_s": (lags[panel] - lags[behind] + jitter) if far else None,
                     "mid_s": (lags[hog] - lags[behind] + R0 + S * (1 / v - 1 / 1.6)) if mid else None,
                     "v_mid_m_s": v if mid else None}}


LAGS = {"left": 0.0, "right": 0.25, "top": 0.5, "bottom": 0.05}


def doc(ends, cal=OLD):
    return {"calibration": cal,
            "games": [{"ends": [{"number": i + 1, "house": h, "shots": s}
                                for i, (h, s) in enumerate(ends)]}]}


def speeds(n=16):
    return [1.4 + 0.1 * (i % 8) for i in range(n)]


def game(n_ends=4, lags=LAGS, **kw):
    ends = []
    for k in range(n_ends):
        house = "bottom" if k % 2 == 0 else "top"
        ends.append((house, [shot_for(house, lags, v=v, **kw) for v in speeds()]))
    return ends


def splits(d):
    return [(s["long_split_s"], s["_true"]) for g in d["games"] for e in g["ends"] for s in e["shots"]]


class TestCorrect:
    def test_splits_come_back_to_the_truth_in_both_directions(self):
        d = Y.correct(doc(game()))
        for got, true in splits(d):
            assert got == pytest.approx(true, abs=0.02)
        assert d["sync"]["role_s"] == pytest.approx(0.3, abs=0.01)
        assert d["sync"]["pairs"] == 3 and d["sync"]["default"] is None

    def test_correct_twice_is_the_same(self):
        once = Y.correct(doc(game()))
        twice = Y.correct(copy.deepcopy(once))
        assert twice == once

    def test_the_raw_split_and_the_correction_are_kept(self):
        d = Y.correct(doc(game()))
        s = d["games"][0]["ends"][0]["shots"][0]
        assert s["long_split_raw_s"] - s["long_split_s"] == pytest.approx(s["long_split_sync_s"], abs=0.01)
        assert s["long_split_sync"] == "measured"
        assert s["sync"]["cam_split_s"] == pytest.approx(s["_true"], abs=0.02)

    def test_a_jump_mid_end_is_followed_within_three_shots(self):
        ends = game()
        jumped = dict(LAGS, top=0.05)              # the top panel jumps at shot 9 of end 2
        ends[1] = ("top", [shot_for("top", LAGS if i < 8 else jumped, v=v)
                           for i, v in enumerate(speeds())])
        d = Y.correct(doc(ends))
        end2 = d["games"][0]["ends"][1]["shots"]
        assert all(abs(s["long_split_s"] - s["_true"]) < 0.03 for s in end2[:6])
        assert all(abs(s["long_split_s"] - s["_true"]) < 0.03 for s in end2[11:])

    def test_an_outlier_does_not_move_its_neighbours(self):
        ends = game()
        ends[0][1][5]["sync"]["far_s"] += 1.1
        d = Y.correct(doc(ends))
        for got, true in splits(d):
            assert got == pytest.approx(true, abs=0.03)

    def test_a_shot_without_readings_takes_its_windows_value(self):
        ends = game()
        ends[0][1][5]["sync"]["far_s"] = None
        d = Y.correct(doc(ends))
        s = d["games"][0]["ends"][0]["shots"][5]
        assert s["long_split_s"] == pytest.approx(s["_true"], abs=0.02)
        assert s["sync"]["cam_split_s"] is None

    def test_a_thin_end_borrows_from_the_nearest_end_to_the_same_house(self):
        ends = game(n_ends=4)
        ends[2] = ("bottom", [shot_for("bottom", LAGS, v=v, far=(i < 2)) for i, v in enumerate(speeds(10))])
        d = Y.correct(doc(ends))
        e3 = d["games"][0]["ends"][2]
        assert e3["sync"]["source"] == "borrowed"
        assert all(s["long_split_sync"] == "borrowed" for s in e3["shots"])
        assert all(abs(s["long_split_s"] - s["_true"]) < 0.03 for s in e3["shots"])

    def test_one_direction_uses_the_framing_default(self):
        d = Y.correct(doc(game(n_ends=1, R0=0.20, S=0.22)))
        assert d["sync"]["default"] == "old" and d["sync"]["pairs"] == 0
        assert d["sync"]["role_s"] == pytest.approx(0.20)
        assert all(s["long_split_sync"] == "default" for s in d["games"][0]["ends"][0]["shots"])
        for got, true in splits(d):
            assert got == pytest.approx(true, abs=0.03)

    def test_a_reaimed_recording_gets_the_reaimed_default(self):
        d = Y.correct(doc(game(n_ends=1, R0=0.56, S=0.36), cal=REAIMED))
        assert d["sync"]["default"] == "reaimed" and d["sync"]["role_s"] == pytest.approx(0.56)

    def test_end_one_is_recomputed_once_end_two_arrives(self):
        ends = game(n_ends=2, R0=0.35)
        alone = Y.correct(doc(ends[:1]))
        both = Y.correct(doc(copy.deepcopy(ends)))
        first_alone = alone["games"][0]["ends"][0]["shots"][0]
        first_both = both["games"][0]["ends"][0]["shots"][0]
        assert first_alone["long_split_sync"] == "default"
        assert first_both["long_split_sync"] == "measured"
        assert first_both["long_split_s"] == pytest.approx(first_both["_true"], abs=0.02)

    def test_adjacent_same_house_ends_are_not_a_pair(self):
        ends = [("bottom", game(1)[0][1]), ("bottom", game(1)[0][1])]
        d = Y.correct(doc(ends))
        assert d["sync"]["pairs"] == 0 and d["sync"]["default"] == "old"

    def test_no_side_views_leaves_splits_raw_and_uncorrected(self):
        ends = game(n_ends=2)
        for _h, shots in ends:
            for s in shots:
                s["sync"] = None
        d = Y.correct(doc(ends, cal={}))
        for g in d["games"]:
            for e in g["ends"]:
                assert e["sync"]["source"] == "uncorrected"
                for s in e["shots"]:
                    assert s["long_split_s"] == s["long_split_raw_s"]
                    assert s["long_split_sync"] == "uncorrected"

    def test_a_document_from_before_has_no_raw_fields_and_is_left_alone(self):
        d = {"calibration": OLD, "games": [{"ends": [{"number": 1, "house": "top",
                                                     "shots": [{"long_split_s": 14.0}]}]}]}
        assert Y.correct(copy.deepcopy(d)) == d

    def test_a_split_moved_across_12_5_flips_draw_through_and_flashed(self):
        ends = game()
        # played to the top: e = top - right = +0.25, so 12.6 raw is 12.35 true
        flip = shot_for("top", LAGS, true_split=12.35, kind="draw_through")
        manual = dict(shot_for("top", LAGS, true_split=12.35, kind="draw_through"),
                      shot_type_source="manual")
        ends[1][1][3], ends[1][1][4] = flip, manual
        d = Y.correct(doc(ends))
        shots = d["games"][0]["ends"][1]["shots"]
        assert shots[3]["shot_type"] == "flashed"
        assert shots[4]["shot_type"] == "draw_through"

    def test_low_confidence_when_an_ends_errors_scatter(self):
        ends = game()
        for i, s in enumerate(ends[0][1]):
            s["sync"]["far_s"] += 0.15 if i % 2 else -0.15
        d = Y.correct(doc(ends))
        assert d["games"][0]["ends"][0]["sync"]["low_confidence"] is True
        assert d["games"][0]["ends"][1]["sync"]["low_confidence"] is False


class TestEndLine:
    def test_it_says_the_provisional_error_with_the_default_role(self):
        from types import SimpleNamespace
        shots = [SimpleNamespace(sync=Y.ShotSync(far_s=0.1, mid_s=0.2 + 0.20, v_mid_m_s=1.6,
                                                 t_far_panel_s=None, t_trail_s=None, v_far_m_s=None))
                 for _ in range(5)]
        line = Y.end_line(3, shots, "old")
        assert "end 3" in line and "-0.10" in line and "n 5" in line
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync_correct.py`
Expected: FAIL with `AttributeError: module ... has no attribute 'correct'`.

- [ ] **Step 3: Append the correction to `sync.py`**

```python
# --- the correction ------------------------------------------------------------

ROLE_V0_M_S = 1.6
WINDOW = 2                # shots either side: a 5-shot running median
OUTLIER_S = 0.3
MIN_PER_END = 3
LOW_CONFIDENCE_MAD_S = 0.08
SPEED_FIT_TOL_S = 0.15


def _mids(end) -> list:
    out = []
    for s in end.get("shots", []):
        sy = s.get("sync") or {}
        if sy.get("mid_s") is not None and sy.get("v_mid_m_s"):
            out.append((float(sy["mid_s"]), float(sy["v_mid_m_s"])))
    return out


def fit_speed_term(ends) -> float | None:
    """The role term's speed part: a pooled within-end regression of mid on
    1/v, over ends with MIN_PER_END readings, readings near their end's median."""
    xs, ys = [], []
    for end in ends:
        m = _mids(end)
        if len(m) < MIN_PER_END:
            continue
        med = statistics.median(d for d, _ in m)
        w = [1.0 / v for _, v in m]
        wbar = sum(w) / len(w)
        for (d, _v), wi in zip(m, w):
            if abs(d - med) <= SPEED_FIT_TOL_S:
                xs.append(wi - wbar)
                ys.append(d - med)
    den = sum(x * x for x in xs)
    if len(xs) < 2 * MIN_PER_END or den < 1e-6:
        return None
    return sum(x * y for x, y in zip(xs, ys)) / den


def fit_role(ends, framing):
    """``(S, R0, pairs, default)``. R0 is the median over adjacent ends played
    to different houses of their median mid at ROLE_V0_M_S; without such a
    pair, the framing's default (named in ``default``)."""
    default = DEFAULTS.get(framing)
    S = fit_speed_term(ends)
    if S is None:
        S = None if default is None else default[1]
    if S is None:
        return None, None, 0, None

    def at_v0(m):
        return statistics.median(d - S * (1.0 / v - 1.0 / ROLE_V0_M_S) for d, v in m)

    vals = []
    for a, b in zip(ends, ends[1:]):
        if a.get("house") == b.get("house"):
            continue
        ma, mb = _mids(a), _mids(b)
        if len(ma) >= MIN_PER_END and len(mb) >= MIN_PER_END:
            vals.append((at_v0(ma) + at_v0(mb)) / 2)
    if vals:
        return S, statistics.median(vals), len(vals), None
    if default is None:
        return None, None, 0, None
    return S, default[0], 0, framing


def shot_error(sy, S, R0) -> float | None:
    """arriving panel - hog camera for one shot, or None."""
    if not sy or S is None or R0 is None:
        return None
    far, mid, v = sy.get("far_s"), sy.get("mid_s"), sy.get("v_mid_m_s")
    if far is None or mid is None or not v:
        return None
    return float(far) - (float(mid) - (R0 + S * (1.0 / v - 1.0 / ROLE_V0_M_S)))


def end_stats(errs):
    """``(median, n, mad)`` of an end's errors after dropping outliers."""
    vals = [e for e in errs if e is not None]
    if not vals:
        return None, 0, None
    m = statistics.median(vals)
    kept = [e for e in vals if abs(e - m) <= OUTLIER_S]
    m = statistics.median(kept)
    return m, len(kept), statistics.median(abs(e - m) for e in kept)


def smooth(errs) -> list:
    """Each shot's correction: the median of its 5-shot window, readings over
    OUTLIER_S from that median dropped; a window with none takes the end's."""
    end_med = end_stats(errs)[0]
    out = []
    for i in range(len(errs)):
        win = [e for e in errs[max(0, i - WINDOW):i + WINDOW + 1] if e is not None]
        if not win:
            out.append(end_med)
            continue
        m = statistics.median(win)
        kept = [e for e in win if abs(e - m) <= OUTLIER_S] or win
        out.append(statistics.median(kept))
    return out


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _reclassify(shot) -> None:
    from curling_score.game import classify as K

    if shot.get("shot_type_source") != "auto" or shot.get("shot_type") not in (K.DRAW_THROUGH, K.FLASHED):
        return
    sp = shot.get("long_split_s")
    if sp is not None:
        shot["shot_type"] = K.DRAW_THROUGH if sp > K.SPLIT_HIT_MAX_S else K.FLASHED


def _apply(shot, corr, own, source) -> None:
    raw = shot.get("long_split_raw_s")
    sy = shot.get("sync")
    if isinstance(sy, dict):
        sy["cam_split_s"] = None if raw is None or own is None else round(raw - own, 2)
    if raw is None:
        shot["long_split_s"], shot["long_split_sync_s"], shot["long_split_sync"] = None, None, None
        return
    if corr is None:
        shot["long_split_s"], shot["long_split_sync_s"], shot["long_split_sync"] = raw, None, "uncorrected"
    else:
        shot["long_split_s"] = round(raw - corr, 2)
        shot["long_split_sync_s"] = round(corr, 3)
        shot["long_split_sync"] = source
    _reclassify(shot)


def correct(doc: dict) -> dict:
    """Rewrite every split from its raw value and the readings, in place, and
    return the document. Idempotent: it reads only ``long_split_raw_s`` and
    the readings, never what it wrote. A document made before splits were
    corrected (no ``long_split_raw_s`` anywhere) is left alone."""
    ends = [e for g in doc.get("games", []) for e in g.get("ends", [])]
    if not any("long_split_raw_s" in s for e in ends for s in e.get("shots", [])):
        return doc
    cal = doc.get("calibration") or {}
    framing = framing_of([(cal.get(k) or {}).get("tee_row") for k in ("left", "right")])
    S, R0, pairs, default = fit_role(ends, framing)
    stats = []
    for end in ends:
        errs = [shot_error(s.get("sync"), S, R0) for s in end.get("shots", [])]
        stats.append((errs, *end_stats(errs)))
    for i, end in enumerate(ends):
        errs, med, n, mad = stats[i]
        if n >= MIN_PER_END:
            corr, source = smooth(errs), ("default" if default else "measured")
        else:
            donors = sorted((j for j in range(len(ends)) if j != i
                             and ends[j].get("house") == end.get("house")
                             and stats[j][2] >= MIN_PER_END), key=lambda j: abs(j - i))
            if donors:
                corr, source = [stats[donors[0]][1]] * len(errs), "borrowed"
            else:
                corr, source = [None] * len(errs), "uncorrected"
        end["sync"] = {"split_error_s": _r(med), "mad_s": _r(mad), "n": n, "source": source,
                       "low_confidence": bool(mad is not None and mad > LOW_CONFIDENCE_MAD_S)}
        for shot, c, own in zip(end.get("shots", []), corr, errs):
            _apply(shot, c, own, source)
    doc["sync"] = {"role_s": _r(R0), "speed_term_m": _r(S), "pairs": pairs, "default": default}
    return doc


def end_line(number, shots, framing) -> str:
    """The worker log's line for one end, with the framing's default role --
    the document's correction may refine it once ends are thrown both ways."""
    R0, S = DEFAULTS.get(framing or "old", DEFAULTS["old"])
    errs = []
    for s in shots:
        sy = getattr(s, "sync", None)
        if sy is not None:
            errs.append(shot_error({"far_s": sy.far_s, "mid_s": sy.mid_s,
                                    "v_mid_m_s": sy.v_mid_m_s}, S, R0))
    med, n, mad = end_stats(errs)
    if med is None:
        return f"    end {number}: split sync -- (no readings)"
    return f"    end {number}: split sync {med:+.2f} s (n {n}, mad {mad:.2f}, default role)"
```

- [ ] **Step 4: Run both sync test files**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync.py tests/test_sync_correct.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/sync.py tests/test_sync_correct.py
git commit -m "sync: the correction, role term fitted per recording from ends thrown both ways, a running median per end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Timeline fields, the document hook, the pipeline wiring and version

**Files:**
- Modify: `src/curling_score/timeline.py`. In `build_end` (~line 207), add the
  fields. In `build_document` (~line 452), add the hook. Add a `_sync` helper
  near `_line`.
- Modify: `src/curling_score/analyze.py` (`build_one_end`, ~lines 508-540)
- Modify: `src/curling_score/version.py` (`PIPELINE_VERSION` and its history comment)
- Test: `tests/test_timeline.py`, `tests/test_linetime.py` (`TestAnalyzeCallsIt`)

**Interfaces:**
- Consumes: `Shot.sync` (Task 5), `sync.correct` and `sync.end_line`
  (Task 6), and `linetime.read_destinations` (Task 4).
- Produces: timeline shot keys `long_split_raw_s`, `long_split_sync_s`,
  `long_split_sync`, `sync`. Documents carry `sync`, and ends carry `sync`
  after `build_document`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_timeline.py`:

```python
class TestSplitSyncFields:
    def shot_with_split(self, sync=None):
        from types import SimpleNamespace
        from curling_score.game import sync as Y
        s = shot(1, "red", [])
        s.delivery = SimpleNamespace(t_enter=120.0, reason="rest", travel_m=4.0, track=[],
                                     speed_at=lambda: 0.3, came_to_rest=True, rest_x_m=0.0,
                                     rest_y_m=0.0)
        s.t_hog_s, s.v_hog_m_s = 100.0, 2.0
        s.far_crossing = split.FarCrossing(t=114.0, reach=0.0)
        s.sync = sync or Y.ShotSync(far_s=0.1, mid_s=0.3, v_mid_m_s=1.6, t_far_panel_s=114.0,
                                   t_trail_s=114.2, v_far_m_s=1.5)
        return s

    def test_build_end_keeps_the_raw_split_and_the_readings(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, [self.shot_with_split()])
        s = end["shots"][0]
        assert s["long_split_raw_s"] == s["long_split_s"] == 14.0
        assert s["sync"]["far_s"] == 0.1 and s["sync"]["mid_s"] == 0.3
        assert s["long_split_sync"] is None

    def test_build_document_corrects_the_splits(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, [self.shot_with_split()])
        doc = timeline.build_document(
            "v", "u", 1, 10.0, calibration={"left": {"tee_row": 472.0}, "right": {"tee_row": 483.0}},
            games=[{"ends": [end]}])
        assert doc["sync"]["default"] == "old"
        s = doc["games"][0]["ends"][0]["shots"][0]
        assert s["long_split_raw_s"] == 14.0
        assert doc["games"][0]["ends"][0]["sync"]["source"] in ("borrowed", "uncorrected", "default")

    def test_build_document_without_side_views_keeps_raw_splits(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, [self.shot_with_split(sync=None)])
        end["shots"][0]["sync"] = None
        doc = timeline.build_document("v", "u", 1, 10.0, calibration={}, games=[{"ends": [end]}])
        s = doc["games"][0]["ends"][0]["shots"][0]
        assert s["long_split_s"] == s["long_split_raw_s"] and s["long_split_sync"] == "uncorrected"
```

If `shot_with_split`'s delivery stand-in lacks an attribute that `build_end` or
`classify` reads, add that attribute to the `SimpleNamespace`. The tests are
about the split fields only. The `S.Shot` dataclass already accepts these
attributes, since dataclass instances take new attributes.

Append to `TestAnalyzeCallsIt` in `tests/test_linetime.py`:

```python
    def test_the_shared_read_runs_before_the_line_pass_and_sync_after_the_far_line(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        assert src.index("linetime.read_destinations(") < src.index("linetime.time_lines(")
        assert src.index("fartime.time_far_crossings(") < src.index("sync.measure_shots(")
        assert src.index("sync.measure_shots(") < src.index("timeline.build_end(")
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py::TestSplitSyncFields tests/test_linetime.py::TestAnalyzeCallsIt`
Expected: FAIL with `KeyError: 'long_split_raw_s'` and `ValueError: substring not found`.

- [ ] **Step 3: Add the timeline fields and the hook**

In `timeline.py`, add `from curling_score.game import sync as sync_mod` to the
imports from `curling_score.game`. In `build_end`'s shot dict, directly after
the `"long_split_s": (...)` entry, add:

```python
                # The split as the two feeds read it; `long_split_s` above is
                # rewritten from this by `sync.correct` in `build_document`,
                # with the correction and where it came from beside it.
                "long_split_raw_s": (
                    None if sp is None else round(float(sp.seconds), 2)
                ),
                "long_split_sync_s": None,
                "long_split_sync": None,
                "sync": _sync(getattr(s, "sync", None)),
```

Add the helper next to `_line`:

```python
def _sync(sy) -> dict | None:
    """A shot's camera-sync readings (`game/sync.py`), or None."""
    if sy is None:
        return None
    r = lambda v: None if v is None else round(float(v), 3)
    return {"far_s": r(sy.far_s), "mid_s": r(sy.mid_s), "v_mid_m_s": r(sy.v_mid_m_s),
            "t_far_panel_s": r(sy.t_far_panel_s), "t_trail_s": r(sy.t_trail_s),
            "v_far_m_s": r(sy.v_far_m_s), "cam_split_s": None}
```

At the end of `build_document`, replace `return doc` with:

```python
    # Every split corrected for the camera offsets, from the raw splits and
    # readings the ends carry -- here, so the VOD run and every live publish
    # get the same pass, and a live end 1 is refined once end 2 is built.
    return sync_mod.correct(doc)
```

- [ ] **Step 4: Wire the pipeline in `analyze.build_one_end`**

Add `sync` to the `from curling_score.game import ...` line in `analyze.py`.
Directly before `t_lines = time.monotonic()`, add:

```python
        # Both long cameras after the hog line from one decode: the line
        # pass's path and the camera-sync readings work from it.
        linetime.read_destinations(
            shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
            sideviews[hogtime.CAMERA_FOR[end.house]], model=ctx.line_model)
```

Directly after `fartime.time_far_crossings(shots, near_line=far.hog_line, far_line=setup.hog_line)`, add:

```python
    if sideviews is not None:
        framing = sync.framing_of([v.tee_row for v in sideviews.values() if v is not None])
        sync.measure_shots(shots, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]], framing)
        progress(sync.end_line(end.number, shots, framing))
```

Leave the `linetime.time_lines(` call text as it is. `TestAnalyzeCallsIt`
checks its arguments.

- [ ] **Step 5: Bump the pipeline version**

In `src/curling_score/version.py`, add a history entry above
`PIPELINE_VERSION` and bump it. Use the commit day's date, or `.5` if it is
still 2026-10-02:

```python
# 2026.10.03: splits are corrected for the composite's camera offsets
# (`game/sync.py`): the two long cameras' mid-sheet overlap and the far hog
# line link all four feeds in every end, and `long_split_s` is the raw split
# (`long_split_raw_s`) less the arriving panel - hog camera offset, fitted per
# recording from ends thrown both ways. On 10/01's Mens sheets 1, 2 and 5 the
# raw splits were off by up to 0.45 s.
PIPELINE_VERSION = "2026.10.03"
```

- [ ] **Step 6: Run the affected tests**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py tests/test_linetime.py tests/test_sync.py tests/test_sync_correct.py tests/test_analyze_split.py tests/test_analyze_side.py tests/test_live_session.py tests/test_version.py tests/test_viewer_js.py tests/test_classify.py`
Expected: PASS. If a test pins the exact set of shot keys or a full document,
add the four new keys there. Don't change any other assertion.

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/timeline.py src/curling_score/analyze.py src/curling_score/version.py tests/test_timeline.py tests/test_linetime.py
git commit -m "timeline: splits corrected for the camera offsets on every document built, raw split and readings kept

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Study scripts into the repo, worker A/B #1 and the live timing check

**Files:**
- Create: `scripts/sync/study/` (copies of `~/curling-work/sync-1001/{probe.py,probe2.py,probe_common.py,analyse.py,combine.py,served_pairs.py}` plus `README.md`)
- Create: `scripts/sync/expected_1001.json`
- Create: `scripts/sync/compare_runs.py`

**Interfaces:**
- Consumes: A/B outputs `/data/wdd/scratch/runs/<vid>-{base,new}/timeline.json`.
- Produces: `compare_runs.py BASE.json NEW.json [--expected expected_1001.json]`.
  It prints shot-list parity, every changed field outside the split and sync
  set, the per-end correction against the study, and each game's top−bottom
  split gap.

- [ ] **Step 1: Copy the study in and write its expected per-end values**

```bash
mkdir -p scripts/sync/study
cp ~/curling-work/sync-1001/{probe.py,probe2.py,probe_common.py,analyse.py,combine.py,served_pairs.py} scripts/sync/study/
cat > scripts/sync/study/README.md <<'EOF'
The 2026-10-02 four-feed camera sync study that `game/sync.py` is built on
(see docs/superpowers/specs/2026-10-02-split-sync-correction-design.md).
probe*.py run on the worker in a throwaway container; analyse.py/combine.py
turn their boxes into per-end offsets. Kept for re-running the comparison.
EOF
cd ~/curling-work/sync-1001 && python3 -c "
import json, sys; sys.path.insert(0, '.')
from combine import lags
runs = {'0SWB4g3SJoE': [('tl_s_1YVfpmRIatwsOMk5S.json','boxes_s_1YVfpmRIatwsOMk5S.jsonl'),('tl_s_0PY7J141eDtEs1q2I.json','boxes_s_0PY7J141eDtEs1q2I.jsonl')],
        '32Dkqsf2t2M': [('tl_s_0VZwQ4EBi51N7gKYH.json','boxes_s_0VZwQ4EBi51N7gKYH.jsonl'),('tl_s_1OvJ2nraonGzf2XFX.json','boxes_s_1OvJ2nraonGzf2XFX.jsonl')],
        'dea75KXakFc': [('tl_s_0TW1uTp9PyboXeRNE.json','boxes_s_0TW1uTp9PyboXeRNE.jsonl'),('tl_s_0eLnRvTydhAUoW9JA.json','boxes_s_0eLnRvTydhAUoW9JA.jsonl')]}
out = {vid: [{k: r[k] for k in ('game','end','house','t','split_err')} for r in lags(vid, p)] for vid, p in runs.items()}
json.dump(out, open('/home/tcuser/src/curling_score/.claude/worktrees/split-sync/scripts/sync/expected_1001.json','w'), indent=1)
" > /dev/null && cd -
```

Expected: `scripts/sync/expected_1001.json` with a per-end `split_err` for each
of the three recordings.

- [ ] **Step 2: Write `scripts/sync/compare_runs.py`**

```python
#!/usr/bin/env python
"""Compare a base and a new timeline of one recording for the split sync change.

    python scripts/sync/compare_runs.py BASE.json NEW.json [--expected scripts/sync/expected_1001.json]

1. Shot lists: every rock (colour, rest time) must be in both; ends are
   matched by start time within 60 s, since runs jitter end boundaries.
2. Fields: anything that changed other than the split/sync fields and a
   draw_through/flashed flip is listed -- it should be nothing.
3. Per end: the new correction (median) against the study's split error, and
   its source.
4. Per game: the top-bottom gap of corrected draw splits, raw and corrected.
"""
import argparse
import json
import statistics as st

SPLIT_KEYS = {"long_split_s", "long_split_raw_s", "long_split_sync_s", "long_split_sync", "sync",
              "youtube_url"}


def ends(doc):
    return [(gi, e) for gi, g in enumerate(doc["games"]) for e in g["ends"]]


def rocks(doc):
    return {(s["color"], round(s["t_rest_s"] or -1, 1)) for _gi, e in ends(doc) for s in e["shots"]
            if not s.get("missing")}


def match(base, new):
    out = []
    for gi, e in ends(new):
        best = min(ends(base), key=lambda x: abs(x[1]["start_s"] - e["start_s"]), default=None)
        if best and abs(best[1]["start_s"] - e["start_s"]) <= 60:
            out.append((best[1], e))
    return out


def field_changes(base, new):
    changes = []
    for b, n in match(base, new):
        for sb, sn in zip(b["shots"], n["shots"]):
            for k in set(sb) | set(sn):
                if k in SPLIT_KEYS or sb.get(k) == sn.get(k):
                    continue
                if k == "shot_type" and {sb.get(k), sn.get(k)} <= {"draw_through", "flashed"}:
                    continue
                changes.append((n["number"], sn["number"], k, sb.get(k), sn.get(k)))
    return changes


def gap(doc, key):
    out = []
    for gi, g in enumerate(doc["games"]):
        by = {"top": [], "bottom": []}
        for e in g["ends"]:
            for s in e["shots"]:
                v = s.get(key)
                if v and 12 < v < 17 and s.get("shot_type") in ("draw", "guard", "freeze"):
                    by[e["house"]].append(v)
        if by["top"] and by["bottom"]:
            out.append(round(st.median(by["top"]) - st.median(by["bottom"]), 2))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("base")
    ap.add_argument("new")
    ap.add_argument("--expected")
    args = ap.parse_args()
    base, new = (json.load(open(p)) for p in (args.base, args.new))
    vid = new["source"]["video_id"]
    rb, rn = rocks(base), rocks(new)
    print(f"{vid}: rocks base {len(rb)} new {len(rn)}; only base {sorted(rb - rn)[:5]}; only new {sorted(rn - rb)[:5]}")
    ch = field_changes(base, new)
    print(f"non-split field changes: {len(ch)}")
    for c in ch[:20]:
        print("   ", c)
    exp = json.load(open(args.expected)).get(vid, []) if args.expected else []
    print(f"document sync: {new.get('sync')}")
    for gi, e in ends(new):
        sy = e.get("sync") or {}
        near = min(exp, key=lambda x: abs(x["t"] - e["start_s"]), default=None)
        want = near["split_err"] if near and abs(near["t"] - e["start_s"]) <= 60 else None
        got = sy.get("split_error_s")
        diff = "" if want is None or got is None else f"  diff {got - want:+.3f}"
        print(f"  g{gi + 1} e{e['number']:<2d} {e['house']:6s} new {got} ({sy.get('source')}, n {sy.get('n')}, "
              f"mad {sy.get('mad_s')})  study {want}{diff}")
    print(f"top-bottom draw split gap per game: raw {gap(new, 'long_split_raw_s')} corrected {gap(new, 'long_split_s')}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run A/B #1 on the worker, outside the watched leagues' windows**

Watched windows (Pacific time):
- Sun 11:00-23:30
- Mon 18:00-23:30
- Tue 16:30-00:00
- Wed 18:30-00:00
- Thu 09:30-15:00 and 17:00-00:00
- Fri 18:00-23:30

Check the worker log for an unfinished job first.

```bash
cd /home/tcuser/src/curling_score/.claude/worktrees/split-sync
ssh administrator@10.0.0.182 'docker logs --since 30m deploy-worker-1 2>&1 | grep -E "job j_" | tail -3'
ssh administrator@10.0.0.182 'rm -rf /data/wdd/scratch/code-base /data/wdd/scratch/code-new && mkdir -p /data/wdd/scratch/code-base /data/wdd/scratch/code-new'
git archive main src | ssh administrator@10.0.0.182 'tar -x -C /data/wdd/scratch/code-base'
git archive HEAD src | ssh administrator@10.0.0.182 'tar -x -C /data/wdd/scratch/code-new'
ssh -f administrator@10.0.0.182 'cd /data/wdd/scratch && nohup sh -c "
  ./run_ab.sh 0SWB4g3SJoE \"10/01 - Sheet 1 - Thursday Mens League 2026-2027\" 16195 1;
  ./run_ab.sh 32Dkqsf2t2M \"10/01 - Sheet 2 - Thursday Mens League 2026-2027\" 16195 2;
  ./run_ab.sh dea75KXakFc \"10/01 - Sheet 5 - Thursday Mens League 2026-2027\" 16195 5" > runs/sync-ab.out 2>&1 < /dev/null &'
```

Expected: six lines in `/data/wdd/scratch/runs/progress.txt` after about
65 min. Then:

```bash
ssh administrator@10.0.0.182 'docker run --rm -v /data/wdd/scratch:/scratch --entrypoint sh curling-worker:local -c "chmod -R a+rX /scratch/runs"'
mkdir -p ~/curling-work/sync-ab1
for vid in 0SWB4g3SJoE 32Dkqsf2t2M dea75KXakFc; do for side in base new; do
  scp -q administrator@10.0.0.182:/data/wdd/scratch/runs/$vid-$side/timeline.json ~/curling-work/sync-ab1/$vid-$side.json; done; done
for vid in 0SWB4g3SJoE 32Dkqsf2t2M dea75KXakFc; do
  python3 scripts/sync/compare_runs.py ~/curling-work/sync-ab1/$vid-base.json ~/curling-work/sync-ab1/$vid-new.json --expected scripts/sync/expected_1001.json; done
```

Expected, before c is fitted:
- Same rocks in both.
- `non-split field changes: 0`.
- Per-end `diff` within ±0.08 s of the study. The study used 15 fps; this uses
  5 fps.
- Sheet 5's corrected gap is steadier between games than its raw gap.

If field changes appear, stop and report them; they break a global constraint.

- [ ] **Step 4: Live replay timing, base against new**

Use the first 2700 s of sheet 1's recording, already cut for the 10/02
calibration check: `/data/wdd/scratch/flags1001/0SWB4g3SJoE-2700.ts`.
Replay it at double speed with each code tree. The lane still builds each end
as it becomes due, so per-end build times stay comparable. Each run takes
~23 min.

```bash
ssh -f administrator@10.0.0.182 'cd /data/wdd/scratch && nohup sh -c "for side in base new; do
  docker run --rm --gpus all --shm-size 2g -v /data/wdd/curling-cache:/data/cache -v /data/wdd/scratch:/scratch \
    -v /data/wdd/scratch/code-\$side/src:/code/src:ro -e PYTHONPATH=/code/src -e YOLO_CONFIG_DIR=/tmp \
    --entrypoint python curling-worker:local -m curling_score.cli live-replay \
    /scratch/flags1001/0SWB4g3SJoE-2700.ts --speed 2 --out /scratch/runs/replay-sync-\$side \
    > runs/replay-sync-\$side.log 2>&1; done" > /dev/null 2>&1 < /dev/null &'
# when both logs end:
ssh administrator@10.0.0.182 'grep -E "end [0-9]+: (lines|split sync)" /data/wdd/scratch/runs/replay-sync-*.log'
```

If `python -m curling_score.cli` is not the entry point, use the image's
`curling-score` script as `/scratch/flags1001/replay_recal.py` does (it calls
`cli.main(["live-replay", ...])`).

Expected:
- The new run's `lines n/N in X s` per end grows by no more than ~5%, plus the
  new `split sync` line per end.
- In `/scratch/runs/replay-sync-new/`'s published documents, end 1's splits
  read `long_split_sync: "default"` until end 2 is built, and `"measured"`
  in every later publish.

If time grows by more than 5%, report it before going on. A five-stream
rehearsal is then needed (`~/curling-work/live-spike`).

- [ ] **Step 5: Commit**

```bash
git add scripts/sync/study scripts/sync/expected_1001.json scripts/sync/compare_runs.py
git commit -m "sync: the four-feed study's scripts and expected values, and a base-vs-new comparison for the A/B

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Fit the receding offset from the hand marks, then A/B #2

This needs Task 2's marks and Task 8's new-run timelines.

**Files:**
- Create: `scripts/sync/fit_receding.py`
- Modify: `src/curling_score/game/sync.py` (`RECEDING_C_S`)
- Test: `tests/test_sync_marks.py` (extend)

**Interfaces:**
- Consumes:
  - `datasets/hogmarks/*-receding.json` (Task 2), with rocks carrying
    `anchor_s, color, house, receding_s, arriving_s`.
  - New-run timelines (Task 8), whose shots carry `t_enter_s, color,
    sync.{t_trail_s, v_far_m_s, t_far_panel_s}`.
- Produces:
  - `fit_receding.pairs(marks, doc) -> list[dict]` matches rocks by colour and
    `|t_enter_s − anchor_s| ≤ 1.0`.
  - `fit_receding.summarise(rows) -> dict`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sync_marks.py`:

```python
fit_receding = load("fit_receding")


class TestFitReceding:
    def doc(self):
        return {"calibration": {"left": {"tee_row": 472.0}, "right": {"tee_row": 483.0}},
                "games": [{"ends": [{"number": 1, "house": "top", "shots": [
                    {"color": "red", "t_enter_s": 100.0,
                     "sync": {"t_trail_s": 99.80, "v_far_m_s": 1.6, "t_far_panel_s": 99.50}},
                    {"color": "yellow", "t_enter_s": 140.0,
                     "sync": {"t_trail_s": 139.70, "v_far_m_s": 1.4, "t_far_panel_s": 139.45}}]}]}]}

    def test_rocks_are_matched_by_colour_and_time_not_end_numbers(self):
        marks = {"rocks": [{"anchor_s": 100.3, "color": "red", "receding_s": 99.84, "arriving_s": 99.52},
                           {"anchor_s": 100.3, "color": "yellow", "receding_s": 1.0, "arriving_s": 1.0}]}
        rows = fit_receding.pairs(marks, self.doc())
        assert len(rows) == 1
        assert rows[0]["c"] == pytest.approx(0.04)
        assert rows[0]["panel_residual"] == pytest.approx(0.02)
        assert rows[0]["framing"] == "old"

    def test_the_summary_is_a_median_per_framing(self):
        rows = [{"framing": "old", "c": 0.04, "panel_residual": 0.0},
                {"framing": "old", "c": 0.06, "panel_residual": 0.02},
                {"framing": "reaimed", "c": 0.10, "panel_residual": -0.01}]
        got = fit_receding.summarise(rows)
        assert got["old"]["c"] == pytest.approx(0.05) and got["old"]["n"] == 2
        assert got["reaimed"]["c"] == pytest.approx(0.10)
```

Add `import pytest` at the top of the file if it isn't there.

- [ ] **Step 2: Run it to see it fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync_marks.py::TestFitReceding`
Expected: FAIL with `FileNotFoundError` for `fit_receding.py`.

- [ ] **Step 3: Write `scripts/sync/fit_receding.py`**

```python
#!/usr/bin/env python
"""Fit the receding stone's offset c from the hand marks, per framing.

    python scripts/sync/fit_receding.py --marks datasets/hogmarks --runs ~/curling-work/sync-ab1

For each marked rock, against the new run's readings of the same rock
(matched by colour and time, because end numbers jitter between runs):

  c              = hand receding mark - model trailing-edge time
  panel_residual = hand arriving mark - the panel's far crossing
  hand_offset    = hand arriving - (hand receding - 2R/v), the arriving panel -
                   camera offset made by hand; compare far_s with c applied.
"""
import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from curling_score.game import sync as Y  # noqa: E402


def pairs(marks, doc) -> list[dict]:
    cal = doc.get("calibration") or {}
    framing = Y.framing_of([(cal.get(k) or {}).get("tee_row") for k in ("left", "right")])
    shots = [s for g in doc["games"] for e in g["ends"] for s in e["shots"]
             if s.get("t_enter_s") is not None and (s.get("sync") or {}).get("t_trail_s") is not None]
    rows = []
    for rock in marks["rocks"]:
        near = [s for s in shots if s["color"] == rock["color"]
                and abs(s["t_enter_s"] - rock["anchor_s"]) <= 1.0]
        if not near:
            continue
        s = min(near, key=lambda s: abs(s["t_enter_s"] - rock["anchor_s"]))
        sy = s["sync"]
        row = {"framing": framing, "anchor_s": rock["anchor_s"], "color": rock["color"],
               "c": None, "panel_residual": None, "hand_offset": None}
        if rock.get("receding_s") is not None:
            row["c"] = round(rock["receding_s"] - sy["t_trail_s"], 3)
        if rock.get("arriving_s") is not None and sy.get("t_far_panel_s") is not None:
            row["panel_residual"] = round(rock["arriving_s"] - sy["t_far_panel_s"], 3)
        if rock.get("receding_s") is not None and rock.get("arriving_s") is not None and sy.get("v_far_m_s"):
            row["hand_offset"] = round(rock["arriving_s"] - (rock["receding_s"] - Y.DIAMETER_M / sy["v_far_m_s"]), 3)
            row["model_trail_s"], row["v_far_m_s"], row["t_far_panel_s"] = sy["t_trail_s"], sy["v_far_m_s"], sy["t_far_panel_s"]
        rows.append(row)
    return rows


def summarise(rows) -> dict:
    out = {}
    for framing in sorted({r["framing"] for r in rows if r["framing"]}):
        mine = [r for r in rows if r["framing"] == framing]
        cs = [r["c"] for r in mine if r["c"] is not None]
        res = [r["panel_residual"] for r in mine if r["panel_residual"] is not None]
        out[framing] = {"n": len(cs), "c": st.median(cs) if cs else None,
                        "c_mad": st.median(abs(c - st.median(cs)) for c in cs) if cs else None,
                        "panel_residual": st.median(res) if res else None}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--marks", required=True)
    ap.add_argument("--runs", required=True, help="directory of <vid>-new.json")
    args = ap.parse_args()
    rows = []
    for path in sorted(Path(args.marks).glob("*-receding.json")):
        marks = json.loads(path.read_text())
        run = Path(args.runs) / f"{marks['video_id']}-new.json"
        rows += pairs(marks, json.loads(run.read_text()))
    summary = summarise(rows)
    print(json.dumps(summary, indent=1))
    for framing, s in summary.items():
        if s["c"] is None:
            continue
        agree = []
        for r in rows:
            if r["framing"] == framing and r["hand_offset"] is not None:
                far_c = r["t_far_panel_s"] - (r["model_trail_s"] + s["c"] - Y.DIAMETER_M / r["v_far_m_s"])
                agree.append(far_c - r["hand_offset"])
        if agree:
            print(f"{framing}: far reading with c minus the hand offset: median {st.median(agree):+.3f} s, "
                  f"worst {max(agree, key=abs):+.3f} s over {len(agree)} rocks")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sync_marks.py`
Expected: PASS.

- [ ] **Step 5: Fit c on the real marks and set it**

```bash
python3 scripts/sync/fit_receding.py --marks datasets/hogmarks --runs ~/curling-work/sync-ab1
```

Expected:
- `old` and `reaimed` entries, each with n ≥ 10 and `c_mad` ≤ 0.05.
- A far-vs-hand median within ±0.03 s.
- If `panel_residual` is beyond ±0.05 s, the split's own end is off on this
  season's framing. Report that as a finding; don't fix it in this plan.

Set the fitted values in `src/curling_score/game/sync.py`, rounded to 0.01 s,
with a comment naming the dataset and n:

```python
# Hand marks against the detector's box bottom for a receding stone, per
# framing: datasets/hogmarks/*-receding.json (10/01 Mens sheets 1, 2, 5),
# median over n rocks each. Fitted by scripts/sync/fit_receding.py.
RECEDING_C_S = {"old": <fitted old c>, "reaimed": <fitted reaimed c>}
```

Replace the two placeholders with the printed medians. If a framing had fewer
than 10 marked rocks, use the other framing's value and say so in the comment.

- [ ] **Step 6: A/B #2 (new code only) and the success criteria**

Re-archive HEAD into `code-new` as in Task 8 Step 3, and run `run_analyze.py`
for `new` only on the three videos. You can reuse the base timelines from
A/B #1. Then run `compare_runs.py` for each video.

Expected (the spec's success criteria):
- The same rocks, and 0 non-split field changes.
- Each end's correction within ~0.05 s of the study after allowing for `c`.
  Every end shifts by the same `c`, so check that the end-to-end pattern
  matches.
- Sheet 5's corrected top−bottom gap is no longer ~0.9 s apart between games.
  Sheets 1 and 2 stay within ±0.05 s.

- [ ] **Step 7: Commit**

```bash
git add scripts/sync/fit_receding.py tests/test_sync_marks.py src/curling_score/game/sync.py
git commit -m "sync: the receding stone's offset fitted from hand marks, per framing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Merge and deploy (only on the user's go-ahead)

**Files:** none new

- [ ] **Step 1: Report and ask.** Give the user the A/B and live-timing
  results and the fitted `c`. Ask whether to merge and deploy. Stop here
  until they say yes.
- [ ] **Step 2: Merge** `split-sync` into main from the worktree:
  `git -C /home/tcuser/src/curling_score merge --no-ff split-sync`. Check
  first that the main checkout has no staged changes from the other session.
- [ ] **Step 3: Deploy the worker** from a clean worktree of the merged main:
  - Rsync with `--exclude deploy/worker.env --exclude .git --exclude .venv --exclude node_modules --exclude 'out*'`
    and never `--delete`.
  - Before any rebuild, check the worker log for an unfinished
    `job j_...: video`.
  - Run `docker compose -f docker-compose.worker.yml build`, then `up -d`
    once no job is claimed.
  - Confirm inside the running container that `sync.RECEDING_C_S` has the
    fitted values.
- [ ] **Step 4: Deploy the API**:
  - Copy `firebase_api_key.json` into the clean worktree.
  - Compare the script's `model_id` one-liner with the live `MODEL_ID`.
  - Run `PROJECT_ID=curling-stats-508323 REGION=us-west1 PUBLIC_BASE_URL=https://curling.dimmit.net ./deploy/deploy-api.sh`
    and check for `accounts: sign-in is live`.
  - If auto mode blocks it, hand the user the command.
- [ ] **Step 5: Reprocessing is a separate question.** Ask the user which hosted
  charts to reprocess. Cached videos take ~4 min each; others need VOD
  downloads, which need a fresh ask.
- [ ] **Step 6: Update the memory notes**: `camera-sync-2026-27.md`,
  `deploying-curling-chart.md` if anything new bit, and `live-games.md`
  (the first live night to check is Sunday after the deploy; look for
  `split sync` lines).
