# Slide, Release and Final Lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every delivered rock a slide line, a release line and a final line, each read at the broom, plus slide-vs-release. Retire the line check. Show the new figures on the game shot detail and on a practice card that carries over PR #2's features. Separately, study a 9 ft rotation window.

**Architecture:** `linetime` fits the final line over 6.401–9.144 m. It keeps today's 6.4–10 m fit internally for everything else that fit drives, and keeps the raw 30 fps hog-line track on the in-memory `Line`. A new pure module, `game/aimlines.py`, runs after `releasetime.time_releases`. It fits the slide and release lines from `line.delivery`, `line.track` and `line.release`, works out each line's error at the broom, and attaches `slide`, `release_line` and `set` to the line. The timeline (schema 10) and the practice throw record serialise them with one shared helper. The viewer formats the stored numbers: `core/line.mjs` for the game page, `core/practice.mjs` for the card.

**Tech Stack:** Python 3 (numpy, dataclasses, pytest), ES modules + React 19 bundled by `frontend/build.mjs` (esbuild), node for the JS tests.

**Spec:** `docs/superpowers/specs/2026-10-31-slide-release-final-lines-design.md`

## Global Constraints

- All work is in `/home/tcuser/src/curling_score/.claude/worktrees/line-windows`, on branch `line-windows`. Never write under `/home/tcuser/src/curling_score` outside this worktree; another session works there.
- Frame: metres from the destination tee, `+y` up-sheet toward the thrower, `+x` the thrower's right. Distance past the throwing tee is `d = TEE_Y - y` with `TEE_Y = C.TEE_TO_TEE_M` (34.747).
- `NINE_FT_M = 9 * 0.3048` (2.7432). The slide starts at `SLIDE_FROM_M = -(C.TEE_TO_HACKLINE_M + C.TEE_TO_BACKLINE_M) / 2` (−2.7435).
- Final window: `d` from 6.401 to 9.1442, at least 15 points over at least 2.0 m. Release window: `d` from the release to the release + 2.7432, at least 15 points over at least 2.0 m. Slide window: `d` from −2.7435 to the release, at least 8 points over at least 2.0 m.
- Today's fit (`FIT_PAST_TEE_M = (6.401, 10.0)`, 15 points over 2.5 m) stays as the pass's internal line. It decides whether a rock has a `line`, seeds `chain`, and drives `thrown_at`.
- Curl comes from the final fit, or from the internal fit when the final is refused. This is a planning clarification of the spec: curl drives the in-turn/out-turn call, which the spec says must not change.
- A set is shown only when both lines are shown and `|set| > SET_SIGMAS * sqrt(se_slide² + se_release²)`, with `SET_SIGMAS = 2.0`. A line is shown only when `se ≤ SE_MAX_M`. `SE_MAX_M = 0.15` is provisional; Task 5 calibrates it.
- Timeline `SCHEMA_VERSION` 9 → 10. Bump `PIPELINE_VERSION` in `src/curling_score/version.py` to `2026.10.31.1`.
- Game page wording uses `feetInches` and `ON_M` ("On the broom" within 0.10 m). The practice card uses whole inch marks (`9"`), "to the broom" within 1″ (`ON_BROOM_IN = 1.0`), and the set as `+7"` / `−7"` (or `7" right` with no curl).
- The full pytest suite OOMs on this laptop (exit 137). Run the files named in each task.
- After any `frontend/` change, rebuild with `cd frontend && npm run build` and commit `src/curling_score/service/static/` together with `frontend/.buildstamp.json`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A broomless rock (doubles, solo practice).** Each line is pinned at the tee, `at_broom` is null, and nothing reads `null.miss_m`. Tested in Task 2 (`test_broomless_lines_are_pinned_at_the_tee`) and Task 6 (`test_broomless_rock_shows_dashes_for_three_lines`).
2. **A rock with no release read.** It has a final line only. The slide, release and set are null, and both pages say "no release read", not "not measured". Tested in Task 2 (`test_no_release_point_gives_no_slide_or_release`), Task 6 and Task 7.
3. **A release late enough that its 9 ft window runs past the delivery path** (`d` > 7.9 m). The track continues it, and the seam samples are not counted twice. Tested in Task 2 (`test_release_window_continues_on_the_track_past_the_delivery`).
4. **An old chart (schema 9) or an old practice record.** It shows today's figures with no empty Slide or Release slots, and a stored `confirmed` is ignored. Tested in Task 6 (`test_schema_9_chart_keeps_its_six_figures`) and Task 7 (`test_old_record_has_no_aim_figures`).
5. **A final window refused while the internal fit stands.** The rock keeps its delivery, release, path and curl. Final shows "–" with "too few sightings past the hog line", and that is never mistaken for "no broom held". Tested in Task 1 (`test_refused_final_keeps_the_line_and_its_curl`), Task 3 (`test_refused_final_serialises_its_marker`) and Task 6.

