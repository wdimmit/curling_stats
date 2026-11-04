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


def test_key_of_a_turned_or_scaled_cut_is_versioned():
    from curling_score.feeds.cut import CUT_IMPL

    assert Cut("", (1, 2, 3, 4), 90, (5, 6)).key() == ((1, 2, 3, 4), 90, (5, 6), CUT_IMPL)
    assert Cut("", (1, 2, 3, 4), 0, (5, 6)).key() == ((1, 2, 3, 4), 0, (5, 6), CUT_IMPL)
    assert Cut("", (1, 2, 3, 4), -90).key() == ((1, 2, 3, 4), -90, None, CUT_IMPL)


def test_apply_equals_steps_at_even_offsets(tmp_path):
    """``apply`` slices BGR; ``steps`` crops in yuv420p. The same pixels where
    chroma pairs line up (even x, y); ``steps`` is the reference."""
    import shutil
    import subprocess

    from curling_score.ingest import frames

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=160x120:rate=10", "-t", "1", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", str(path)], check=True)
    cut = Cut("left", (12, 8, 80, 60))
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vf", cut.steps(), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        check=True, capture_output=True).stdout
    want = np.frombuffer(raw, np.uint8).reshape(60, 80, 3)
    _, got = next(iter(frames.window(as_feed(path), 0.0, 0.0, 10.0, crop=cut)))
    assert np.array_equal(got, want)


def test_of():
    assert Cut.of((1, 2, 3, 4), "top") == Cut("top", (1, 2, 3, 4))
    c = Cut("left", (1, 2, 3, 4))
    assert Cut.of(c) is c
    assert Cut.of([1, 2, 3, 4]).crop == (1, 2, 3, 4)
    assert Cut.of(c, "left") is c
    assert Cut.of(c, "") is c


def test_of_a_cut_under_another_name_raises():
    with pytest.raises(ValueError, match="'left', not 'right'"):
        Cut.of(Cut("left", (1, 2, 3, 4)), "right")


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


# --- phase 2: the cameras' own files ------------------------------------------

from curling_score.feeds import cameras  # noqa: E402


def test_camera_feed_puts_each_view_in_its_cameras_file():
    feed = cameras.camera_feed(Path("d"), 2)
    assert feed.kind == "cameras" and feed.sheet == 2
    assert feed.path_for("top") == Path("d/house_h.ts")
    assert feed.path_for("bottom") == Path("d/house_a.ts")
    assert feed.path_for("left") == Path("d/long_a.ts")
    assert feed.path_for("right") == Path("d/long_h.ts")
    assert feed.path_for("board") == Path("d/long_h.ts")


@pytest.mark.parametrize("sheet", [1, 2, 3, 4, 5])
def test_view_roles_match_every_sheets_layout(sheet):
    sl = layout.load(sheet)
    out = {p.role: p.out for p in sl.placements}
    assert out["house_h"][1] < out["house_a"][1]
    assert out["long_a"][0] < out["long_h"][0]


@pytest.mark.parametrize("sheet", [1, 2, 3, 4, 5])
def test_long_views_are_crops_and_houses_are_turned(sheet):
    sl = layout.load(sheet)
    cuts = cameras.view_cuts(sl)
    assert set(cuts) == {"top", "bottom", "left", "right"}
    assert cuts["left"].is_crop and cuts["right"].is_crop
    for name in ("top", "bottom"):
        p = cameras.placement_for(sl, name)
        assert cuts[name].rotate == 90
        assert cuts[name].out_size == tuple(p.out[2:])
        assert cuts[name].name == name


def test_a_scaled_long_view_is_not_a_crop():
    p = layout.Placement("long_a", (0, 0, 800, 1080), 0, (0, 0, 400, 540))
    cut = cameras.view_cut("left", p)
    assert not cut.is_crop and cut.out_size == (400, 540)


def test_feed_str_names_its_files_and_never_raises():
    feed = cameras.camera_feed(Path("d"), 2)
    assert "cameras feed" in str(feed) and "house_h.ts" in str(feed)
    with pytest.raises(TypeError):
        os.fspath(feed)


def test_path_for_an_unknown_view_is_a_value_error():
    with pytest.raises(ValueError, match="no such view: tpo"):
        cameras.camera_feed(Path("d"), 2).path_for("tpo")


