"""The input's two halves: a ``Cut`` (how a view is taken from a file) and a
``Feed`` (which file each view lives in)."""

import os
from pathlib import Path

import numpy as np
import pytest

from curling_score.cameras import layout
from curling_score.feeds import VIEWS, Cut, Feed, as_feed


def test_crop_cut_steps_match_longview():
    c = Cut("left", (0, 0, 813, 1080))
    assert c.steps() == "crop=813:1080:0:0:exact=1"
    assert c.out_size == (813, 1080)
    assert c.is_crop


def test_turned_cut_steps():
    c = Cut("top", (518, 255, 1319, 814), 90, (298, 510))
    assert c.steps() == "crop=1319:814:518:255:exact=1,transpose=1,scale=298:510"
    assert c.out_size == (298, 510)
    assert not c.is_crop


def test_counter_clockwise_is_transpose_2():
    assert Cut("", (0, 0, 40, 20), -90).steps() == "crop=40:20:0:0:exact=1,transpose=2"


def test_turned_cut_without_scale_swaps_size():
    c = Cut("top", (0, 0, 1319, 814), 90)
    assert c.out_size == (814, 1319)
    assert Cut("top", (0, 0, 1319, 814), -90).out_size == (814, 1319)


def test_rotation_must_be_a_quarter_turn():
    with pytest.raises(ValueError, match="rotate"):
        Cut("top", (0, 0, 4, 4), 180)


def test_apply_crop_is_the_slice():
    arr = np.random.default_rng(0).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
    out = Cut("left", (100, 20, 813, 600)).apply(arr)
    assert np.array_equal(out, arr[20:620, 100:913])
    assert out.flags.c_contiguous


def test_apply_turned_raises():
    arr = np.zeros((1080, 1920, 3), np.uint8)
    with pytest.raises(NotImplementedError):
        Cut("top", (0, 0, 100, 100), 90).apply(arr)
    with pytest.raises(NotImplementedError):
        Cut("top", (0, 0, 100, 100), 0, (50, 50)).apply(arr)


def test_key_of_crop_is_the_rect():
    assert Cut("", (1, 2, 3, 4)).key() == (1, 2, 3, 4)
    assert Cut("", (1, 2, 3, 4), 90, (5, 6)).key() == ((1, 2, 3, 4), 90, (5, 6))


def test_of():
    assert Cut.of((1, 2, 3, 4), "top") == Cut("top", (1, 2, 3, 4))
    c = Cut("left", (1, 2, 3, 4))
    assert Cut.of(c) is c
    assert Cut.of([1, 2, 3, 4]).crop == (1, 2, 3, 4)


def test_from_placement():
    p = layout.Placement("house_h", (518, 255, 1319, 814), 90, (812, 10, 298, 510))
    c = Cut.from_placement("top", p)
    assert c == Cut("top", (518, 255, 1319, 814), 90, (298, 510))
    assert c.size == (298, 510)


def test_feed_single_path_is_the_path():
    f = as_feed(Path("/v/x.mp4"))
    assert isinstance(f, Feed)
    assert f.kind == "youtube"
    assert set(f.paths) == set(VIEWS)
    assert os.fspath(f) == str(f) == "/v/x.mp4"
    assert f.path_for("left") == Path("/v/x.mp4")
    assert f.path_for("") == Path("/v/x.mp4")
    assert as_feed(f) is f
    assert as_feed("/v/x.mp4") == f


def test_feed_on_moves_views():
    f = as_feed(Path("/v/x.mp4"))
    g = f.on(("top", "bottom"), "/p/x.strip.mp4")
    assert g.path_for("top") == Path("/p/x.strip.mp4")
    assert g.path_for("bottom") == Path("/p/x.strip.mp4")
    assert g.path_for("left") == Path("/v/x.mp4")
    assert g.single() is None
    assert f.single() == Path("/v/x.mp4")  # the original is untouched
    with pytest.raises(TypeError, match="x.strip.mp4"):
        os.fspath(g)
    with pytest.raises(ValueError):
        g.path_for("")


def test_on_rejects_an_unknown_view():
    with pytest.raises(ValueError, match="nope"):
        as_feed("/v/x.mp4").on(("nope",), "/p/y.mp4")


def test_feed_is_hashable_and_compares_by_files():
    a, b = as_feed("/v/x.mp4"), as_feed(Path("/v/x.mp4"))
    assert a == b and hash(a) == hash(b)
    assert a != a.on(("top",), "/p/y.mp4")


class TestWholeFrameReads:
    """The wall board is read whole from the file its view is in, and the
    profile's panels from the file they share."""

    def feed(self):
        return as_feed("/v/x.mp4").on(("top", "bottom"), "/p/x.strip.mp4")

    def test_the_board_is_read_from_the_boards_file(self, monkeypatch):
        from curling_score.game import boardsplit, scoreboard
        from curling_score.ingest import frames as F

        asked = []
        monkeypatch.setattr(F, "keyframe_sweep",
                            lambda path, **kw: asked.append(path) or iter(()))
        boardsplit.board_states(self.feed(), 0.0, 10.0)
        assert scoreboard.read_cards_at(self.feed(), 100.0) is None
        assert asked == [Path("/v/x.mp4")] * 2

    def test_a_bare_path_is_read_as_it_was(self, monkeypatch):
        from curling_score.game import scoreboard
        from curling_score.ingest import frames as F

        asked = []
        monkeypatch.setattr(F, "keyframe_sweep",
                            lambda path, **kw: asked.append(path) or iter(()))
        scoreboard.read_cards_at("/v/x.mp4", 100.0)
        assert asked == [Path("/v/x.mp4")]

    def test_the_profile_reads_the_file_both_panels_share(self):
        from curling_score.game import profile

        assert profile.panels_file(self.feed()) == Path("/p/x.strip.mp4")
        assert profile.panels_file("/v/x.mp4") == Path("/v/x.mp4")
        split = as_feed("/v/x.mp4").on(("bottom",), "/v/y.mp4")
        with pytest.raises(NotImplementedError, match="phase 2"):
            profile.panels_file(split)
