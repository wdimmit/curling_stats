"""``scripts/mark_release.py`` is loaded by path, as ``test_mark_hog.py`` loads
its sibling. What it shows a person is what every mark in
``datasets/releasemarks`` was made through: the crop follows the rock, and past
the end of the delivery path it follows a guess, which these pin down.
"""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mark_release", Path(__file__).resolve().parents[1] / "scripts" / "mark_release.py")
mark_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mark_release)

TEE_Y = mark_release.TEE_Y


def path(speed=2.0, x_per_s=0.05, until=3.0, step=0.1):
    """[t, y, x] like ``line.delivery``: y house metres from the far tee."""
    n = int(round(until / step))
    return [[i * step, TEE_Y - speed * i * step, x_per_s * i * step] for i in range(n + 1)]


class TestTheCropFollowsTheRock:
    def test_on_the_path_it_interpolates(self):
        x, yp = mark_release.rock_at(path(), 1.05)
        assert yp == pytest.approx(2.1)
        assert x == pytest.approx(0.0525)

    def test_past_the_path_it_carries_on_at_the_last_speed(self):
        # A hand still on at hog +1.5 m has to stay in the crop for the
        # second after, or "still on at the end" could not be told from lost.
        x, yp = mark_release.rock_at(path(), 3.0 + 1.2)
        assert yp == pytest.approx(2.0 * 4.2)
        assert x == pytest.approx(0.05 * 4.2)
