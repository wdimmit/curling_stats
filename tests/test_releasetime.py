"""The release read: hand crops cut along the delivery path, the frame stacks
the model reads, the on-to-off step fit, and the pass that runs them over an
end's shots on the frames the lane keeper kept."""
import dataclasses
import logging
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.detect import lanekeep
from curling_score.game import hogtime, linetime, releasetime
from curling_score.geometry.sideview import SideView

# 10/20 Wed sheet 2's left view.
VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=360.8, hog_row=433.0,
                centre_col=399.21, lat_px_per_m_at_tee=117.425,
                centre_line=(394.038, 0.007471))
TEE_Y = linetime.TEE_Y
T_REL = 100.0

# A throw from half a metre behind the throwing tee to 8 m past it, 3 s,
# drifting a little to the thrower's right: `[t, y, x]`.
DELIVERY = [[3.0 * i / 20, TEE_Y - (-1.0 + 9.0 * i / 20), 0.05 + 0.1 * i / 20]
            for i in range(21)]


def lane(t0=T_REL - 0.2, t1=T_REL + 3.5, dx=0.0):
    """Strips of the lane at 30 fps with a bright 3x3 dot at the rock's image
    point in each, or ``dx`` metres to the thrower's right of it; the
    `LaneKeeper`'s origin, and the strips' times."""
    row0, row1, col0, col1 = lanekeep.strip_box(VIEW)
    times = np.arange(round(t0 * 30), round(t1 * 30)) / 30
    strips = np.zeros((len(times), row1 - row0, col1 - col0), np.uint8)
    for i, t in enumerate(times):
        at = releasetime.rock_at(DELIVERY, t - T_REL)
        if at is None:
            continue
        y, x = at
        col, row = VIEW.to_image(-(x + dx), TEE_Y - y)
        r, c = round(row) - row0, round(col) - col0
        strips[i, r - 1:r + 2, c - 1:c + 2] = 255
    return strips, times, (col0, row0)


def test_rock_at_interpolates_the_path_and_is_none_outside_it():
    y, x = releasetime.rock_at(DELIVERY, 0.075)
    assert y == pytest.approx((DELIVERY[0][1] + DELIVERY[1][1]) / 2)
    assert x == pytest.approx((DELIVERY[0][2] + DELIVERY[1][2]) / 2)
    assert releasetime.rock_at(DELIVERY, -0.01) is None
    assert releasetime.rock_at(DELIVERY, 3.01) is None
    assert releasetime.rock_at(DELIVERY, 3.0) == pytest.approx(DELIVERY[-1][1:])


def test_the_rock_sits_at_the_same_spot_in_every_crop():
    strips, times, origin = lane()
    crops, o, yp = releasetime.hand_crops(strips, times, origin, VIEW, DELIVERY, T_REL)
    assert crops.shape[1:] == (107, 160) and crops.dtype == np.uint8
    assert len(crops) > 40
    for c in crops:
        r, col = np.unravel_index(c.argmax(), c.shape)
        assert abs(col - 80) <= 2 and abs(r - 88) <= 2       # 0.5 m across, 0.55 m up at 160 px/m


def test_the_crop_is_160_pixels_to_the_metre():
    # 0.3 m to the thrower's right is 0.3 m to the left in the view, 48 px.
    for dx, want in ((0.3, 80 - 48), (-0.3, 80 + 48)):
        strips, times, origin = lane(dx=dx)
        crops, _o, _yp = releasetime.hand_crops(strips, times, origin, VIEW, DELIVERY, T_REL)
        for c in crops:
            r, col = np.unravel_index(c.argmax(), c.shape)
            assert abs(col - want) <= 2 and abs(r - 88) <= 2


def test_the_crop_height_is_what_the_constants_say():
    assert releasetime.CROP_PX == (160, round((releasetime.CROP_UP_M + releasetime.CROP_DOWN_M) * 160))


def test_crops_span_half_a_metre_behind_the_tee_to_the_path_end():
    strips, times, origin = lane()
    crops, o, yp = releasetime.hand_crops(strips, times, origin, VIEW, DELIVERY, T_REL)
    assert len(crops) == len(o) == len(yp) < len(strips)
    assert yp.min() >= -0.5 - 0.1 and yp.max() <= TEE_Y - DELIVERY[-1][1] + 1e-6
    assert o.min() >= DELIVERY[0][0] and o.max() <= DELIVERY[-1][0]


def test_stacks_take_the_frames_two_and_four_back():
    o = np.arange(10) / 30
    crops = np.stack([np.full((107, 160), 10 * i, np.uint8) for i in range(10)])
    s = releasetime.stacks(crops, o)
    assert s.shape == (10, 3, 107, 160) and s.dtype == np.float32
    for i in (4, 7, 9):
        assert [s[i, k, 0, 0] * 255 for k in range(3)] == pytest.approx(
            [10 * (i - 4), 10 * (i - 2), 10 * i])
    # Clamped to the sequence at the start.
    assert [s[0, k, 0, 0] * 255 for k in range(3)] == pytest.approx([0, 0, 0])
    assert [s[1, k, 0, 0] * 255 for k in range(3)] == pytest.approx([0, 0, 10])
    assert s.max() <= 1.0