def test_feed_paths_are_read_only():
    feed = as_feed("v.mp4")
    with pytest.raises(TypeError):
        feed.paths["top"] = Path("x")


def test_cut_coerces_its_crop():
    c = Cut("x", [1, 2, 3, 4], 0, [5, 6])
    assert c.crop == (1, 2, 3, 4) and c.size == (5, 6)
    assert hash(c) == hash(Cut("x", (1, 2, 3, 4), 0, (5, 6)))


# --- phase 2: calibrating a camera feed ------------------------------------------

def _outs(sheet=2):
    sl = layout.load(sheet)
    return sl, {n: cameras.placement_for(sl, n).out for n in cameras.CUT_VIEWS}


def test_compose_places_each_view_at_its_out():
    sl, outs = _outs()
    colours = {"top": (255, 0, 0), "bottom": (0, 255, 0), "left": (0, 0, 255),
               "right": (255, 255, 0)}
    views = {n: np.full((outs[n][3], outs[n][2], 3), c, np.uint8) for n, c in colours.items()}
    frame = cameras.compose(views, sl)
    assert frame.shape == (1080, 1920, 3)
    grey = int(sl.background, 16) & 0xFF
    covered = np.zeros((1080, 1920), bool)
    for n, (x, y, w, h) in outs.items():
        assert (frame[y:y + h, x:x + w] == colours[n]).all()
        covered[y:y + h, x:x + w] = True
    assert (frame[~covered] == grey).all()


def test_compose_refuses_a_wrong_sized_view():
    sl, outs = _outs()
    views = {n: np.zeros((outs[n][3], outs[n][2], 3), np.uint8) for n in outs}
    views["top"] = views["top"][:-1]
    with pytest.raises(ValueError, match="top"):
        cameras.compose(views, sl)


def _setup(rect):
    from types import SimpleNamespace

    from curling_score.game import profile

    return profile.PanelSetup(rect=rect, calib=SimpleNamespace(px_per_m=100.0))


def _side(rect, name=""):
    from curling_score.geometry.sideview import SideView

    return SideView(rect=rect, tee_row=430.0, hog_row=520.0, name=name)


def test_localize_moves_setups_into_their_views():
    sl, outs = _outs()
    setups = {n: _setup(outs[n]) for n in ("top", "bottom")}
    sides = {n: _side(outs[n], n) for n in ("left", "right")}
    got_setups, got_sides = cameras.localize(setups, sides, sl)
    cuts = cameras.view_cuts(sl)
    top = got_setups["top"]
    assert top.rect == (0, 0, 297, 514) and top.transform == cuts["top"] and top.name == "top"
    assert top.calib is setups["top"].calib
    left = got_sides["left"]
    assert left.rect == (0, 0, 800, 1080) and left.transform.is_crop
    assert left.tee_row == 430.0 and left.cut == cuts["left"]
    assert cameras.localize(setups, None, sl)[1] is None


def test_localize_refuses_a_setup_not_at_its_out():
    sl, outs = _outs()
    x, y, w, h = outs["top"]
    setups = {"top": _setup((x + 1, y, w, h)), "bottom": _setup(outs["bottom"])}
    with pytest.raises(ValueError, match="top"):
        cameras.localize(setups, None, sl)


def test_view_keyframes_cuts_each_view_and_trims_to_the_shortest(monkeypatch):
    from curling_score.ingest import frames as F

    seen = []

    def spread(path, until_s, count=24, with_times=False, start_s=None, cut=None):
        seen.append((cut.name, until_s, count, start_s))
        n = 3 if cut.name == "bottom" else 4
        return [np.zeros((cut.out_size[1], cut.out_size[0], 3), np.uint8)] * n

    monkeypatch.setattr(F, "spread_keyframes", spread)
    sl, _ = _outs()
    feed = cameras.camera_feed(Path("d"), 2)
    got = cameras.view_keyframes(feed, cameras.view_cuts(sl), 60.0, 8, start_s=5.0)
    assert {n: len(v) for n, v in got.items()} == {n: 3 for n in cameras.CUT_VIEWS}
    assert sorted(seen) == sorted((n, 60.0, 8, 5.0) for n in cameras.CUT_VIEWS)