---

### Task 1: Final line over 9 ft past the hog line; line check removed

**Files:**
- Modify: `src/curling_score/game/linetime.py` (constants at lines 40–55, `Line` at 67–83, `fit_line` at 88–98, `confirmed_by` at 131–140 to be removed, `measure` at 156–180, `time_lines` at 531–592)
- Test: `tests/test_linetime.py`

**Interfaces:**
- Produces:
  - `NINE_FT_M = 9 * 0.3048`
  - `FINAL_PAST_TEE_M = (C.TEE_TO_HOGLINE_M, C.TEE_TO_HOGLINE_M + NINE_FT_M)`, `FINAL_MIN_N = 15`, `FINAL_MIN_SPAN_M = 2.0`
  - `fit_line(track, window=FIT_PAST_TEE_M, min_n=FIT_MIN_N, min_span=FIT_MIN_SPAN_M) -> Fit | None`
  - `final_fit(track) -> Fit | None`, which is `fit_line(track, FINAL_PAST_TEE_M, FINAL_MIN_N, FINAL_MIN_SPAN_M)`
  - `measure(fit, track, start, broom, rest=None, path=(), delivery=(), *, final: Fit | None) -> Line`
  - `Line` changes:
    - `confirmed` is removed.
    - `at_hog_x: float | None`, `fit_n: int | None`, `fit_rms: float | None` (all None when the final is refused).
    - New fields with defaults:
      - `final_refused: bool = False`
      - `track: tuple = field(default=(), repr=False, compare=False)`: the raw `hog_track` samples `(t, x, y, yp)`, 30 fps, not serialised
      - `slide: object = None`, `release_line: object = None`, `set: object = None` (filled in by Task 2)

- [ ] **Step 1: Write the failing tests** in `tests/test_linetime.py`. Delete the `confirmed_by` tests (lines ~105–127) and any `confirmed=` assertion. Add:

```python
def test_final_window_is_nine_feet_past_the_hog_line():
    assert L.FINAL_PAST_TEE_M == pytest.approx((6.401, 6.401 + 2.7432))
    # Samples every 0.05 m from 6.0 to 11.0 m; x = 0.01 * yp inside 6.401-9.144, 1.0 beyond.
    track = [(i / 30, 0.01 * yp if yp <= 9.1442 else 1.0, L.TEE_Y - yp, yp)
             for i, yp in enumerate(np.arange(6.0, 11.0, 0.05))]
    f = L.final_fit(track)
    assert f is not None and f.rms < 1e-6          # only the 9 ft window was used

def test_refused_final_keeps_the_line_and_its_curl():
    internal = L.Fit(a=0.0, b=0.0, n=40, rms=0.002)
    line = L.measure(internal, track=[], start=None, broom=(-0.5, 0.0), rest=(0.3, 0.0),
                     final=None)
    assert line.final_refused is True
    assert line.miss is None and line.at_broom_x is None and line.at_hog_x is None
    assert line.side is None and line.fit_n is None
    assert line.curl == "right"                    # from the internal fit

def test_the_final_fit_supplies_the_figures():
    internal = L.Fit(a=0.0, b=0.0, n=40, rms=0.002)
    final = L.Fit(a=-0.2, b=0.0, n=30, rms=0.001)
    line = L.measure(internal, track=[], start=None, broom=(0.0, 0.0), rest=(0.3, 0.0),
                     final=final)
    assert line.miss == pytest.approx(-0.2) and line.fit_n == 30
    assert line.final_refused is False and not hasattr(line, "confirmed")
```