def test_stacks_pick_by_time_on_a_thinned_sequence():
    o = np.arange(0, 20, 2) / 30                       # every other frame
    crops = np.stack([np.full((107, 160), 10 * i, np.uint8) for i in range(10)])
    s = releasetime.stacks(crops, o)
    # Four frames back is two crops back, two frames back is one.
    assert [s[5, k, 0, 0] * 255 for k in range(3)] == pytest.approx([30, 40, 50])


def test_step_fit_finds_the_switch():
    assert releasetime.step_fit(np.array([0, .1, 0, .9, 1, 1.])) == 3


def test_a_switch_on_the_first_frames_or_never_is_no_release():
    o = np.linspace(0.2, 3.0, 43)
    assert releasetime.release_at(np.r_[[0.0], np.ones(42)], o, DELIVERY) is None
    assert releasetime.release_at(np.zeros(43), o, DELIVERY) is None


def test_release_is_the_rocks_point_on_the_path():
    o = np.linspace(0.2, 3.0, 43)
    t, y, x = releasetime.release_at(np.r_[np.zeros(20), np.ones(23)], o, DELIVERY)
    assert t == pytest.approx(o[20])
    assert (y, x) == pytest.approx(releasetime.rock_at(DELIVERY, o[20]))


# The pass over an end's shots.

class Keeper:
    """A lane keeper's reading side, over the strips `lane` draws."""

    def __init__(self, strips=None, times=None, origin=None):
        if strips is None:
            strips, times, origin = lane()
        self.strips, self.times, self.origin = strips, times, origin
        self.asked = []

    def frames_between(self, t0, t1):
        self.asked.append((t0, t1))
        keep = (self.times >= t0 - 1e-6) & (self.times < t1 - 1e-6)
        return self.strips[keep], self.times[keep]


def line_with(delivery=tuple(tuple(s) for s in DELIVERY)):
    return linetime.Line(
        start=(0.05, TEE_Y + 1.0), at_hog_x=0.1, at_hog_offset=0.01, at_broom_x=0.2,
        miss=0.02, curl="left", side="wide", confirmed=True, hog_path=((30.0, 0.1),),
        path=((20.0, 0.2),), fit_n=12, fit_rms=0.01, delivery=delivery)


def rock(number=1, line=None):
    return SimpleNamespace(number=number, release=SimpleNamespace(t=T_REL),
                           line=line_with() if line is None else line)


STEP_AT = 20      # the crop the stub model sees the hand come off on


def step_infer(model, stacks):
    return (np.arange(len(stacks)) >= STEP_AT).astype(float)


# A view with no lateral fit: it can time a crossing but place nothing across.
FLAT = dataclasses.replace(VIEW, centre_col=None, lat_px_per_m_at_tee=None,
                           centre_line=None)


def test_a_rock_with_a_delivery_gets_its_release():
    shot, keeper, seen = rock(), Keeper(), []
    model = object()

    def infer(m, stacks):
        seen.append(m)
        return step_infer(m, stacks)

    n = releasetime.time_releases([shot], keeper, VIEW, model=model, infer=infer)
    _crops, o, _yp = releasetime.hand_crops(*lane(), VIEW, DELIVERY, T_REL)
    assert n == 1 and seen == [model]
    assert shot.line.release == pytest.approx(
        (o[STEP_AT], *releasetime.rock_at(DELIVERY, o[STEP_AT])))
    # From a little before the path's first point past FROM_M (0.3 s, 0.1 m
    # past the tee) to just past its last.
    assert keeper.asked == [pytest.approx((T_REL + 0.1, T_REL + 3.05))]


def test_nothing_else_on_the_line_changes():
    shot = rock()
    before = shot.line
    releasetime.time_releases([shot], Keeper(), VIEW, model=object(), infer=step_infer)
    assert shot.line.release is not None
    assert dataclasses.replace(shot.line, release=None) == before


def test_a_failing_read_leaves_no_release_and_the_rest(caplog):
    calls = []

    def flaky(model, stacks):
        calls.append(len(stacks))
        if len(calls) == 1:
            raise RuntimeError("the GPU fell over")
        return step_infer(model, stacks)

    first, second = rock(1), rock(2)
    with caplog.at_level(logging.ERROR):
        n = releasetime.time_releases([first, second], Keeper(), VIEW, model=object(),
                                      infer=flaky)
    assert n == 1 and len(calls) == 2
    assert first.line.release is None and second.line.release is not None
    assert "GPU fell over" in caplog.text


def test_rocks_without_a_path_or_release_are_skipped():
    called = []

    def infer(model, stacks):
        called.append(1)
        return step_infer(model, stacks)

    shots = [SimpleNamespace(number=1, release=SimpleNamespace(t=T_REL), line=None),
             rock(2, line=line_with(())),
             SimpleNamespace(number=3, release=None, line=line_with()),
             SimpleNamespace(number=4)]
    assert releasetime.time_releases(shots, Keeper(), VIEW, model=object(), infer=infer) == 0
    assert called == [] and shots[2].line.release is None


