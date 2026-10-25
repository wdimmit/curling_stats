"""``sequence`` reads a panel through its cut, from a path or a feed."""

import numpy as np
import pytest

from curling_score.detect import sequence
from curling_score.feeds import Cut, as_feed
from curling_score.game.profile import PanelSetup
from curling_score.geometry.calibrate import PanelCalib

RECT = (4, 6, 40, 60)


def setup(**over):
    return PanelSetup(rect=RECT, calib=PanelCalib(
        center_px=(20.0, 30.0), px_per_m=40.0, edge_erosion_px=2.0,
        residual_m=0.001, flipped=False), **over)


@pytest.fixture
def seen(monkeypatch):
    """What the readers are asked for, with a frame to hand back."""
    calls = []

    def chunked(path, start_s, end_s, fps, size, crop):
        calls.append(("chunked", path, crop))
        yield [(start_s, np.zeros((60, 40, 3), np.uint8))]

    def windows(path, specs, end_s):
        calls.append(("windows", path, [c for _s, _f, c in specs]))
        return iter(())

    monkeypatch.setattr(sequence.F, "chunked", chunked)
    monkeypatch.setattr(sequence.F, "windows", windows)
    return calls


def test_detect_span_reads_the_panel_through_its_cut(seen):
    s = setup(name="top")
    sequence.detect_span("v.mp4", s, 0.0, 1.0, 5.0, use_cache=False)
    assert seen == [("chunked", "v.mp4", s.cut)]


def test_detect_span_feed_equals_path(seen):
    s = setup(name="top")
    sequence.detect_span("v.mp4", s, 0.0, 1.0, 5.0, use_cache=False)
    sequence.detect_span(as_feed("v.mp4"), s, 0.0, 1.0, 5.0, use_cache=False)
    (_, _, by_path), (_, feed, by_feed) = seen
    assert by_feed == by_path == Cut("top", RECT)
    assert str(feed) == "v.mp4"


def test_detect_spans_feed_equals_path(seen):
    specs = [(setup(name="top"), 0.0, 5.0), (setup(name="bottom"), 0.0, 5.0)]
    sequence.detect_spans("v.mp4", specs, 1.0)
    sequence.detect_spans(as_feed("v.mp4"), specs, 1.0)
    assert seen[0][2] == seen[1][2] == [Cut("top", RECT), Cut("bottom", RECT)]


def test_a_cached_span_keys_the_same_for_a_feed(seen, tmp_path, monkeypatch):
    monkeypatch.setenv("CURLING_SCORE_CACHE", str(tmp_path / "cache"))
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x" * 64)
    s = setup(name="top")
    sequence.detect_span(str(v), s, 0.0, 1.0, 5.0)
    sequence.detect_span(as_feed(v), s, 0.0, 1.0, 5.0)
    assert [c[0] for c in seen] == ["chunked"]  # the second was a hit