Also add `test_time_lines_keeps_the_raw_track`. Use the existing `time_lines` fixture of `test_a_shot_with_a_crossing_and_a_broom_gets_a_line` and assert `len(shot.line.track) == len(L.hog_track(crossing, view))`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_linetime.py -q`. Expected: the new tests FAIL (no `FINAL_PAST_TEE_M`, `measure` has no `final`).

- [ ] **Step 3: Implement** in `linetime.py`:
  - Add the constants, then generalise `fit_line` and add `final_fit`.
  - Delete `CONFIRM_*`, `confirmed_by` and the `Line.confirmed` field.
  - In `measure`, compute `at_hog_x`, `at_hog_offset`, `at_broom_x`/`miss`/`at_tee_x`, `side`, `fit_n` and `fit_rms` from `final` when it is not None, and None otherwise. Set `final_refused=final is None`. Take `curl` from `curl_of(final or fit, end)`.
  - In `time_lines`, compute `final = final_fit(track)` next to `fit`, pass it to `measure`, and pass `track=tuple(track)` through to the `Line`.
  - Fix the module docstring's "confirms the line" sentence, and the `CONFIRM_WINDOW_Y` comment block.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_linetime.py -q`. Expected: PASS. Then `grep -rn "confirmed_by\|CONFIRM_\|\.confirmed\b" src/ tests/ scripts/`, expecting only unrelated hits (`house_confirmed`, etc.). Fix any that read `line.confirmed`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/linetime.py tests/test_linetime.py
git commit -m "linetime: final line over 9 ft past the hog line; line check removed; raw track kept"
```

---

### Task 2: `aimlines`: the slide and release lines, their errors, and the set

**Files:**
- Create: `src/curling_score/game/aimlines.py` (the path is free on main)
- Create: `tests/test_aimlines.py` (the path is free on main)

**Interfaces:**
- Consumes: `linetime.Line` (`delivery` as `((t, y, x), ...)` with `t` relative to the release; `release` as `(t, y, x)`; `track` as `((t, x, y, yp), ...)`; `curl`), `linetime.side_of`, `linetime.TEE_Y`, `linetime.NINE_FT_M`, and `shot.target_broom` (`.x_m`, `.y_m`, or None).
- Produces:

```python
SLIDE_FROM_M = -(C.TEE_TO_HACKLINE_M + C.TEE_TO_BACKLINE_M) / 2
SLIDE_MIN_N, SLIDE_MIN_SPAN_M = 8, 2.0
RELEASE_MIN_N, RELEASE_MIN_SPAN_M = 15, 2.0
SE_MAX_M = 0.15          # provisional; calibrated in Task 5
SET_SIGMAS = 2.0

@dataclass(frozen=True)
class AimLine:
    a: float; b: float            # x = a + b*y
    n: int; n_eff: float; rms: float
    se_m: float                   # standard error at the broom's y (the tee's, broomless)
    at_broom_x: float | None; miss: float | None; side: str | None
    at_tee_x: float | None        # only without a broom
    shown: bool                   # se_m <= SE_MAX_M

@dataclass(frozen=True)
class AimSet:
    m: float                      # + wide (or + right without a curl)
    se_m: float
    shown: bool