def test_a_rock_with_no_frames_kept_is_not_read():
    called = []
    shot = rock()
    keeper = Keeper(*lane(t0=T_REL + 10, t1=T_REL + 11))
    n = releasetime.time_releases([shot], keeper, VIEW, model=object(),
                                  infer=lambda m, s: called.append(1))
    assert n == 0 and called == [] and shot.line.release is None


def test_no_model_keeper_or_lateral_fit_is_a_no_op():
    shot = rock()
    for keeper, view, model in ((Keeper(), VIEW, None), (None, VIEW, object()),
                                (Keeper(), FLAT, object()), (Keeper(), None, object())):
        assert releasetime.time_releases([shot], keeper, view, model=model,
                                         infer=step_infer) == 0
    assert shot.line.release is None


def test_the_keeper_keeps_from_a_second_before_each_release_to_seven_after():
    shots = [SimpleNamespace(release=SimpleNamespace(t=100.0)),
             SimpleNamespace(release=SimpleNamespace(t=130.5))]
    assert releasetime.keep_windows(shots) == [(99.0, 107.0), (129.5, 137.5)]


def test_a_side_or_missing_release_keeps_hogtimes_own_window():
    # No release yet: the long camera may give it one (sidereleases) from
    # hogtime's frames, so those are kept, a second wider either side.
    shot = SimpleNamespace(release=None, delivery=SimpleNamespace(t_enter=200.0))
    t0, t1 = hogtime.window_for(shot)
    assert releasetime.keep_windows([shot]) == [pytest.approx((t0 - 1.0, t1 + 1.0))]
    blank = SimpleNamespace(missing=True, release=None, delivery=None)
    assert releasetime.keep_windows([blank]) == []


def test_a_keeper_is_made_only_for_a_read():
    shots = [SimpleNamespace(release=SimpleNamespace(t=100.0))]
    keeper = releasetime.keeper_for(shots, VIEW, model=object())
    assert isinstance(keeper, lanekeep.LaneKeeper)
    assert keeper.view is VIEW and keeper.windows == [(99.0, 107.0)]
    # No model, or nowhere to place the rock across the sheet: nothing is kept.
    assert releasetime.keeper_for(shots, VIEW, model=None) is None
    assert releasetime.keeper_for(shots, FLAT, model=object()) is None
    assert releasetime.keeper_for(shots, None, model=object()) is None


def test_a_release_found_after_the_keeper_was_made_keeps_its_seconds():
    # No release when the end's keeper is made: it keeps hogtime's window for
    # the rock, which hangs off its arrival and ends 7 s before it. The long
    # camera then finds a release 10 s before the arrival (`sidereleases`),
    # and the delivery read decodes on past where that window ended.
    shot = SimpleNamespace(release=None, delivery=SimpleNamespace(t_enter=220.0))
    keeper = releasetime.keeper_for([shot], VIEW, model=object())
    t0, t1 = hogtime.window_for(shot)
    early = np.zeros((4, 1080, 810, 3), np.uint8)
    keeper.offer(VIEW.rect, early, [t0 + i / 30 for i in range(4)], 30.0)
    assert keeper.nbytes > 0
    shot.release = SimpleNamespace(t=210.0)
    releasetime.follow_releases(keeper, [shot])
    # Kept for the release now, and what was kept only for the arrival is gone.
    assert keeper.windows == [(209.0, 217.0)] and keeper.nbytes == 0
    tail = [t1 + 1.0 + i / 30 for i in range(6)]              # past the old window
    keeper.offer(VIEW.rect, np.zeros((6, 1080, 810, 3), np.uint8), tail, 30.0)
    assert len(keeper.frames_between(209.0, 217.0)[1]) == 6


def test_a_rock_still_without_a_release_lets_go_of_its_frames():
    # The long camera found none for it either, so nothing will read it: the
    # ~14 s kept off its arrival goes, and the rock with a release keeps its own.
    held = SimpleNamespace(release=SimpleNamespace(t=100.0))
    lost = SimpleNamespace(release=None, delivery=SimpleNamespace(t_enter=220.0))
    keeper = releasetime.keeper_for([held, lost], VIEW, model=object())
    t0, t1 = hogtime.window_for(lost)
    assert keeper.windows == [(99.0, 107.0), pytest.approx((t0 - 1.0, t1 + 1.0))]
    frames = np.zeros((4, 1080, 810, 3), np.uint8)
    keeper.offer(VIEW.rect, frames, [100.0 + i / 30 for i in range(4)], 30.0)
    keeper.offer(VIEW.rect, frames, [t0 + i / 30 for i in range(4)], 30.0)
    releasetime.follow_releases(keeper, [held, lost])
    assert keeper.windows == [(99.0, 107.0)]
    assert len(keeper.frames_between(99.0, 107.0)[1]) == 4
    assert keeper.nbytes == 4 * keeper.frames_between(99.0, 107.0)[0][0].nbytes


def test_following_releases_without_a_keeper_is_a_no_op():
    releasetime.follow_releases(None, [SimpleNamespace(release=SimpleNamespace(t=1.0))])
