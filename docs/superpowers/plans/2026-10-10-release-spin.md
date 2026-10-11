# Release Spin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every thrown rock its rotation as thrown -- **Rotation: 11 rpm, in-turn** -- read from the long camera facing the thrower, on recorded and live games.

**Architecture:** A new attach-only pass, `game/spintime.py`, runs after the line pass in `analyze.build_one_end` (live games go through it too). It reads hogtime's camera at 10 fps from 0.5 s after the hog crossing, follows the rock from hogtime's own track, crops it exactly as the training crops were cut, finds the handle's neck and grip end with a small YOLO-pose model (`weights/spin1.pt`), and fits the swing of the grip across the neck (`game/spinfit.py`) over the first ~1.25 turns from 8 m past the throwing tee. The timeline (schema 9) carries `spin.rpm`; the viewer shows it beside the line figures and names the turn from the curl and the hack it already has.

**Tech Stack:** Python 3.12, numpy, ultralytics YOLO (pose), ffmpeg via `detect/longview.decode`; React viewer built with esbuild (`frontend/`), tested in node through `tests/test_viewer_js.py`.

**Spec:** `docs/superpowers/specs/2026-10-10-release-spin-design.md`

## Global Constraints

- Work in the worktree `.claude/worktrees/release-spin`, branch `release-spin` (already made from main f9c9a07, with the `spin` branch merged in as a3110f9). Never write under `/home/tcuser/src/curling_score` outside this worktree; another session shares that checkout.
- Python: `/home/tcuser/src/curling_score/.venv/bin/python` (the worktree has no venv of its own). It has no scipy: the fit is pure numpy.
- **rpm = 60 x turns per second.** The viewer shows it rounded to a whole number: `11 rpm`.
- **Figure:** key `rotation`, label `Rotation`, value `"<n> rpm"`, note `in-turn` / `out-turn` / `turn not known`; `–` with note `not read` when the chart has spin but this rock has none.
- **In-turn when the rock curls away from its hack's side** (curls right from the left hack, left from the right hack); **out-turn when it curls toward it**. A stone curls the way it turns, and a right-hander throws from the left hack and turns an in-turn clockwise.
- **The reported value is the early rate:** a constant-rate fit over the first `EARLY_TURNS = 1.25` turns from `FROM_M = 8.0` m past the throwing tee, the window clipped to `EARLY_WINDOW_S = (5.0, 9.0)`.
- **Sampling:** `SPIN_FPS = 10.0`, window `WINDOW_S = (0.5, 10.5)` seconds after the hog crossing.
- **Gates:** at least `MIN_SAMPLES = 30` samples; whole-stretch fit amplitude `>= 0.10` stone widths, amplitude / robust noise (1.4826 x the residuals' median absolute value) `>= 3.0`, `>= 0.5` turns in view; keypoint confidence `>= 0.5`.
- **The pose model:** `weights/spin1.pt` (md5 `40588fd2043df15d329c57e42cba1039`, the spike's p5: yolo11n-pose, 224 px, two keypoints neck then grip end), env `CURLING_SCORE_SPIN_WEIGHTS`, absent = no spin, not an error; `none` switches it off.
- **The crop is the training crop:** detector box `(x0, y0, x1, y1)`, `w = x1 - x0`, `h = y1 - y0`; crop `[x0 - 0.35w, x1 + 0.35w] x [y0 - 0.5h, y1 + 0.25h]`, clipped to the frame, pixels from a frame decoded with `longview.decode` over the view's rect (RGB). Predict on BGR (`[:, :, ::-1]`), as training read PNGs.
- **Timeline schema 9:** each shot has `"spin": {"rpm", "rpm_avg", "turns_in_view", "samples"}` or `null`. Charts below 9 show **no** Rotation figure (not a dash): they are not being reprocessed.
- **No backfill:** do not queue reprocesses of hosted games. Live games are included.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Run tests with `-q` and read the exit status: `pytest ... | tail -1 && git commit` hides a failure.

## Review Focus

- **A rock hogtime never followed** -- hogged, refused with no samples, a missing placeholder: no `spin`, no exception, every other rock still read. (Task 4)
- **A fast hit that leaves the frame after a fraction of a turn:** `null`, never a confident slow rpm fitted to a quarter wave. (Task 2)
- **A handle the model cannot see** (low keypoint confidence, a flat or noisy swing, a stone stopped in view): `null`, not a small rpm. (Task 2)
- **A second stone of the rock's colour in view** (a neighbouring sheet on sheet 2's wide framing, a stone parked at the side): the follower stays on the rock. (Task 3)
- **Old charts and rocks without a line:** a schema-8 chart shows no Rotation figure at all; a schema-9 rock with spin but no line or no hack still shows its rpm, with "turn not known". (Task 6)

---

### Task 1: Ship the pose model and its loader

**Files:**
- Create: `weights/spin1.pt` (copy of `~/curling-work/spin/runs/p5/weights/best.pt`)
- Modify: `.gitignore` (an exception for it, beside the broom ones)
- Modify: `src/curling_score/weights.py` (after `BROOM_ENV_VAR` and after `broom_path`)
- Modify: `Dockerfile.worker` (beside `BROOM_MODEL`)
- Create: `src/curling_score/detect/spinmodel.py`
- Test: `tests/test_weights.py`, `tests/test_spinmodel.py`

**Interfaces:**
- Produces: `weights.SPIN_NAME = "spin1.pt"`, `weights.SPIN_ENV_VAR = "CURLING_SCORE_SPIN_WEIGHTS"`, `weights.spin_path() -> Path | None`.
- Produces: `spinmodel.default_model()` -> a loaded YOLO model or `None`; `spinmodel.handle_points(model, patches, imgsz=224) -> list[tuple[tuple[float, float], tuple[float, float], float] | None]` -- per patch, `((neck_x, neck_y), (grip_x, grip_y), conf)` in the patch's own pixels with `conf` the lower of the two keypoint confidences, or `None` where nothing was found.

- [ ] **Step 1: Copy the weights in and check them**

```bash
cp ~/curling-work/spin/runs/p5/weights/best.pt weights/spin1.pt
md5sum weights/spin1.pt     # 40588fd2043df15d329c57e42cba1039
```

Add to `.gitignore`, after the `!weights/broom4.pt` exception (find it with `grep -n broom4 .gitignore`):

```
# spin1: the handle-point model (neck, grip end) the spin pass reads a rock's
# rotation with -- datasets/spin, the spike's p5. See game/spintime.py.
!weights/spin1.pt
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_weights.py`, inside the Dockerfile test class (the one with `test_it_ships_and_pins_the_broom_model`):

```python
    def test_it_ships_and_pins_the_spin_model(self):
        """`spin_path` treats a missing file as "no spin", so an image built
        without it would publish every rock with a null spin. Pinned, a missing
        file is a failure."""
        from curling_score import weights
        df = self._dockerfile()
        assert f"ARG SPIN_MODEL={weights.SPIN_NAME}" in df
        assert "COPY weights/${SPIN_MODEL}" in df
        assert "CURLING_SCORE_SPIN_WEIGHTS=/opt/curling/weights/${SPIN_MODEL}" in df
```

and a new class at the end of the file:

```python
class TestSpinPath:
    """No spin model means no spin, never a broken run."""

    def test_absent_is_no_spin_not_an_error(self, monkeypatch):
        monkeypatch.delenv(weights.SPIN_ENV_VAR, raising=False)
        monkeypatch.setattr(weights, "_candidates", lambda name=None: iter(()))
        assert weights.spin_path() is None

    def test_none_switches_it_off(self, monkeypatch):
        monkeypatch.setenv(weights.SPIN_ENV_VAR, "none")
        assert weights.spin_path() is None

    def test_a_set_but_missing_path_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.SPIN_ENV_VAR, str(tmp_path / "nope.pt"))
        with pytest.raises(FileNotFoundError):
            weights.spin_path()

    def test_a_set_path_is_used(self, monkeypatch, tmp_path):
        p = tmp_path / "s.pt"; p.write_bytes(b"x")
        monkeypatch.setenv(weights.SPIN_ENV_VAR, str(p))
        assert weights.spin_path() == p

    def test_the_repository_ships_the_default(self):
        assert (Path(__file__).resolve().parents[1] / "weights" / weights.SPIN_NAME).is_file()
```

(Add `from pathlib import Path` to the file's imports if it is not there.)

Create `tests/test_spinmodel.py`:

```python
"""The handle-point model's wrapper: what it hands back per crop."""
import numpy as np

from curling_score.detect import spinmodel


class _Arr:
    def __init__(self, a):
        self.a = np.asarray(a, float)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Res:
    def __init__(self, found):
        if found is None:
            self.boxes, self.keypoints = [], None
            return
        boxes_conf, xy, kconf = found
        self.boxes = type("B", (), {"conf": _Arr(boxes_conf), "__len__": lambda s: len(boxes_conf)})()
        self.keypoints = type("K", (), {"xy": _Arr(xy), "conf": _Arr(kconf)})()


class FakePose:
    def __init__(self, results):
        self.results, self.seen = results, []

    def predict(self, images, imgsz, conf, verbose):
        self.seen += [im.copy() for im in images]
        out, self.results = self.results[:len(images)], self.results[len(images):]
        return [_Res(r) for r in out]


def test_points_come_back_per_crop_with_the_weaker_confidence():
    pose = FakePose([([0.9], [[[10.0, 20.0], [30.0, 15.0]]], [[0.95, 0.6]]), None])
    got = spinmodel.handle_points(pose, [np.zeros((40, 60, 3), np.uint8)] * 2)
    assert got[0] == ((10.0, 20.0), (30.0, 15.0), 0.6)
    assert got[1] is None


def test_the_most_confident_stone_is_the_one_read():
    pose = FakePose([([0.4, 0.9], [[[1, 1], [2, 2]], [[10, 20], [30, 15]]], [[0.9, 0.9], [0.8, 0.7]])])
    assert spinmodel.handle_points(pose, [np.zeros((40, 60, 3), np.uint8)])[0][0] == (10.0, 20.0)


def test_crops_go_to_the_model_as_bgr_like_the_training_pngs():
    pose = FakePose([None])
    rgb = np.zeros((4, 4, 3), np.uint8); rgb[..., 0] = 200      # red in RGB
    spinmodel.handle_points(pose, [rgb])
    assert pose.seen[0][0, 0].tolist() == [0, 0, 200]
```

- [ ] **Step 3: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_weights.py tests/test_spinmodel.py -q`
Expected: FAIL (`SPIN_NAME` / `spinmodel` not defined, Dockerfile lines missing).

- [ ] **Step 4: Implement**

In `src/curling_score/weights.py`, after `BROOM_ENV_VAR = ...`:

```python
# The handle-point model the spin pass reads a rock's rotation with: two
# keypoints, the handle's neck (its pivot) and its grip end, on a crop of the
# stone from the long camera facing the thrower. Like SIDE_NAME it is optional:
# a missing file means no spin, not a broken run. See `game/spintime.py`.
#
# spin1 as of 2026-10-10: yolo11n-pose at 224 px on 258 hand-labelled frames of
# five 2026-27 games, one per sheet (datasets/spin, the spike's p5). On rocks it
# never saw, its early rates sit within ~10% of frame-by-frame eye readings, and
# it reads 122 of 128 rocks of four games' unlabelled ends.
SPIN_NAME = "spin1.pt"
SPIN_ENV_VAR = "CURLING_SCORE_SPIN_WEIGHTS"
```

and after `broom_path()`:

```python
def spin_path():
    """The handle-point model, or None: no model means no spin, not an error.

    Resolved like :func:`broom_path`: ``none`` or unset-and-absent gives None,
    and a path that is set but missing raises.
    """
    chosen = os.environ.get(SPIN_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(
                f"{SPIN_ENV_VAR}={chosen} does not exist. Set it to a weights "
                f"file, or to 'none' for no spin.")
        return path
    for path in _candidates(SPIN_NAME):
        if path.is_file():
            return path
    return None
```

In `Dockerfile.worker`, after the `BROOM_MODEL` ARG/COPY lines (read lines 55-90 first and match their comment style):

```dockerfile
# The handle-point model the spin pass reads rotation with. Pinned for the
# same reason as the broom model: `weights.spin_path()` treats a missing file
# as "no spin", so an unpinned image would quietly publish none.
ARG SPIN_MODEL=spin1.pt
COPY weights/${SPIN_MODEL} /opt/curling/weights/${SPIN_MODEL}
```

and add `CURLING_SCORE_SPIN_WEIGHTS=/opt/curling/weights/${SPIN_MODEL} \` to the `ENV` block with the other three weights variables.

Create `src/curling_score/detect/spinmodel.py`:

```python
"""The handle-point model: where a stone's handle neck and grip end are.

A YOLO-pose model with one class (the stone) and two keypoints -- the neck,
where the handle rises out of the stone and which stays put as it turns, and
the grip end, which swings round. `game/spintime.py` reads a rock's rotation
from how the grip end swings across the neck. Loaded lazily, like
`sidemodel.default_finder`, so importing this needs no GPU or weights.
"""
from __future__ import annotations

import functools

import numpy as np

IMGSZ = 224
CONF = 0.25
BATCH = 64


@functools.lru_cache(maxsize=2)
def _load(path: str):
    from ultralytics import YOLO

    return YOLO(path)


def default_model():
    """The handle-point model, or None when none is configured."""
    from curling_score import weights as weights_mod
    path = weights_mod.spin_path()
    return None if path is None else _load(str(path))


def handle_points(model, patches, imgsz: int = IMGSZ):
    """Per patch (RGB, as `longview.decode` gives them): ``((neck_x, neck_y),
    (grip_x, grip_y), conf)`` in the patch's pixels, ``conf`` the weaker of the
    two points' confidences; None where no stone was found. The most confident
    stone is the one read. Patches go in as BGR, as training read its PNGs."""
    out = []
    for i in range(0, len(patches), BATCH):
        batch = [np.ascontiguousarray(p[:, :, ::-1]) for p in patches[i:i + BATCH]]
        for r in model.predict(batch, imgsz=imgsz, conf=CONF, verbose=False):
            if r.keypoints is None or len(r.boxes) == 0:
                out.append(None)
                continue
            k = int(np.argmax(r.boxes.conf.cpu().numpy()))
            xy = r.keypoints.xy.cpu().numpy()[k]
            kc = r.keypoints.conf.cpu().numpy()[k] if r.keypoints.conf is not None else np.ones(2)
            out.append(((float(xy[0][0]), float(xy[0][1])), (float(xy[1][0]), float(xy[1][1])),
                        float(min(kc))))
    return out
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_weights.py tests/test_spinmodel.py -q; echo "exit $?"`
Expected: all pass, `exit 0`.

- [ ] **Step 6: Commit**

```bash
git add -f weights/spin1.pt
git add .gitignore src/curling_score/weights.py Dockerfile.worker src/curling_score/detect/spinmodel.py tests/test_weights.py tests/test_spinmodel.py
git commit -m "spin: ship the handle-point model (spin1) and its loader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The rate fit

**Files:**
- Create: `src/curling_score/game/spinfit.py`
- Test: `tests/test_spinfit.py`

**Interfaces:**
- Produces: `spinfit.Spin` (frozen dataclass: `rpm: float, rpm_avg: float, turns_in_view: float, samples: int`) and `spinfit.read(t, s) -> Spin | None` with `t` seconds (any origin) and `s` the grip's offset across from the neck in stone widths, both 1-D numpy arrays, one entry per frame read.
- Constants other tasks use: `MIN_SAMPLES = 30`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spinfit.py`:

```python
"""Reading a rock's rotation from the swing of its handle."""
import numpy as np
import pytest

from curling_score.game import spinfit


def swing(f=0.2, seconds=10.0, fps=10.0, a=0.2, noise=0.03, phase=0.7, decay=0.0, seed=1):
    """The grip's offset across from the neck: once round per turn, f turns a
    second at the start, slowing by ``decay`` turns/s each second."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, seconds, 1 / fps)
    turns = f * t + 0.5 * decay * t ** 2
    return t, a * np.sin(2 * np.pi * turns + phase) + rng.normal(0, noise, t.size)


def test_a_steady_rock_reads_its_rate_in_rpm():
    got = spinfit.read(*swing(f=0.2))
    assert got.rpm == pytest.approx(12.0, abs=0.4)
    assert got.rpm_avg == pytest.approx(12.0, abs=0.4)
    assert got.turns_in_view == pytest.approx(2.0, abs=0.15)
    assert got.samples == 100


def test_the_rate_reported_is_the_early_one():
    # 0.26 turns/s at the start, slowing to ~0.18 by the end of ten seconds.
    got = spinfit.read(*swing(f=0.26, decay=-0.008))
    assert got.rpm > got.rpm_avg
    assert got.rpm == pytest.approx(60 * 0.245, rel=0.06)


def test_a_slow_rock_needs_only_half_a_turn_in_view():
    got = spinfit.read(*swing(f=0.07, seconds=9.0, noise=0.02))
    assert got.rpm == pytest.approx(4.2, rel=0.15)


def test_a_fast_rock():
    assert spinfit.read(*swing(f=0.47)).rpm == pytest.approx(28.2, rel=0.03)


def test_a_quarter_wave_is_not_a_reading():
    """A fast hit gone from the frame after a fraction of a turn."""
    assert spinfit.read(*swing(f=0.05, seconds=4.0)) is None


def test_a_flat_or_unreadable_swing_is_not_a_slow_rock():
    t, _ = swing()
    rng = np.random.default_rng(2)
    assert spinfit.read(t, rng.normal(0, 0.03, t.size)) is None          # nothing there
    assert spinfit.read(*swing(a=0.08, noise=0.06)) is None              # too faint


def test_too_few_frames_is_not_a_reading():
    t, s = swing()
    assert spinfit.read(t[:spinfit.MIN_SAMPLES - 1], s[:spinfit.MIN_SAMPLES - 1]) is None


def test_a_few_wild_frames_do_not_move_it():
    t, s = swing(f=0.2)
    s = s.copy(); s[::17] = 0.6                                             # a box on a broom
    assert spinfit.read(t, s).rpm == pytest.approx(12.0, rel=0.05)


def test_gaps_in_the_frames_are_fine():
    t, s = swing(f=0.2)
    keep = np.ones(t.size, bool); keep[30:45] = False                       # a sweeper in the way
    assert spinfit.read(t[keep], s[keep]).rpm == pytest.approx(12.0, rel=0.04)
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_spinfit.py -q`
Expected: FAIL (`spinfit` not found).

- [ ] **Step 3: Implement**

Create `src/curling_score/game/spinfit.py` (ported from `scripts/spin/rate.py`, which the spike validated):

```python
"""A rock's rotation from the swing of its handle.

Seen from the long camera facing the thrower, the grip end of the handle sits
r*sin(theta) across from the neck, theta the handle's angle from the line of
sight -- so ``s`` (the grip's offset across from the neck, in stone widths)
swings once per turn. The rate is its frequency: a periodogram for where to
start, then a sinusoid fitted with a grid over frequency (and, for the whole
stretch, a slow drift) and least squares, reweighted so a stray frame cannot
pull it.

Two rates: ``rpm`` is the spin as thrown -- a constant rate over the first
~1.25 turns -- and ``rpm_avg`` the whole stretch in view, which a rock that
slows reads up to ~15% lower. A slow-down fitted through the whole stretch and
extrapolated back to the hog line was tried in the spike and over-read badly.

Direction is not here: the swing is the same either way round.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FREQS = np.linspace(0.05, 1.0, 381)      # turns/s: 3 to 60 rpm
EARLY_TURNS = 1.25
EARLY_WINDOW_S = (5.0, 9.0)
MIN_SAMPLES = 30                         # 3 s at 10 fps
MIN_AMPLITUDE = 0.10                     # stone widths
MIN_SNR = 3.0
MIN_TURNS_IN_VIEW = 0.5
DRIFTS = tuple(np.linspace(-0.02, 0.01, 13))   # turns/s^2, the whole stretch only


@dataclass(frozen=True)
class Spin:
    rpm: float              # the spin as thrown: the early window
    rpm_avg: float          # the whole stretch in view
    turns_in_view: float
    samples: int


def periodogram(t, x, freqs=FREQS):
    """Lomb-Scargle power at each frequency, normalised by the variance."""
    x = x - x.mean()
    var = max(float(x.var()), 1e-12)
    out = np.empty(len(freqs))
    for i, f in enumerate(freqs):
        w = 2 * np.pi * f
        tau = np.arctan2(np.sum(np.sin(2 * w * t)), np.sum(np.cos(2 * w * t))) / (2 * w)
        c, s = np.cos(w * (t - tau)), np.sin(w * (t - tau))
        out[i] = 0.5 * ((x @ c) ** 2 / (c @ c) + (x @ s) ** 2 / (s @ s)) / var
    return out


def fit(t, x, f0, drifts=DRIFTS):
    """x = a sin(2pi(f tm + k tm^2 / 2) + ph) + c, f within 30% of f0. For a
    given (f, k) it is linear in (a cos ph, a sin ph, c): the grid searches f
    and k, least squares (Huber-reweighted) the rest. Returns (f, k, a, noise),
    noise the residuals' robust spread (1.4826 x their median absolute value),
    so a few stray frames -- a box on a broom -- do not read as noise."""
    tm = t - t.mean()
    best = None
    for f in np.linspace(0.7 * f0, 1.3 * f0, 121):
        for k in drifts:
            ph = 2 * np.pi * (f * tm + 0.5 * k * tm ** 2)
            A = np.stack([np.sin(ph), np.cos(ph), np.ones_like(tm)], axis=1)
            wts = np.ones_like(x)
            for _ in range(4):
                coef, *_ = np.linalg.lstsq(A * wts[:, None], x * wts, rcond=None)
                r = x - A @ coef
                scale = 1.4826 * np.median(np.abs(r)) + 1e-6
                wts = np.sqrt(np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(r), 1e-9)))
            cost = float(np.sum((wts * r) ** 2))
            if best is None or cost < best[0]:
                best = (cost, f, k, coef, r)
    _, f, k, (bs, bc, _c), r = best
    return float(f), float(k), float(np.hypot(bs, bc)), float(1.4826 * np.median(np.abs(r)))


def _peak(t, x, freqs=FREQS) -> float:
    return float(freqs[int(np.argmax(periodogram(t, x, freqs)))])


def read(t, s) -> Spin | None:
    """The rock's rotation from its swing, or None where it cannot be read."""
    t, s = np.asarray(t, float), np.asarray(s, float)
    if t.size < MIN_SAMPLES:
        return None
    order = np.argsort(t)
    t, s = t[order], s[order]
    f_all, _k, a, noise = fit(t, s, _peak(t, s - np.polyval(np.polyfit(t, s, 1), t)))
    span = float(t[-1] - t[0])
    turns = f_all * span
    if a < MIN_AMPLITUDE or a / max(noise, 1e-9) < MIN_SNR or turns < MIN_TURNS_IN_VIEW:
        return None
    w = float(np.clip(EARLY_TURNS / max(f_all, 1e-3), *EARLY_WINDOW_S))
    m = t <= t[0] + w
    if m.sum() < MIN_SAMPLES:
        return None
    # Anchored on the whole stretch's rate, not on the window's own peak: a
    # sweeper hiding the rock for a second can pull that peak off, and a search
    # around it may then not reach the true rate. The early rate sits within
    # -2% to +12% of the whole stretch's on most rocks (spike, 84 rocks).
    f_early, *_ = fit(t[m], s[m], f_all, drifts=(0.0,))
    return Spin(rpm=60.0 * f_early, rpm_avg=60.0 * f_all, turns_in_view=turns, samples=int(t.size))
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_spinfit.py -q; echo "exit $?"`
Expected: all pass. This code was dry-run before the plan was handed over: these tests passed, and on PHbZ's 92 real rocks at 10 fps it read the spike's 30 fps early rates to a median 1.2% (90%: 3.7%); only the three slowest rocks (under one turn in view) differ by more than 10%, by about 1 rpm. If `test_the_rate_reported_is_the_early_one` misses by a little, check the synthetic arithmetic (the early window spans ~5 s, where the mean rate is `0.26 - 0.008 * 2.5 = 0.24`) before touching the code.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/spinfit.py tests/test_spinfit.py
git commit -m "spin: read a rock's rotation from the swing of its handle

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Find, follow and crop the rock

**Files:**
- Modify: `src/curling_score/detect/sidemodel.py` (a `detect_boxes` beside `detect_bands`)
- Create: `src/curling_score/game/spintime.py` (first half: constants, `seed_of`, `follow`, `crop_box`)
- Test: `tests/test_sidemodel.py`, `tests/test_spintime.py`

**Interfaces:**
- Consumes: `linetime.relink(samples, key, t_seed)`, `sidepool.band_crop(view)`, `linetime.CROP_EDGE_ROWS`, `SideView.lateral_px_per_m(row)`, `SideView.metres_at(row)`.
- Produces: `sidemodel.detect_boxes(model, frames, times, lo, hi, color, *, imgsz=800, conf=CONF_MIN) -> list[list[tuple[float, float, float, float, float]]]` -- per frame, `(x0, y0, x1, y1, conf)` in VIEW pixels for boxes of `color`.
- Produces: `spintime.seed_of(crossing, view, t_start) -> tuple[float, float, float, float, float] | None` -- `(t, cx, bottom_row, vx, vy)`, the rock's box bottom-centre on hogtime's track at the sample nearest `t_start`, with its velocity in view px/s.
- Produces: `spintime.follow(times, boxes, seed, view, frame_h) -> list[tuple[int, float, tuple]]` -- `(frame index, t, (x0, y0, x1, y1, conf))` per frame the rock was found.
- Produces: `spintime.crop_box(box, shape) -> tuple[int, int, int, int]` -- `(x0, y0, x1, y1)` integer slice bounds of the training crop within an `(H, W, ...)` frame.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sidemodel.py`:

```python
class TestDetectBoxes:
    def test_whole_boxes_of_the_colour_come_back_in_view_rows(self):
        lo = 500
        per = [[[10, 20, 60, 50, 0, 0.9], [100, 30, 150, 60, 1, 0.8]]]     # crop rows; red is class 0
        got = sidemodel.detect_boxes(FakeModel(per), [np.zeros((1080, 810, 3), np.uint8)], [1.0],
                                     lo, 1080, "red")
        assert got == [[(10.0, 520.0, 60.0, 550.0, 0.9)]]
```

Create `tests/test_spintime.py`:

```python
"""Following a rock down the camera facing its thrower, for its spin."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.game import spintime as ST
from curling_score.geometry.sideview import SideView

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                centre_col=390.0, lat_px_per_m_at_tee=150.0)


def box_at(y_m, x_px=380.0, conf=0.9):
    """The rock's box where its bottom sits ``y_m`` past the throwing tee."""
    row = VIEW.row_for(y_m)
    w = ST.STONE_M * VIEW.lateral_px_per_m(row)
    return (x_px - w / 2, row - 0.7 * w, x_px + w / 2, row, conf)


def crossing(t0=100.0, n=60, fps=30.0, speed=2.2, y0=5.0):
    """hogtime's samples: (t, cx, row, w) on the rock's track, 30 fps, from y0 metres."""
    samples = []
    for i in range(n):
        x0, y0b, x1, y1, _c = box_at(y0 + speed * i / fps)
        samples.append((t0 + i / fps, (x0 + x1) / 2, y1, x1 - x0))
    return SimpleNamespace(t=t0 + 0.6, track_key=int(samples[0][1] // 120), samples=tuple(samples))


class TestSeed:
    def test_it_takes_the_track_where_the_window_starts_with_its_velocity(self):
        c = crossing()
        t, cx, row, vx, vy = ST.seed_of(c, VIEW, 100.8)
        assert t == pytest.approx(100.8, abs=0.02)
        assert vy > 0 and abs(vx) < 1.0           # down the frame, straight

    def test_no_track_no_seed(self):
        assert ST.seed_of(None, VIEW, 100.0) is None
        assert ST.seed_of(SimpleNamespace(t=None, track_key=None, samples=()), VIEW, 100.0) is None


class TestFollow:
    def frames(self, n=100, fps=10.0, t0=100.6, speed=2.2, y_start=None, extra=()):
        times = [t0 + i / fps for i in range(n)]
        y_start = 5.0 + speed * (t0 - 100.0) if y_start is None else y_start
        boxes = [[box_at(y_start + speed * i / fps)] + [e(i) for e in extra] for i in range(n)]
        return times, boxes

    def test_it_follows_the_rock_to_the_bottom_of_the_frame_and_stops(self):
        times, boxes = self.frames()
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        rows = [b[3] for _i, _t, b in got]
        assert len(got) > 20 and rows == sorted(rows)
        assert rows[-1] < 1080 - ST.BOTTOM_PAD_PX

    def test_a_parked_stone_of_its_colour_beside_it_is_not_the_rock(self):
        parked = lambda i: box_at(14.0, x_px=650.0)          # 1.8 m across, still
        times, boxes = self.frames(extra=(parked,))
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert all(abs((b[0] + b[2]) / 2 - 380.0) < 20 for _i, _t, b in got)

    def test_a_rock_hidden_for_a_moment_is_found_again(self):
        times, boxes = self.frames()
        for i in range(20, 25):
            boxes[i] = []                                   # a sweeper in front of it
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert any(i > 25 for i, _t, _b in got)

    def test_a_rock_lost_for_long_is_let_go(self):
        times, boxes = self.frames()
        for i in range(20, 40):
            boxes[i] = []
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert max(i for i, _t, _b in got) < 20

    def test_a_box_far_off_the_stones_size_is_refused(self):
        times, boxes = self.frames()
        x0, y0, x1, y1, c = boxes[10][0]
        boxes[10] = [(x0 - 60, y0 - 60, x1 + 60, y1, c)]    # a box round a sweeper's legs
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert 10 not in [i for i, _t, _b in got]


class TestCrop:
    def test_it_is_the_training_crop(self):
        assert ST.crop_box((100.0, 200.0, 160.0, 240.0, 0.9), (1080, 810, 3)) == (79, 180, 182, 251)

    def test_it_stays_inside_the_frame(self):
        x0, y0, x1, y1 = ST.crop_box((5.0, 1050.0, 65.0, 1079.0, 0.9), (1080, 810, 3))
        assert (x0, y1) == (0, 1080)
```

(The crop: `w = 60, h = 40`; x `100 - 21 = 79` to `160 + 21 = 181` -> `int(181) + 1 = 182`; y `200 - 20 = 180` to `240 + 10 = 250` -> `251`. The `+ 1` is `extract.py`'s, which cut the training crops.)

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sidemodel.py::TestDetectBoxes tests/test_spintime.py -q`
Expected: FAIL (`detect_boxes`, `spintime` missing).

- [ ] **Step 3: Implement `sidemodel.detect_boxes`**

In `src/curling_score/detect/sidemodel.py`, after `detect_bands`:

```python
def detect_boxes(model, frames, times, lo, hi, color, *, imgsz=800, conf=CONF_MIN):
    """Every box of ``color`` in rows ``lo:hi`` of each frame, whole: per frame,
    a list of (x0, y0, x1, y1, conf) in view pixels. The spin pass crops the
    stone from these, so it needs the box's top as well as its bottom."""
    want = _CLASS_FOR[color]
    lo = max(0, int(lo))
    crops = [np.ascontiguousarray(np.asarray(f)[lo:min(np.asarray(f).shape[0], int(hi)), :, ::-1])
             for f in frames]
    out = []
    for i in range(0, len(crops), BATCH):
        for res in model.predict(crops[i:i + BATCH], imgsz=imgsz, conf=conf, verbose=False):
            got = []
            if res.boxes is not None:
                for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy(),
                                    res.boxes.conf.cpu().numpy()):
                    if int(c) == want and float(cf) >= conf:
                        x0, y0, x1, y1 = (float(v) for v in b)
                        got.append((x0, y0 + lo, x1, y1 + lo, float(cf)))
            out.append(got)
    return out
```

- [ ] **Step 4: Implement the first half of `game/spintime.py`**

```python
"""How fast the thrower set the rock turning, from the long camera facing them.

The camera is hogtime's -- the one at the far end, looking back at the thrower
-- and the rock comes towards it the whole way, from ~35 px wide at the hack to
~95 px at the bottom of the frame. From 0.5 s after the hog crossing it is read
at 10 fps, followed frame to frame from hogtime's own track, and each frame's
stone is cropped exactly as the training crops were cut (``scripts/spin/
extract.py``). The handle-point model finds the handle's neck and grip end;
``spinfit`` turns the grip's swing across the neck into a rate.

Its own read: hogtime keeps the band around the hog line (to ~10 m) and keeps
positions, not pixels, and the line pass's down-sheet path is on the other
camera. Measured in the spike (``~/curling-work/spin-spike``, 2026-10-10): 10
fps reads within 1% of 30 fps, the early rate is within ~10% of frame-by-frame
eye readings, and 122 of 128 rocks of four games' unlabelled ends are read.

Attach-only, like ``hogtime``, ``broomtime`` and ``linetime``: it may give a
shot a ``spin`` and can never add, drop or renumber one.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from curling_score.game import linetime
from curling_score.harvest import sidepool

log = logging.getLogger(__name__)

SPIN_FPS = 10.0
WINDOW_S = (0.5, 10.5)          # after the hog crossing
FROM_M = 8.0                    # past the throwing tee: the hand is off by here
MIN_CONF = 0.5                  # the weaker of the two points
STONE_M = 0.2896                # a stone's width
WIDTH_TOL = (0.6, 1.6)          # of STONE_M at the box's row
ABOVE_HOG_ROWS = 40             # the detector's rows start this far above the hog row
BOTTOM_PAD_PX = 14              # a box this close to the frame's bottom is leaving it
LOST_S = 0.8                    # unseen this long, the rock is let go
SEED_LOST_S = 1.0               # not found this long after the seed, give up
SEED_TAIL = 9                   # hogtime samples (30 fps) the seed's velocity comes from
CROP = (0.35, 0.5, 0.25)        # the training crop: each side, above, below


def seed_of(crossing, view, t_start):
    """Where hogtime's track has the rock at ``t_start``, in view pixels, and
    how fast it is moving there: ``(t, cx, bottom_row, vx, vy)``, or None."""
    if crossing is None or getattr(crossing, "track_key", None) is None \
            or not getattr(crossing, "samples", ()):
        return None
    edge = sidepool.band_crop(view)[1] - linetime.CROP_EDGE_ROWS
    samples = [s for s in crossing.samples if s[2] < edge]
    if not samples:
        return None
    t_seed = crossing.t if crossing.t is not None else samples[len(samples) // 2][0]
    chain = linetime.relink(samples, crossing.track_key, t_seed)
    if len(chain) < 3:
        return None
    i = min(range(len(chain)), key=lambda j: abs(chain[j][0] - t_start))
    tail = chain[max(0, i - SEED_TAIL + 1):i + 1] if i >= 2 else chain[:3]
    ts = np.array([s[0] for s in tail])
    cx = np.array([s[1] for s in tail])
    row = np.array([s[2] for s in tail])
    vx, vy = (np.polyfit(ts, cx, 1)[0], np.polyfit(ts, row, 1)[0]) if np.ptp(ts) > 0 else (0.0, 0.0)
    t, c, r = chain[i][0], chain[i][1], chain[i][2]
    return float(t), float(c), float(r), float(vx), float(vy)


def follow(times, boxes, seed, view, frame_h):
    """The rock frame to frame from ``seed``: ``(index, t, box)`` per frame it
    was found. Each frame's prediction runs the last speed on; the gate is a
    stone's width plus how far the speed could be off; a box far from a
    stone's width at its row is not the stone."""
    if seed is None:
        return []
    t_last, cx, row, vx, vy = seed
    out = []
    for i, (t, frame_boxes) in enumerate(zip(times, boxes)):
        dt = t - t_last
        if dt <= 0:
            continue
        if dt > (LOST_S if out else SEED_LOST_S):
            break
        px, py = cx + vx * dt, row + vy * dt
        best = None
        for b in frame_boxes:
            x0, _y0, x1, y1, _c = b
            want = STONE_M * view.lateral_px_per_m(y1)
            if want <= 0 or not WIDTH_TOL[0] * want <= x1 - x0 <= WIDTH_TOL[1] * want:
                continue
            d = math.hypot((x0 + x1) / 2 - px, y1 - py)
            if d < 0.6 * want + 4 + 0.5 * abs(vy) * dt and (best is None or d < best[0]):
                best = (d, b)
        if best is None:
            continue
        x0, _y0, x1, y1, _c = b = best[1]
        if y1 >= frame_h - BOTTOM_PAD_PX:
            break
        a = min(1.0, dt / 0.3)
        vx = (1 - a) * vx + a * ((x0 + x1) / 2 - cx) / dt
        vy = (1 - a) * vy + a * (y1 - row) / dt
        cx, row, t_last = (x0 + x1) / 2, y1, t
        out.append((i, t, b))
    return out


def crop_box(box, shape):
    """The training crop around a box, as slice bounds inside the frame."""
    x0, y0, x1, y1 = box[:4]
    w, h = x1 - x0, y1 - y0
    side, above, below = CROP
    H, W = shape[0], shape[1]
    return (max(0, int(x0 - side * w)), max(0, int(y0 - above * h)),
            min(W, int(x1 + side * w) + 1), min(H, int(y1 + below * h) + 1))
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_sidemodel.py tests/test_spintime.py -q; echo "exit $?"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/detect/sidemodel.py src/curling_score/game/spintime.py tests/test_sidemodel.py tests/test_spintime.py
git commit -m "spin: follow the rock down the camera facing its thrower, and crop it as trained

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The spin pass, wired into every end

**Files:**
- Modify: `src/curling_score/game/spintime.py` (second half: `read_spin`, `time_spins`)
- Modify: `src/curling_score/analyze.py:575-582` (after the line pass)
- Test: `tests/test_spintime.py`

**Interfaces:**
- Consumes: Task 1's `spinmodel.default_model()` / `spinmodel.handle_points`, Task 2's `spinfit.read`, Task 3's `seed_of` / `follow` / `crop_box` / `sidemodel.detect_boxes`; `longview.decode(video, rect, t0, t1, fps) -> (frames ndarray N x H x W x 3 RGB, times list)`.
- Produces: `spintime.read_spin(model, pose, video, view, shot, *, decode, detect, points) -> spinfit.Spin | None` and `spintime.time_spins(shots, video, hog_view, *, model, pose=None, decode=None, detect=None, points=None) -> int`; sets `shot.spin` (a `spinfit.Spin`) and nothing else.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_spintime.py`:

```python
from curling_score.game import spinfit


class Stubs:
    """A decode, a detector and a handle-point model for one rock turning at
    ``f`` turns a second: the grip swings 0.2 stone widths either side."""

    def __init__(self, f=0.2, speed=2.2, fail=0):
        self.f, self.speed, self.fail, self.calls = f, speed, fail, 0     # fail: the first n calls

    def decode(self, video, rect, t0, t1, fps):
        times = list(np.arange(t0, t1, 1 / fps))
        return np.zeros((len(times), 1080, 810, 3), np.uint8), times

    def detect(self, model, frames, times, lo, hi, color, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("decoder fell over")
        return [[box_at(5.0 + self.speed * (t - 100.0))] for t in times]

    def points(self, pose, patches):
        out = []
        for k, p in enumerate(patches):
            t = self.t[k]
            w = self.w[k]
            s = 0.2 * np.sin(2 * np.pi * self.f * t)
            out.append(((30.0, 20.0), (30.0 + s * w, 12.0), 0.9))
        return out


def shot(n=1, cross=True, missing=False, speed=2.2):
    return SimpleNamespace(number=n, color="red", missing=missing, t_hog_s=100.6 if cross else None,
                           hog_crossing=crossing(speed=speed) if cross else None)


def run(shots, stubs, **kw):
    # `points` needs each crop's time and box width: read_spin passes crops in
    # the order of `follow`, so the stub records them from a wrapped follow.
    real = ST.follow

    def spy(times, boxes, seed, view, frame_h):
        got = real(times, boxes, seed, view, frame_h)
        stubs.t = [t for _i, t, _b in got]
        stubs.w = [b[2] - b[0] for _i, _t, b in got]
        return got

    ST.follow = spy
    try:
        return ST.time_spins(shots, "v.mp4", VIEW, model=object(), pose=object(),
                             decode=stubs.decode, detect=stubs.detect, points=stubs.points, **kw)
    finally:
        ST.follow = real


class TestTimeSpins:
    def test_a_turning_rock_gets_its_spin_and_nothing_else(self):
        s = shot()
        before = dict(vars(s))
        assert run([s], Stubs(f=0.2)) == 1
        assert isinstance(s.spin, spinfit.Spin)
        assert s.spin.rpm == pytest.approx(12.0, rel=0.05)
        assert {k: v for k, v in vars(s).items() if k != "spin"} == before

    def test_a_rock_hogtime_never_followed_gets_none_and_the_rest_still_read(self):
        a, b, c = shot(1, cross=False), shot(2, missing=True), shot(3)
        assert run([a, b, c], Stubs()) == 1
        assert not hasattr(a, "spin") and not hasattr(b, "spin") and hasattr(c, "spin")

    def test_a_failure_on_one_rock_costs_that_rock_only(self):
        a, b = shot(1), shot(2)
        assert run([a, b], Stubs(fail=1)) == 1
        assert not hasattr(a, "spin") and hasattr(b, "spin")

    def test_no_model_or_no_lateral_calibration_is_a_no_op(self):
        s = shot()
        assert ST.time_spins([s], "v.mp4", VIEW, model=None, pose=object()) == 0
        flat = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        assert ST.time_spins([s], "v.mp4", flat, model=object(), pose=object()) == 0
        assert not hasattr(s, "spin")

    def test_no_pose_model_configured_is_no_spin(self, monkeypatch):
        from curling_score.detect import spinmodel
        monkeypatch.setattr(spinmodel, "default_model", lambda: None)
        s = shot()
        assert ST.time_spins([s], "v.mp4", VIEW, model=object()) == 0

    def test_frames_before_8_m_are_left_out(self):
        s = shot(speed=0.9)
        stubs = Stubs(f=0.2, speed=0.9)          # slow: the first frames sit short of 8 m
        run([s], stubs)
        assert s.spin.samples < len(stubs.t)
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_spintime.py -q`
Expected: FAIL (`time_spins` missing).

- [ ] **Step 3: Implement the pass**

Append to `src/curling_score/game/spintime.py` (add `from curling_score.game import spinfit` to its imports):

```python
def read_spin(model, pose, video, view, shot, *, decode, detect, points):
    """One rock's spin from the camera facing its thrower, or None."""
    crossing = shot.hog_crossing
    t_ref = shot.t_hog_s if shot.t_hog_s is not None else getattr(crossing, "t", None)
    if t_ref is None:
        # A crossing refused for speed keeps its samples but no time: take
        # where its track met the hog row.
        rows = [s for s in crossing.samples if s[2] >= view.hog_row]
        if not rows:
            return None
        t_ref = min(rows)[0]
    frames, times = decode(video, view.rect, t_ref + WINDOW_S[0], t_ref + WINDOW_S[1], SPIN_FPS)
    if not len(frames):
        return None
    frame_h = frames.shape[1]
    boxes = detect(model, frames, times, int(view.hog_row) - ABOVE_HOG_ROWS, frame_h, shot.color,
                   imgsz=800, conf=0.25)
    tracked = follow(times, boxes, seed_of(crossing, view, times[0]), view, frame_h)
    if not tracked:
        return None
    patches = []
    for i, _t, b in tracked:
        x0, y0, x1, y1 = crop_box(b, frames.shape[1:])
        patches.append(frames[i][y0:y1, x0:x1])
    ts, ss = [], []
    for (_i, t, b), p in zip(tracked, points(pose, patches)):
        if p is None or p[2] < MIN_CONF:
            continue
        w = b[2] - b[0]
        if view.metres_at(b[3] - 0.12 * w) < FROM_M:
            continue
        ts.append(t)
        ss.append((p[1][0] - p[0][0]) / w)
    return spinfit.read(np.array(ts), np.array(ss))


def time_spins(shots, video, hog_view, *, model, pose=None, decode=None, detect=None,
               points=None) -> int:
    """Give each shot its ``spin``, in place; return how many it gave one.

    ``model`` is the side detector the line pass uses (``ctx.line_model``);
    ``pose`` defaults to the handle-point model, resolved per call so importing
    this loads nothing. A no-op without either, or without a laterally
    calibrated hog-camera view. A failure on one rock is logged and costs that
    rock its spin, not the end its analysis."""
    if model is None or hog_view is None or not hog_view.has_lateral:
        return 0
    if pose is None:
        from curling_score.detect import spinmodel
        pose = spinmodel.default_model()
        if pose is None:
            return 0
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_boxes
    if points is None:
        from curling_score.detect import spinmodel
        points = spinmodel.handle_points
    n = 0
    for shot in shots:
        if getattr(shot, "missing", False) or getattr(shot, "hog_crossing", None) is None:
            continue
        try:
            got = read_spin(model, pose, video, hog_view, shot,
                            decode=decode, detect=detect, points=points)
        except Exception:
            log.exception("spin pass failed on shot %s; it keeps no spin",
                          getattr(shot, "number", "?"))
            continue
        if got is not None:
            shot.spin = got
            n += 1
    return n
```

- [ ] **Step 4: Wire it into `analyze.build_one_end`**

In `src/curling_score/analyze.py`, add `spintime` to the `from curling_score.game import (...)` list, and right after the line pass's `progress(f"    end {end.number}: lines ...")` (around line 582), inside the same `if sideviews is not None:` block:

```python
        # How fast the thrower set the rock turning: hogtime's camera again,
        # its own 10 fps read from just past the hog line. Needs hogtime's
        # crossing to start from; timed, for the live lane's budget.
        t_spin = time.monotonic()
        n_spin = spintime.time_spins(
            shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
            model=ctx.line_model)
        progress(f"    end {end.number}: spin {n_spin}/{len(shots)} "
                 f"in {time.monotonic() - t_spin:.1f} s")
```

- [ ] **Step 5: Run the tests, then the neighbours they could break**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_spintime.py tests/test_spinfit.py tests/test_linetime.py tests/test_hogtime.py -q; echo "exit $?"`
Expected: all pass. (The whole suite can OOM this box -- exit 137 is the OOM killer, see memory `test-suite-ooms-on-this-box`; run affected subsets.)

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/game/spintime.py src/curling_score/analyze.py tests/test_spintime.py
git commit -m "spin: a spin pass after the line pass, on every end, live ones too

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Timeline schema 9

**Files:**
- Modify: `src/curling_score/timeline.py` (a `_spin` beside `_line`; the shot dict; `SCHEMA_VERSION`)
- Modify: `src/curling_score/version.py` (`PIPELINE_VERSION` and its note)
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `spinfit.Spin`.
- Produces: per shot `"spin": {"rpm": float(1 dp), "rpm_avg": float(1 dp), "turns_in_view": float(2 dp), "samples": int}` or `null`; `"schema_version": 9`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_timeline.py`:

```python
class TestSpin:
    def test_a_rock_with_spin_carries_it_rounded(self):
        from curling_score.game.spinfit import Spin
        s = shot(1, "red", [])
        s.spin = Spin(rpm=10.347, rpm_avg=9.91, turns_in_view=1.8333, samples=74)
        end = timeline.build_end(1, "top", 0.0, 900.0, [s])
        assert end["shots"][0]["spin"] == {"rpm": 10.3, "rpm_avg": 9.9, "turns_in_view": 1.83,
                                           "samples": 74}

    def test_a_rock_without_is_null_not_absent(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, [shot(1, "red", [])])
        assert "spin" in end["shots"][0] and end["shots"][0]["spin"] is None

    def test_the_schema_says_spin_was_read(self):
        assert timeline.SCHEMA_VERSION == 9
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py::TestSpin -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/curling_score/timeline.py`, after `_line`:

```python
def _spin(sp):
    """A `spinfit.Spin`, or None: the rock's rotation in rpm."""
    if sp is None:
        return None
    return {"rpm": round(float(sp.rpm), 1), "rpm_avg": round(float(sp.rpm_avg), 1),
            "turns_in_view": round(float(sp.turns_in_view), 2), "samples": int(sp.samples)}
```

In the shot dict, after `"line": _line(getattr(s, "line", None)),`:

```python
                # How fast the thrower set the rock turning, rpm: the first
                # turn and a quarter from 8 m past the throwing tee, from the
                # camera facing the thrower (game/spintime.py). null when not
                # read: a hogged rock, a fast hit gone after half a turn.
                "spin": _spin(getattr(s, "spin", None)),
```

Bump `SCHEMA_VERSION = 8` to `9` and add to the comment above it (read it first and match its style) one line: `# 9: each shot's "spin" (rpm at release, game/spintime.py).`

In `src/curling_score/version.py`, add a note above `PIPELINE_VERSION` and bump it to the next version after the one on main at the time of the change, dated that day (e.g. `"2026.10.10.1"` if main is still at `2026.10.08.4`):

```python
# 2026.10.10.1: every rock's spin, rpm at release, from the camera facing the
# thrower (`game/spintime.py`, model weights/spin1.pt); timeline schema 9.
# Nothing else moves: the pass is attach-only.
```

- [ ] **Step 4: Run the tests**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py tests/test_compare_timelines.py -q; echo "exit $?"`
Expected: all pass. If a test elsewhere pins `schema_version == 8` (`grep -rn "schema_version\"\?\]\? == 8\|SCHEMA_VERSION" tests/`), update it to 9 in this commit.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py src/curling_score/version.py tests/
git commit -m "timeline: schema 9, each rock's spin

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The Rotation figure in the viewer

**Files:**
- Modify: `frontend/core/line.mjs` (`spinReadable`, `turnName`, `rotationFig`; `lineFigures` gains it)
- Modify: `tests/js/singleton.mjs` (the test fixture names each export it passes on: add `turnName` and `spinReadable` after `lineNumbers`)
- Build: `cd frontend && npm run build` (committed output under `src/curling_score/`)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `turnOf(shot)`, `hackOf(shot)`, `fig(...)`, `lineNumbers(...)` in `frontend/core/line.mjs`; the doc's `schema_version`; the shot's `spin.rpm`.
- Produces: exported `spinReadable(doc) -> boolean`, `turnName(shot, curlDir = turnOf(shot)) -> "in-turn" | "out-turn" | null`; a `rotation` figure after `weight` in every branch of `lineFigures` when `spinReadable(doc)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_viewer_js.py` (it already has `run_js` and a `shot(...)` helper; read `TestLineFigures` around line 1700 for the line block shape):

```python
class TestRotationFigure:
    """The rock's spin, rpm at release, in-turn or out-turn from the curl and
    the hack: in-turn when it curls away from its hack's side."""

    DOC9 = {"schema_version": 9}

    def rock(self, curl="right", start_x=-0.23, rpm=10.6, **kw):
        line = {"start": {"x": start_x, "y": 38.07},
                "at_hog": {"x": -0.757, "offset_m": -0.163},
                "at_broom": {"x": -2.359, "miss_m": -0.712},
                "side": "wide", "curl": curl, "confirmed": True,
                "hog_path": [[28.35, -0.757], [25.0, -0.95]],
                "path": [[20.0, -1.18], [1.35, -1.15]], "fit": {"n": 53, "rms_m": 0.004}}
        base = dict(target_broom={"x": -1.647, "y": 0.17}, long_split_s=13.79,
                    delivered_stone_index=0, stones=[{"color": "yellow", "x": -1.1529, "y": 1.3486}],
                    line=line, spin=None if rpm is None else {"rpm": rpm, "rpm_avg": rpm - 0.4,
                                                               "turns_in_view": 1.8, "samples": 74})
        base.update(kw)
        return shot(11, "yellow", "third", **base)

    def fig(self, s, doc=None):
        got = run_js(f"out(lineFigures({json.dumps(s)}, {json.dumps(doc or self.DOC9)}));")
        return {f["key"]: f for f in got["figures"]}, [f["key"] for f in got["figures"]]

    def test_left_hack_curling_right_is_an_in_turn(self):
        f, keys = self.fig(self.rock(curl="right", start_x=-0.23))
        assert (f["rotation"]["label"], f["rotation"]["value"], f["rotation"]["note"]) == (
            "Rotation", "11 rpm", "in-turn")
        assert keys.index("rotation") == keys.index("weight") + 1

    def test_left_hack_curling_left_is_an_out_turn(self):
        f, _ = self.fig(self.rock(curl="left", start_x=-0.23))
        assert f["rotation"]["note"] == "out-turn"

    def test_right_hack_mirrors_it(self):
        assert self.fig(self.rock(curl="left", start_x=0.20))[0]["rotation"]["note"] == "in-turn"
        assert self.fig(self.rock(curl="right", start_x=0.20))[0]["rotation"]["note"] == "out-turn"

    def test_no_curl_says_the_turn_is_not_known_but_keeps_the_rpm(self):
        f, _ = self.fig(self.rock(curl=None))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("11 rpm", "turn not known")

    def test_a_rock_without_a_line_still_shows_its_rpm(self):
        f, _ = self.fig(self.rock(line=None))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("11 rpm", "turn not known")

    def test_a_rock_not_read_says_so(self):
        f, _ = self.fig(self.rock(rpm=None))
        assert (f["rotation"]["value"], f["rotation"]["note"]) == ("–", "not read")

    def test_a_chart_from_before_spin_has_no_rotation_figure(self):
        _, keys = self.fig(self.rock(), {"schema_version": 8})
        assert "rotation" not in keys

    def test_turn_name_on_its_own(self):
        got = run_js("out([turnName({hack: {side: 'left'}}, 'right'), turnName({hack: {side: 'left'}}, 'left'),"
                     " turnName({hack: {side: 'right'}}, 'left'), turnName({}, 'right'),"
                     " turnName({hack: {side: 'left'}}, null)]);")
        assert got == ["in-turn", "out-turn", "in-turn", None, None]
```

- [ ] **Step 2: Run them to see them fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py::TestRotationFigure -q`
Expected: FAIL (`rotation` key missing, `turnName` undefined).

- [ ] **Step 3: Implement in `frontend/core/line.mjs`**

After `hackFig` (around line 264):

```js
/* Has this chart's pipeline read spin at all: schema 9 on. Charts from before
 * were not reprocessed, so they show no Rotation figure rather than a dash on
 * every rock. */
export function spinReadable(doc) {
  return (doc?.schema_version ?? 0) >= 9;
}

/* In-turn or out-turn. A stone curls the way it turns, and a right-hander --
 * on the left hack, as nearly everyone is -- turns an in-turn clockwise, which
 * curls right. So in-turn when the rock curls away from its hack's side,
 * out-turn when it curls toward it; null without both. */
export function turnName(shot, curlDir = turnOf(shot)) {
  const hack = hackOf(shot);
  if (!hack || (curlDir !== "left" && curlDir !== "right")) return null;
  return curlDir === hack ? "out-turn" : "in-turn";
}

/* The rock's spin as thrown, in whole rpm, and which turn. */
function rotationFig(shot, curlDir) {
  const rpm = shot?.spin?.rpm;
  if (typeof rpm !== "number") return fig("rotation", "Rotation", "–", "not read");
  return fig("rotation", "Rotation", `${Math.round(rpm)} rpm`, turnName(shot, curlDir) ?? "turn not known");
}
```

In `lineFigures`, after `const weight = ...` add:

```js
  // After Weight: the two things the thrower set, how hard and how much turn.
  const withRotation = figs => (spinReadable(doc)
    ? figs.flatMap(f => (f.key === "weight" ? [f, rotationFig(shot, n.turn)] : [f]))
    : figs);
```

and wrap each of the three `figures: [...]` arrays it returns in `withRotation(...)` (the `reason` branch, the `broomless` branch and the last return). `n.turn` is null in the `reason` branch, which reads as "turn not known".

In `tests/js/singleton.mjs`, after `export const lineNumbers = core.lineNumbers;`:

```js
export const turnName = core.turnName;
export const spinReadable = core.spinReadable;
```

(This step's code was dry-run before the plan was handed over: with it, the whole of `tests/test_viewer_js.py` passed, the new class included.)

- [ ] **Step 4: Run the tests, then build**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py -q > /tmp/js.log 2>&1; echo "exit $?"; tail -3 /tmp/js.log`
Expected: `exit 0`. A test elsewhere that pinned the exact figure list of a schema-9 doc would now see `rotation`; fix such a test only if its doc is schema 9 (most use 6).

Then build the shipped bundle: `cd frontend && npm run build` (never esbuild directly). Expected: lint clean, `src/curling_score/viewer/app.js` and `frontend/.buildstamp.json` updated.

- [ ] **Step 5: Look at it on a phone-sized page**

Follow memory `headless-phone-check` (devserve + `scripts/cdp.mjs` at 390x844 with touch emulation) with a schema-9 timeline that has `spin` on its rocks -- e.g. take `~/curling-work/twoline/timeline_s_0N8Q2sB4vY8Hv4Ooq.json`, set `schema_version` to 9 and add `"spin": {"rpm": 10.6, ...}` to a few shots with a short python snippet. Screenshot the Detail pane and the desktop card; check the seventh figure sits cleanly in the grid. If the phone grid leaves Rotation alone on a row and that looks broken, fix the CSS in `src/curling_score/viewer/style.css` (hand-authored, outside the build).

- [ ] **Step 6: Commit**

```bash
git add frontend/core/line.mjs frontend/.buildstamp.json src/curling_score/viewer/ tests/test_viewer_js.py tests/js/singleton.mjs
git commit -m "viewer: Rotation, rpm at release, in-turn or out-turn from the curl and the hack

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Measure it on real games before it ships (the 3070 laptop)

**Files:**
- Create: `scripts/spin/ab_parity.py` (compare a branch timeline's spin with the spike's rates)
- No production code changes unless the numbers say so.

**Interfaces:**
- Consumes: the A/B harness on the laptop (`/data/wdd/scratch/run_ab.sh`, `run_analyze.py`; see memory `worker-ab-analyze`), the spike's rates: `~/curling-work/spin/rate_p5_phbz.json` (PHbZ, all ends) and `~/curling-work/spin/rates/p5_{seen,unseen}_<video>.json` (0SWB, VVgL, QQpN, dea7, ends 2-6).

- [ ] **Step 1: Prepare the A/B on the laptop** (`ssh administrator@100.80.66.81`, idle: no production workers there)

```bash
# base = main, new = this branch, as source trees the image runs with PYTHONPATH
git archive main src | ssh administrator@100.80.66.81 'rm -rf /data/wdd/scratch/code-base && mkdir -p /data/wdd/scratch/code-base && tar -x -C /data/wdd/scratch/code-base'
git archive HEAD src | ssh administrator@100.80.66.81 'rm -rf /data/wdd/scratch/code-new && mkdir -p /data/wdd/scratch/code-new && tar -x -C /data/wdd/scratch/code-new'
scp weights/spin1.pt administrator@100.80.66.81:/data/wdd/scratch/spin/weights/spin1.pt
```

On the laptop, copy `run_ab.sh` to `run_ab_spin.sh` and add to its `docker run` line
`-v /data/wdd/scratch/spin/weights/spin1.pt:/opt/curling/weights/spin1.pt:ro -e CURLING_SCORE_SPIN_WEIGHTS=/opt/curling/weights/spin1.pt`
(main's code never reads it). Run the two sides **in sequence** (the script already does; parallel containers collide on the detection cache, memory `worker-ab-analyze`). Both sides use the image's own overhead, side and broom models, so only the spin pass differs. Start it detached (`ssh -f administrator@100.80.66.81 'cd /data/wdd/scratch && nohup sh -c "..." > runs/spin_ab.log 2>&1 < /dev/null &'`) with these three games, all cached on the laptop:

```bash
./run_ab_spin.sh PHbZ3EKOhMI "09/27 - Sheet 3 - Sunday Skips Semi-CompetitiveLeague 2026-2027" 8995 3
./run_ab_spin.sh 0SWB4g3SJoE "10/01 - Sheet 1 - Thursday Mens League 2026-2027" 16195 1
./run_ab_spin.sh VVgLH4uBxYs "09/28 - Sheet 2 - Monday Open League 2026-2027" 16205 2
```

About 10-11 min per run on the 3070 for PHbZ, longer for the two four-hour videos; allow ~1.5 h, and watch `runs/progress.txt`. Chmod the root-owned outputs before copying them back (memory `worker-ab-analyze`).

- [ ] **Step 2: Write `scripts/spin/ab_parity.py`**

```python
"""A branch timeline's spin against the spike's rates for the same rocks.

    python scripts/spin/ab_parity.py new/timeline.json SPIKE_RATES.json [...]

The spike read every rock at 30 fps over its own crops with the same model;
the pipeline reads at 10 fps. Pairs rocks by (end, shot); prints how many each
read, and the pipeline's rpm against 60 x the spike's early rate.
"""
import json
import sys

import numpy as np

doc = json.load(open(sys.argv[1]))
spike = {}
for path in sys.argv[2:]:
    for r in json.load(open(path)):
        if "f_early" in r and r["a"] >= 0.1 and r["a"] / r["rms"] >= 3 and r["turns_in_view"] >= 0.5:
            spike[r["rock"]] = 60.0 * r["f_early"]
pairs, only_pipe, only_spike, total = [], 0, 0, 0
for g in doc["games"]:
    for e in g["ends"]:
        for s in e["shots"]:
            if s.get("missing"):
                continue
            total += 1
            k = f"e{e['number']}s{s['number']:02d}"
            got = (s.get("spin") or {}).get("rpm")
            if got is not None and k in spike:
                pairs.append((k, got, spike[k]))
            elif got is not None:
                only_pipe += 1
            elif k in spike:
                only_spike += 1
d = np.array([abs(a / b - 1) for _k, a, b in pairs])
print(f"rocks {total}: both {len(pairs)}, pipeline only {only_pipe}, spike only {only_spike}")
if len(d):
    print(f"pipeline vs spike: median {np.median(d) * 100:.1f}%, 90% {np.percentile(d, 90) * 100:.1f}%, "
          f">10%: {[(k, round(a, 1), round(b, 1)) for k, a, b in pairs if abs(a / b - 1) > 0.10]}")
```

- [ ] **Step 3: Read the three numbers and record them**

1. **Parity:** `ab_parity.py runs/<vid>-new/timeline.json <spike rates for that video>`. Expect `both` >= 90% of the spike's reads and median difference <= 3% (this fit on the spike's own crops at 10 fps: 1.2% median on PHbZ). Rocks under one turn in view (the slowest, ~3-4 rpm) may differ by ~1 rpm. Note: the spike's rates for the four other games cover ends 2-6 only.
2. **Nothing else moved:** the pass is attach-only; check it with `python scripts/compare_timelines.py runs/<vid>-new/timeline.json runs/<vid>-base/timeline.json` and expect the same few differences a base-vs-base rerun shows (segmentation jitter, memory `worker-ab-analyze`), none caused by spin.
3. **Cost:** the new run's log prints `end N: spin a/b in X s` per end; sum X per game, divide by the number of rocks, and compare with the end's total time in the same log. The spec's estimate is 1-1.5 s per rock on the 3070, +15-25% per end.

If parity fails (median > 5%), compare one rock's crops (`scripts/spin/clips.py` on the spike pickle against crops from the pipeline) before changing any constant. If the cost is more than ~25% per end, tell the user before Task 8: the cheapest lever is a shorter `WINDOW_S` (the early window ends within ~9 s of 8 m), the next an imgsz-416 pass below row 640 as `linetime.find_path` does.

- [ ] **Step 4: Commit the script and write the numbers into the spec**

Add a "Measured on real games" section to `docs/superpowers/specs/2026-10-10-release-spin-design.md` with the three numbers per video.

```bash
git add scripts/spin/ab_parity.py docs/superpowers/specs/2026-10-10-release-spin-design.md
git commit -m "spin: parity and cost on three games before it ships

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Merge and deploy (only after the user says go)

**Files:** none new.

- [ ] **Step 1: Show the user Task 7's numbers and ask to merge and deploy.** Live games are included by the user's decision, so the per-end cost matters most; do not deploy without an explicit go.

- [ ] **Step 2: Merge into local main** (fast-forward if main has not moved; otherwise merge main into `release-spin` first and rerun the affected tests). Check `git status` on the main checkout first: another session works there (memory `concurrent-session-commits`).

- [ ] **Step 3: Deploy, per memory `deploying-curling-chart`:**
  - From a clean worktree of the merged HEAD (`git worktree add --detach .claude/worktrees/deploy-<sha> <sha>`), copy `firebase_api_key.json` in from the main checkout.
  - **Worker** (the Ryzen, `curling@100.85.240.114`): dry run `rsync -anc --itemize-changes --exclude deploy/worker.env --exclude .git --exclude .venv --exclude node_modules --exclude 'out*'` to see exactly which files change (expect `weights/spin1.pt`, `Dockerfile.worker`, the new modules, `analyze.py`, `timeline.py`, `version.py`, the viewer bundle), then the real rsync; `docker compose -f docker-compose.worker.yml build`; check every worker's log for an unfinished `job j_...` and for live streams, and only then `up -d` (it recreates all three workers and drops live jobs). Confirm the running container has the model: `docker compose exec -T worker python -c "from curling_score import weights; print(weights.spin_path())"`.
  - **API** (serves the viewer): `PROJECT_ID=curling-stats-508323 REGION=us-west1 PUBLIC_BASE_URL=https://curling.dimmit.net ./deploy/deploy-api.sh` from the deploy worktree; check it prints `accounts: sign-in is live`. If the classifier blocks it, hand the user the command.
  - **No reprocess.** The user chose not to backfill: new games and live games only.

- [ ] **Step 4: Check the first live or newly processed game** has `spin` on its rocks (`/g/<slug>/timeline.json`) and that its `end N: spin a/b in X s` lines are within the measured budget; tell the user, and note in memory `spin-rate-spike`.