def slide_points(line) -> list[tuple[float, float]]          # (y, x)
def release_points(line) -> list[tuple[float, float]]        # (y, x)
def fit_aim(points, min_n, min_span, broom, curl) -> AimLine | None
def aim_set(slide: AimLine | None, release: AimLine | None, curl) -> AimSet | None
def attach_aim_lines(shots) -> int                           # in place; returns rocks given a release line
def aim_json(a: AimLine | None) -> dict | None
def set_json(s: AimSet | None) -> dict | None
```

`aim_json` returns `{"at_broom": {"x", "miss_m"} | None, "at_tee": {"x"}` (only when broomless)`, "side", "shown", "fit": {"n", "n_eff", "rms_m", "se_m"}}`. `set_json` returns `{"m", "se_m", "shown"}`. Round to 4 decimals, as `timeline._line` does.

- [ ] **Step 1: Write the failing tests** in `tests/test_aimlines.py`. Build a `Line` with `linetime.Line(...)` (or `dataclasses.replace` on a minimal one). Tests and their asserts:
  - `test_slide_window_runs_from_halfway_to_the_back_line_to_the_release`: delivery samples every 1/30 s with `d` from −3.2 to 7.9 m and release at `d = 5.5`. `slide_points` returns exactly the samples with `-2.7435 <= d <= 5.5`.
  - `test_release_window_is_nine_feet_past_the_release`: same delivery, release at `d = 4.0`. `release_points` covers `4.0 <= d <= 6.7432`, using only the delivery.
  - `test_release_window_continues_on_the_track_past_the_delivery`: release at `d = 6.5`, delivery ending at `d = 7.9`, track samples from `d = 6.4` to 10.0. Points with `d <= 7.9` come from the delivery only, those with `7.9 < d <= 9.2432` from the track only, and none are duplicated.
  - `test_a_straight_slide_lands_where_it_points`: points on `x = 0.1 - 0.01*(y - 20)` with broom `(−0.5, 0.0)`. `miss == approx(0.1 + 0.2 + 0.5)`, `side` comes from `side_of` with curl "right", and `shown` is True.
  - `test_correlated_residuals_shrink_the_effective_count`: 60 points whose residuals are a slow sine, so lag-1 autocorrelation is near 1. `n_eff < 10` and `se_m` is more than 2× the independent-sample formula's.
  - `test_too_few_or_too_short_is_no_line`: 7 slide points give None; 20 points over 1.5 m give None.
  - `test_too_uncertain_is_not_shown`: `se_m > SE_MAX_M` gives `shown is False`.
  - `test_set_is_signed_toward_wide_and_shown_only_past_two_sigma`, using the PR #2 example: slide 3″ narrow and release 4″ wide with curl "right" give `m ≈ +7″ = 0.1778 m`. The same lines with `se_m` large enough give `shown is False`. A line not shown gives `shown is False`. Without a curl, `m` is signed to the right.
  - `test_broomless_lines_are_pinned_at_the_tee`: `target_broom=None` gives `at_broom_x is None`, `miss is None`, and `at_tee_x == approx(a)`.
  - `test_no_release_point_gives_no_slide_or_release`: `line.release is None` means `attach_aim_lines` leaves `slide`, `release_line` and `set` as None and returns 0.
  - `test_attach_is_attach_only`: a shot without a `line` is skipped. A `fit_aim` that raises on one shot leaves that shot's line unchanged and does not raise. Monkeypatch the function.
  - `test_json_shapes`: `aim_json` and `set_json` return the documented keys, and `None` maps to `None`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_aimlines.py -q`. Expected: FAIL (`ModuleNotFoundError: curling_score.game.aimlines`).

