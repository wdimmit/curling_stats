"""The destination crossing, attached to each shot from both panels' lines."""

from types import SimpleNamespace

import pytest

from curling_score.game import fartime, split
from tests.synth_hogline import line_at

FAR = line_at(4.44)
NEAR = line_at(4.44)


def arriving(t0=20.0, y0=4.8, speed=1.0, fps=10.0, n=30):
    return tuple((round(t0 + i / fps, 3), 0.0, round(y0 - i * speed / fps, 4)) for i in range(n))


def leaving(t0=0.0, y0=-2.0, speed=2.0, fps=5.0, n=40):
    return tuple((round(t0 + i / fps, 3), 0.05, round(y0 + i * speed / fps, 4)) for i in range(n))


def shot(arrival=None, release=None, missing=False):
    return SimpleNamespace(
        missing=missing,
        delivery=None if arrival is None else SimpleNamespace(track=arrival),
        release=None if release is None else SimpleNamespace(track=release))


def test_an_observed_crossing_is_attached():
    s = shot(arrival=arriving())
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    fc = fartime.crossing(s)
    assert fc.t == pytest.approx(split.line_crossing(arriving(), FAR))
    assert fc.reach == 0.0
    assert fc.v_far == pytest.approx(1.0, abs=0.01)


def test_a_reached_for_crossing_is_attached_with_its_reach():
    s = shot(arrival=arriving(y0=4.34))
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    fc = fartime.crossing(s)
    assert fc.t is not None and fc.t < 20.0
    assert fc.reach == pytest.approx(0.10, abs=1e-6)
    assert fc.v_far is None


def test_an_arrival_beyond_the_cap_has_no_crossing():
    s = shot(arrival=arriving(y0=4.10))
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    assert fartime.crossing(s).t is None


def test_the_throwing_panel_gives_its_own_crossing_and_speed():
    s = shot(arrival=arriving(), release=leaving())
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    fc = fartime.crossing(s)
    assert fc.t_near_panel == pytest.approx(split.line_crossing(leaving(), NEAR))
    assert fc.v_near == pytest.approx(2.0, abs=0.01)


def test_a_panel_with_no_line_times_nothing_there():
    s = shot(arrival=arriving(), release=leaving())
    fartime.time_far_crossings([s], near_line=None, far_line=None)
    fc = fartime.crossing(s)
    assert fc.t is None and fc.v_far is None
    assert fc.t_near_panel is None and fc.v_near is None


def test_a_shot_with_no_delivery_has_no_far_crossing():
    s = shot(release=leaving())
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    assert fartime.crossing(s).t is None


def test_a_placeholder_shot_is_left_alone():
    s = shot(arrival=arriving(), missing=True)
    fartime.time_far_crossings([s], near_line=NEAR, far_line=FAR)
    assert fartime.crossing(s) is None


def test_it_never_changes_the_shot_list():
    shots = [shot(arrival=arriving()), shot(), shot(missing=True)]
    fartime.time_far_crossings(shots, near_line=NEAR, far_line=FAR)
    assert len(shots) == 3