- [ ] **Step 3: Implement `aimlines.py`.** Points are sorted in travel order (descending `y`). `fit_aim` uses `np.polyfit(ys, xs, 1)`. The error at `y*` (the broom's `y`, or 0.0 broomless) is:

```python
r = xs - (a + b * ys)                         # travel order
rms = sqrt(sum(r**2) / (n - 2))
rho = clip(corr(r[:-1], r[1:]), 0.0, 0.95)    # 0.0 when r is constant
n_eff = max(3.0, n * (1 - rho) / (1 + rho))
sxx = sum((ys - ys.mean())**2)
se = rms * sqrt(1 / n_eff + (y_star - ys.mean())**2 / (sxx * n_eff / n))
```

`side` comes from `linetime.side_of(miss, curl)`. `aim_set` follows PR #2's `slideReading` (`3212ead8:frontend/core/slide.mjs` lines 84–100), with `SIGMA_R_IN` replaced by the two lines' `se_m`. `attach_aim_lines` sets `shot.line = dataclasses.replace(line, slide=..., release_line=..., set=...)`, and catches and logs per shot like `releasetime.time_releases`. The module docstring cites the spec and says it is attach-only.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_aimlines.py tests/test_linetime.py -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/aimlines.py tests/test_aimlines.py
git commit -m "aimlines: slide and release lines at the broom, their errors, and the set"
```

---

### Task 3: Run it in games and practice; schema 10

**Files:**
- Modify: `src/curling_score/analyze.py` (after the `time_releases` block, ~lines 700–713)
- Modify: `src/curling_score/practice/enrich.py` (after `_run("release", ...)`, ~line 123)
- Modify: `src/curling_score/timeline.py` (`SCHEMA_VERSION` line 17, `_line` lines 41–66)
- Modify: `src/curling_score/practice/throws.py` (`"line"` block, lines ~97–125)
- Modify: `src/curling_score/version.py:329`
- Test: `tests/test_timeline.py`, `tests/test_practice_throws.py`, `tests/test_practice_enrich.py`

**Interfaces:**
- Consumes: `aimlines.attach_aim_lines`, `aimlines.aim_json`, `aimlines.set_json` (Task 2), and `Line.final_refused` (Task 1).
- Produces:
  - Timeline `line` gains `"slide"`, `"release_line"` and `"set"`, always present (null when not measured). It loses `"confirmed"`.
  - When the final is refused: `"fit": null`, `"at_hog": {"x": null, "offset_m": null}`, `"at_broom": {"x": null, "miss_m": null}` (only when a broom was held; still `null` broomless), and `"final_refused": true`. The key is absent otherwise.
  - Practice `line` gains the same three keys and `"final_refused"` the same way, and loses `"confirmed"`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_timeline.py::test_line_carries_slide_release_and_set`: a shot whose `Line` has `slide`/`release_line`/`set`. `doc["schema_version"] == 10`, the three keys match `aim_json`/`set_json`, and `"confirmed" not in line`.
  - `tests/test_timeline.py::test_refused_final_serialises_its_marker`: `final_refused=True` with a broom gives `line["final_refused"] is True`, `line["fit"] is None` and `line["at_broom"] == {"x": None, "miss_m": None}`. Broomless gives `line["at_broom"] is None`.
  - `tests/test_practice_throws.py::test_line_carries_slide_release_and_set`: the same three keys and no `"confirmed"`.
  - `tests/test_practice_enrich.py::test_aim_lines_follow_the_release`: with stub stages where `release` attaches `line.release`, the enriched shot's `line.release_line` is not None. Use the file's existing stub `Stages` pattern.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_timeline.py tests/test_practice_throws.py tests/test_practice_enrich.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement.**
  - In `analyze.py`, inside `if keeper is not None:` and after `time_releases`, call `aimlines.attach_aim_lines(shots)`, print `progress(f"    end {end.number}: aim lines {n}/{len(shots)}")`, then `stages.mark("aim")`.
  - In `enrich.py`, call `aimlines.attach_aim_lines(shots)` right after `_run("release", ...)` (it is pure, so it is not a `Stages` field).
  - Serialise with the shared helpers.
  - `SCHEMA_VERSION = 10`, `PIPELINE_VERSION = "2026.10.31.1"`.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_timeline.py tests/test_practice_throws.py tests/test_practice_enrich.py tests/test_practice_api.py tests/test_aimlines.py tests/test_linetime.py -q`. Expected: PASS. Then `grep -rn "schema_version.*9\|SCHEMA_VERSION" src tests` and update any test pinned to 9 that means "the current schema".

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/analyze.py src/curling_score/practice/enrich.py src/curling_score/timeline.py \
        src/curling_score/practice/throws.py src/curling_score/version.py tests/
git commit -m "timeline 10: slide, release and set on every line; line check dropped"
```

---

### Task 4: Rotation study, 9 ft past the hog line against today's rpm (one-off)

**Files (outside the repo):**
- Create: `~/curling-work/spin-9ft/study.py`, `~/curling-work/spin-9ft/report.md`

**Interfaces:**
- Consumes: `spintime.spin_start`, `spintime.follow`, `spintime.crop_box`, `spintime.stopping_at`, the pose `points` function `time_spins` uses, `spinfit.read`, `spinfit.read_with_swing`, `spintime.swing_for`, and the shipped weights (`weights/spin1.pt`, the line model).
- Produces: `report.md`, with a table per game and colour (rocks, read by today, read by 9 ft, range-only, refused, median and p90 |9ft/today − 1|) and a recommendation.

- [ ] **Step 1: Pick the games.** On the Ryzen worker (`curling@100.85.240.114`), list cached videos (`/data/wdd/curling-cache*/videos/`) and choose PHbZ3EKOhMI (it has eye reads) plus 2–3 this-season fours games that are present. **No downloads.** Check for live games first (`find /data/wdd/curling-cache*/live -name 'rec.*.ts' -mmin -2`) and run only when there are none.
- [ ] **Step 2: Write `study.py`.** For each shot in the chart's timeline that has a `spin`:
  - Copy `spintime._read_window`, keeping `metres` per sample.
  - Read from `t_hog` to 3 s past it at 30 fps.
  - Keep samples with `6.401 <= m <= 9.1442`, ignoring `FROM_M`.
  - Fit with `spinfit.read`, then `read_with_swing` using `swing_for(colour, firsts)` from the same end's clean reads.
  - Record turns in view, the result (`rpm` / range / refused), and today's `spin.rpm` from the timeline.
- [ ] **Step 3: Run it** on one end first. Eye-check two rocks against contact sheets, then run the rest.
- [ ] **Step 4: Write `report.md`.** Cover the counts, the agreement split by weight (split time) and colour, the turns-in-view distribution, and a recommendation. If the window is too short, say whether 30 fps or a longer window would change that; the spec allows a frame-rate increase. Copy `report.md` into the session scratchpad and hand it to the user. No repo change.

---

### Task 5: A/B on the worker; calibrate; the slide decision point

**Files:**
- Possibly modify: `src/curling_score/game/aimlines.py` (`SE_MAX_M`, the gates) and `linetime.py` (`FINAL_MIN_SPAN_M`, `EXTEND_FPS`), only as the numbers direct.
- Create (outside the repo): `~/curling-work/line-windows/ab/` (scripts, outputs, `report.md`)

- [ ] **Step 1: Choose the games.** Use 10/20 S2 `dmljQzQr1xo` (s_1TH0, with hand marks) and 10/06 Tuesday Super League `2rOCyiAiroU`, if both are still cached on the Ryzen. Otherwise use two other cached this-season fours games, at least one with off-centre brooms. No downloads.
- [ ] **Step 2: Run full `analyze()`, base = `main` and new = this branch, with `run_ab.sh`.** It and `run_analyze.py` come from `~/curling-work/missed-first-rock` or the laptop's `/data/wdd/scratch`. Run base then new in sequence, outside league hours, after checking for live games as in Task 4. Make the outputs readable with the `chmod` container command before copying them.
- [ ] **Step 3: Report** in `ab/report.md`:
  - Per rock, the final miss against today's miss: the median, p90, and the split by weight band.
  - Rocks refused by the final gate but kept by the internal fit (`final_refused`).
  - How many rocks got a slide and a release line, their `n`, `n_eff`, `rms` and `se_m` distributions, and how many are not shown at `SE_MAX_M = 0.15`.
  - The miss distributions for slide, release and final.
  - **Slide miss against the broom's lateral offset** (a scatter plot and a slope).
  - The set: how often it is shown, and its size.
  - The timeline diff outside `line`. It should be only known segmentation jitter (pair ends by `(vid, number, house, start ±60 s)`).
- [ ] **Step 4: Calibrate.** Set `SE_MAX_M` from the `se_m` distribution and write the reason in a code comment. If a gate refuses more than about 5% of rocks for want of points, raise the read's frame rate or widen the read where the spec allows it, measure the added seconds per end, and rerun the new side.
- [ ] **Step 5: Decision point.** If the slide miss tracks the broom offset (slope over about 0.1, or reads tens of inches off on off-centre brooms, as in PR #2), the viewer tasks still go ahead, but Slide and the set are not shown: in Tasks 6–7, `aimReadable` shows Release and Final only. Before any further slide work, run one long-camera-as-a-ruler check on 3–4 off-centre rocks and **bring the numbers to the user**.
- [ ] **Step 6: Commit** any constant changes with their tests (`tests/test_aimlines.py`, `tests/test_linetime.py`):

```bash
git commit -am "aimlines: SE ceiling and gates from the A/B on <games>"
```

---

### Task 6: Game shot detail: Slide, Release and Final

**Files:**
- Modify: `frontend/core/line.mjs` (`confirmTick` lines 317–326 removed; `lineNumbers` 332–356; `lineFigures` 370–415)
- Modify: `frontend/viewer/Detail.jsx` (the `Check` tick, and the caption's "figures ±4 in" if it changes)
- Modify: `src/curling_score/viewer/*.css` only if the 3×3 desktop grid needs it
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: timeline `line.slide`, `line.release_line`, `line.set`, `line.final_refused` (Task 3).
- Produces:
  - `AIM_SCHEMA = 10` and `aimReadable(doc)`, which is `Number(doc?.schema_version) >= AIM_SCHEMA`.
  - `lineNumbers` gains `slide_m`, `release_m`, `set_m`, `set_shown` and `final_refused`. These are null where they are not measured or not shown, and `miss_m` is null when the final is refused.
  - The `lineFigures` keys are `slide`, `release` and `broom` (labelled "Final" on schema ≥ 10, "At the broom" below 10), then `hack`, `hog`, `weight`, `rotation`, `curl` and `rest`.

Figure copy (schema ≥ 10):

| Figure | Value | Note |
| --- | --- | --- |
| Slide | `On the broom` / `${feetInches} wide` | `the slide, to the broom` |
| Release | same format | set shown: `${feetInches(set)} wider than the slide` / `narrower`; no curl: `left` / `right of the slide`. Otherwise `9 ft past the release` |
| Final | same format | `9 ft past the hog line` |
| any of the three, not measured | `–` | `no release read` · `too few sightings` · `too uncertain to show` · `no broom held in the house` · Final refused: `too few sightings past the hog line` |

- [ ] **Step 1: Write the failing tests** in `tests/test_viewer_js.py`, in a `TestAimFigures` class built on the file's existing chart fixtures with `schema_version: 10`:
  - `test_three_lines_at_the_broom`: the labels in order start `["Slide", "Release", "Final", "Hack", ...]`, and the values match the copy table for a fixture with a 0.08 m narrow slide and a 0.10 m wide release (curl right).
  - `test_set_shows_on_the_release_note_only_when_shown`.
  - `test_no_release_read_says_so`.
  - `test_refused_final_is_a_dash_not_no_broom`.
  - `test_broomless_rock_shows_dashes_for_three_lines`.
  - `test_schema_9_chart_keeps_its_six_figures`: the labels are exactly today's, and a stored `confirmed: true` changes nothing.
  - `test_slide_hidden_when_held_back`: only if Task 5's decision point held the slide back.

  Delete the tests of `confirmTick`, the ticks and the "confirmed from behind the thrower" notes.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_viewer_js.py -q -k "Aim or Figure or line"`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** in `line.mjs` per the table. Remove `confirmTick`, every `tick`/`dim` that came from it, and `Check`'s use in `Detail.jsx` (keep `Figures` rendering `tick` generically or drop the prop).
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_viewer_js.py tests/test_ice_js.py -q` (and `tests/test_myshots*` if present, since My Shots shares `lineFigures`). Expected: PASS. Then `cd frontend && npm run build`, expecting a clean eslint and build.
- [ ] **Step 5: Commit**

```bash
git add frontend/core/line.mjs frontend/viewer/Detail.jsx frontend/.buildstamp.json \
        src/curling_score/service/static src/curling_score/viewer tests/test_viewer_js.py
git commit -m "viewer: Slide, Release and Final at the broom; the line check's tick removed"
```

---

### Task 7: Practice card with PR #2's features, from the stored lines

**Files:**
- Modify: `frontend/core/practice.mjs` (add the aim figures; `lineText` short form in inch marks)
- Modify: `frontend/site/Practice.jsx`
- Modify: `src/curling_score/service/static/site.css`
- Test: `tests/test_practice_js.py`

Reference throughout: PR #2 at `3212ead8` (local branch `pr2-merge`). Take `SlideHero`, `WeightFigure`, `RestFigure` and `SensorFigures`, and the `Latest` layout, from its `frontend/site/Practice.jsx`. Take the `.slide-*` rules from its `site.css`. Do **not** port `frontend/core/slide.mjs`'s fitting (`slideFit`, `slideReading`).

**Interfaces:**
- Consumes: the practice `line.slide`, `line.release_line`, `line.set`, `line.miss_m`, `line.side`, `line.aim_x` and `line.final_refused` (Task 3), plus `t.split_s`, `t.rest`, `t.sensor`, `t.arrived` and `t.curl`.
- Produces in `practice.mjs`:
  - `aimFigures(t) -> {slide, release, final, set} | null`. It is null when `!("slide" in (t.line ?? {}))` (an old record). `slide`, `release` and `final` are each `{value, side}`, or null when not measured or not shown. `set` is `{value}` or null.
  - `figureWords(f) -> string` gives `'9" narrow'`, `"to the broom"`, or `'8" left of centre'` broomless.
  - `lineText(line, {short: true})` returns inch marks, `'14" wide'`, matching `figureWords(aimFigures(t).final)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_practice_js.py`:
  - `test_aim_figures_in_inch_marks`: a slide 0.0762 m narrow, a release 0.1016 m wide and a final 0.127 m wide (curl right) give `{slide: {value: '3"', side: "narrow"}, release: {value: '4"', side: "wide"}, final: {value: '5"', side: "wide"}}`. A set of 0.1778 m shown gives `{value: '+7"'}`.
  - `test_within_an_inch_is_to_the_broom`.
  - `test_set_without_curl_says_which_way`: `'7" right'`.
  - `test_not_shown_or_missing_is_null`: covers `shown: false`, a null slide, and `final_refused`.
  - `test_broomless_reads_against_centre`: `at_tee.x = -0.2032` gives `figureWords(...) == '8" left of centre'`.
  - `test_old_record_has_no_aim_figures`: a record with no `slide` key gives `aimFigures(t) === null`.
  - `test_table_line_matches_the_card`: `lineText(line, {short: true}) == figureWords(aimFigures(t).final)`.

  Update the existing `test_the_line_against_the_broom_or_where_it_was_aimed` expectations to inch marks where it uses `short`.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_practice_js.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `practice.mjs`, then the card. The figure order in the hero is Slide, Release (with the set under it), Final, Hog to hog (only `t.arrived`), Came to rest, Sensor short and Sensor long (only on a sensor session). Make it two columns on a phone. An old record (`aimFigures` null) keeps today's `<dl>` rows. The hogged-rock branch shows the three lines and the sensors, as PR #2 did. A figure that is null reads "not measured", or "no release read" when `t.line.release` is absent.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_practice_js.py tests/test_practice_api.py -q`, then `cd frontend && npm run build`. Expected: PASS, and a clean build.
- [ ] **Step 5: Commit**

```bash
git add frontend/core/practice.mjs frontend/site/Practice.jsx frontend/.buildstamp.json \
        src/curling_score/service/static tests/test_practice_js.py
git commit -m "practice: the card's figures from the stored lines, with PR #2's layout (leilani-oti)"
```

---

### Task 8: Look at it, then open the PR

- [ ] **Step 1: Game page.**
  - Take one new-schema timeline from Task 5's A/B and one schema-9 timeline.
  - Serve each with `PYTHONPATH=src .venv/bin/python scripts/devserve.py <timeline.json>`.
  - Check the shot detail at 390×844 (mobile + touch emulation, through `scripts/cdp.mjs`) and at 1500 px wide, in both themes.
  - Screenshot each into the session scratchpad. Look for: nine figures in a 3×3 grid on desktop, no empty slots on the old chart, no overflow.
- [ ] **Step 2: Practice card.**
  - Run one cached practice session through `enrich` on the branch. If there is none, stage one the way the practice-page screenshots were (`~/curling-work/practice-shots`).
  - Screenshot the card at phone width, for a broom throw, a broomless throw, a hogged throw, and an old record.
- [ ] **Step 3: Fix** what the screenshots show (one pass), rebuild, and commit.
- [ ] **Step 4: Push and open the PR**, but only when the user says to. Use `git push -u origin line-windows` and `gh pr create`. The body summarises the three lines, the removed check, schema 10 with no reprocess, and the A/B numbers. It credits leilani-oti's PR #2 for the card, links #2 as superseded, and ends with the attribution line given for PR descriptions.
