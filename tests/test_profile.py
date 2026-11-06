"""The activity profile's stone counts: the trained detector's when there is
one, since the colour thresholds also count people (game/profile.py)."""

import json
import pickle
from pathlib import Path

import numpy as np
import pytest

from curling_score import analyze
from curling_score.feeds import Cut
from curling_score.game import boardsplit, profile, segment
from curling_score.game.segment import EndSegment, GameSegment
from curling_score.geometry import layout
from tests.synth_hogline import TOP, plate_with_line


class Setup:
    """A panel setup cropping one half of a frame."""

    def __init__(self, x0, calib):
        self.x0, self.calib = x0, calib

    def crop(self, frame):
        return frame[:, self.x0:self.x0 + 4]

    def on_sheet(self, stones):
        return stones

    def in_play(self, stones):
        return stones


class Detector:
    """Finds as many stones in a panel as its first pixel's value, and keeps
    each call's calibrations."""

    def __init__(self):
        self.calls = []

    def find_stones_each(self, panels, calibs):
        self.calls.append(list(calibs))
        return [[object()] * int(p[0, 0, 0]) for p in panels]


def frame(top, bottom):
    out = np.zeros((4, 8, 3), np.uint8)
    out[:, :4], out[:, 4:] = top, bottom
    return out


SETUPS = {"top": Setup(0, "top-calib"), "bottom": Setup(4, "bottom-calib")}


class TestCountFrame:
    def test_the_detector_counts_both_houses_each_in_its_own_calibration(self, monkeypatch):
        monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: True)
        monkeypatch.setattr(profile.rocks, "find_stones",
                            lambda *a: (_ for _ in ()).throw(AssertionError("thresholds")))
        det = Detector()
        assert profile.count_frame(frame(3, 1), SETUPS, det) == (3, 1, True, True)
        assert det.calls == [["top-calib", "bottom-calib"]]

    def test_a_house_too_dark_to_play_is_not_shown_to_the_detector(self, monkeypatch):
        monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: panel[0, 0, 0] != 9)
        det = Detector()
        assert profile.count_frame(frame(9, 2), SETUPS, det) == (0, 2, False, True)
        assert det.calls == [["bottom-calib"]]

    def test_without_a_detector_the_thresholds_count(self, monkeypatch):
        monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: True)
        monkeypatch.setattr(profile.rocks, "find_stones",
                            lambda panel, calib: [object()] * (2 if calib == "top-calib" else 5))
        assert profile.count_frame(frame(0, 0), SETUPS) == (2, 5, True, True)

    def test_build_profile_counts_with_the_detector_it_is_given(self, monkeypatch):
        monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: True)
        sweep = [(0.0, frame(4, 0)), (5.0, frame(0, 2))]
        got = profile.build_profile(None, SETUPS, sweep=sweep, detector=Detector())
        assert [(s.t, s.top_stones, s.bottom_stones) for s in got] == [(0.0, 4, 0), (5.0, 0, 2)]


def _fixture():
    doc = json.loads((Path(__file__).parent / "fixtures" / "profiles"
                      / "lgBGFKnr4vA.json").read_text())

    def samples(key):
        return [segment.Sample(t=t, top_stones=top, bottom_stones=bot,
                               top_playable=bool(tp), bottom_playable=bool(bp))
                for t, top, bot, tp, bp in doc[key]]

    return samples("samples"), samples("samples_thresholds"), doc["board_states"]


# The chart's ends 1-5, before the fixture's window opens.
EARLIER = [("top", 160.0, 1110.0), ("bottom", 1115.0, 2015.0), ("top", 2050.0, 2995.0),
           ("bottom", 3000.0, 3840.0), ("top", 3875.0, 4760.0)]


def _night(samples):
    """The whole night's single game as the empty-sheet rule gives it: the
    chart's first five ends, then the fixture's."""
    (game,) = segment.segment_games(samples)
    ends = [EndSegment(number=i, house=h, start_s=a, end_s=b)
            for i, (h, a, b) in enumerate(EARLIER, start=1)]
    ends += [EndSegment(number=len(ends) + i, house=e.house, start_s=e.start_s, end_s=e.end_s)
             for i, e in enumerate(game.ends, start=1)]
    return GameSegment(index=0, start_s=ends[0].start_s, end_s=ends[-1].end_s, ends=ends)


class TestThursdayMensSheet1:
    """10/15: the 7 pm and 9 pm games, charted live as one fourteen-end game.
    The 7 pm game's last rock rested at 6533 s, and the club cleared the board
    with it; the 9 pm game's first rock was released at 6902 s."""

    def _split(self, samples):
        _, _, states = _fixture()
        reads = []

        def read(t0, t1):
            reads.append((t0, t1))
            return [(t, s) for t, s in states if t0 <= t <= t1]

        return boardsplit.split_games([_night(samples)], read), reads

    def test_on_the_detectors_counts_the_empty_sheet_ends_the_game(self):
        """Since `segment._drop_strays`: one keyframe of a "stone" in the
        bottom house at 6680 s, 115 s into 330 s of empty sheet, no longer
        breaks it up, and the empty-sheet rule parts the games where the
        board did."""
        samples, _, _ = _fixture()
        games = segment.segment_games(samples)
        assert [len(g.ends) for g in games] == [2, 3]
        assert games[0].end_s == pytest.approx(6540.0, abs=5)
        assert games[1].start_s == pytest.approx(6920.0, abs=5)

    def test_on_the_detectors_counts_the_board_parts_them_too(self, monkeypatch):
        """Without that, as before it: one game by the empty sheet, split by
        the board cleared across the changeover."""
        monkeypatch.setattr(segment, "STRAY_KEYFRAMES", 0)
        samples, _, _ = _fixture()
        games, reads = self._split(samples)
        assert [len(g.ends) for g in games] == [7, 3]
        # End 7 stops at its last rock, not 400 s later. Boundaries move a
        # keyframe with the hash seed (`segment._smooth`'s tie-break).
        assert games[0].end_s == pytest.approx(6540.0, abs=5)
        assert games[1].start_s == pytest.approx(6920.0, abs=5)
        ((t0, t1),) = reads
        assert (t0, t1) == (pytest.approx(6240.0, abs=5), pytest.approx(7520.0, abs=5))

    def test_on_the_colour_thresholds_it_did_not(self):
        _, thresholds, _ = _fixture()
        games, reads = self._split(thresholds)
        # People in the top house kept end 7 going to 6950 s, 65 s before
        # the next end: no gap the board decides.
        assert [len(g.ends) for g in games] == [10]
        assert games[0].ends[6].end_s == pytest.approx(6950.0, abs=5)
        assert all(t0 > 7000.0 for t0, _ in reads), reads

    def test_game_2_end_2_keeps_its_last_five_rocks(self):
        """Players walking through the far house cut it at 8320 s on the
        thresholds' counts; its rocks 12-16 came down to 8720 s. Since
        `segment._passing` the thresholds keep them too: what cut it was a
        keyframe or two of the far house while this one sat emptied."""
        samples, thresholds, _ = _fixture()
        by_counter = [[e for g in segment.segment_games(s) for e in g.ends][3]
                      for s in (samples, thresholds)]
        assert [e.house for e in by_counter] == ["top", "top"]
        assert [e.end_s for e in by_counter] == [pytest.approx(8695.0, abs=5),
                                                 pytest.approx(8765.0, abs=5)]


class TestSetupCut:
    def test_setup_cut_defaults_to_rect(self):
        s = profile.PanelSetup(rect=(10, 20, 300, 534), calib=TOP)
        assert s.cut == Cut("", (10, 20, 300, 534))
        assert s.transform is None and s.name == ""

    def test_a_transform_wins_over_the_rect(self):
        turned = Cut("top", (0, 0, 534, 300), rotate=90, size=(300, 534))
        s = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP, name="top",
                               transform=turned)
        assert s.cut is turned

    def test_a_transform_must_be_the_panels_own(self):
        turned = Cut("top", (0, 0, 534, 300), rotate=90, size=(300, 534))
        with pytest.raises(ValueError, match="transform is for 'top'"):
            profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP, name="bottom",
                               transform=turned)
        with pytest.raises(ValueError, match="transform is for 'top'"):
            profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP, transform=turned)

    def test_rect_must_be_the_transforms_output(self):
        turned = Cut("top", (0, 0, 534, 300), rotate=90, size=(300, 534))
        for rect in ((0, 0, 534, 300), (3, 0, 300, 534), (0, 0, 300, 535)):
            with pytest.raises(ValueError, match="not the transform's output"):
                profile.PanelSetup(rect=rect, calib=TOP, name="top", transform=turned)

    def test_crop_goes_through_the_cut_when_there_is_a_transform(self):
        frame = np.zeros((600, 800, 3), np.uint8)
        # a crop-only transform is the slice, from the cut's own crop
        flat = Cut("top", (10, 20, 300, 534))
        s = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP, name="top",
                               transform=flat)
        assert s.crop(frame).shape == (534, 300, 3)
        assert s.crop(frame).flags.c_contiguous
        # a turned one is not made in numpy: no silent slice of the wrong pixels
        turned = Cut("top", (0, 0, 534, 300), rotate=90, size=(300, 534))
        t = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP, name="top",
                               transform=turned)
        with pytest.raises(NotImplementedError, match="made by frames"):
            t.crop(frame)

    def test_crop_without_a_transform_is_the_slice(self):
        frame = np.arange(600 * 800 * 3, dtype=np.uint8).reshape(600, 800, 3)
        s = profile.PanelSetup(rect=(10, 20, 30, 40), calib=TOP)
        assert np.array_equal(s.crop(frame), frame[20:60, 10:40])

    def test_calibrate_panels_names_setups(self, monkeypatch):
        monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
        monkeypatch.setattr(profile.calibrate, "solve", lambda median, side: TOP)
        plate = plate_with_line()
        h, w = plate.shape[:2]
        both = np.concatenate([plate, plate], axis=1)
        setups = profile.calibrate_panels(
            [both.copy() for _ in range(3)],
            layout.PanelLayout(top=(0, 0, w, h), bottom=(w, 0, w, h)))
        assert {n: s.name for n, s in setups.items()} == {"top": "top", "bottom": "bottom"}
        assert setups["bottom"].cut == Cut("bottom", (w, 0, w, h))

    def test_old_pickle_has_a_cut(self):
        """A setup pickled before name/transform existed has neither in its
        __dict__; the class defaults stand in."""
        s = profile.PanelSetup(rect=(1, 2, 3, 4), calib=TOP, name="top")
        state = dict(s.__dict__)
        del state["name"], state["transform"]
        old = profile.PanelSetup.__new__(profile.PanelSetup)
        object.__setattr__(old, "__dict__", state)
        loaded = pickle.loads(pickle.dumps(old))
        assert "name" not in loaded.__dict__
        assert loaded.cut == Cut("", (1, 2, 3, 4))

    def test_proxy_setups_keep_the_name_and_follow_the_moved_rect(self):
        s = profile.PanelSetup(rect=(100, 0, 300, 534), calib=TOP, name="top")
        moved = analyze._proxy_setups({"top": s}, (100, 0, 300, 1060))["top"]
        assert moved.name == "top" and moved.transform is None
        assert moved.cut == Cut("top", tuple(moved.rect))
        assert moved.rect != s.rect


# --- a camera feed: each house in its own file ----------------------------------------------

class ViewSetup(Setup):
    """A panel setup on its own view, as a camera feed's is: a turned, scaled
    cut, which -- like PanelSetup's -- cannot be taken from a decoded picture.
    The sweep has already cut the view; the picture is the panel."""

    def crop(self, frame):
        raise NotImplementedError("a turned or scaled cut is made by frames()")


def view(n):
    return np.full((4, 4, 3), n, np.uint8)


def fake_sweeps(monkeypatch, times):
    """keyframe_sweep of each house view: ``times[name]`` are its keyframes,
    each picture holding its own time's whole seconds."""
    from curling_score.ingest import frames as F

    asked = []

    def sweep(path, decode=True, start_s=None, end_s=None, cut=None):
        asked.append((cut.name, start_s, end_s))
        for t in times[cut.name]:
            if (start_s is None or t >= start_s) and (end_s is None or t <= end_s):
                yield t, view(int(t))

    monkeypatch.setattr(F, "keyframe_sweep", sweep)
    return asked


class TestACameraFeedsProfile:
    def test_each_top_keyframe_is_paired_with_the_nearest_bottom_one(self, monkeypatch,
                                                                    tmp_path):
        from curling_score.feeds.cameras import camera_feed

        asked = fake_sweeps(monkeypatch, {"top": [0.0, 2.0, 4.0, 10.0],
                                          "bottom": [0.7, 1.9, 4.2, 4.9]})
        got = [(t, int(v["top"][0, 0, 0]), int(v["bottom"][0, 0, 0]))
               for t, v in profile.sweep(camera_feed(tmp_path, 3), start_s=0.0, end_s=20.0)]
        # 10.0 has no bottom keyframe within PAIR_S: no sample there.
        assert got == [(0.0, 0, 0), (2.0, 2, 1), (4.0, 4, 4)]
        assert sorted(a[0] for a in asked) == ["bottom", "top"]
        assert all(a[1:] == (0.0, 20.0 + profile.PAIR_S) or a[0] == "top" for a in asked)

    def test_its_profile_counts_each_house_from_its_own_picture(self, monkeypatch, tmp_path):
        from curling_score.feeds.cameras import camera_feed

        monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: True)
        fake_sweeps(monkeypatch, {"top": [0.0, 2.0], "bottom": [0.1, 2.1]})
        setups = {"top": ViewSetup(0, "top-calib"), "bottom": ViewSetup(0, "bottom-calib")}
        det = Detector()
        got = profile.build_profile(camera_feed(tmp_path, 3), setups, detector=det)
        assert [(s.t, s.top_stones, s.bottom_stones) for s in got] == [(0.0, 0, 0), (2.0, 2, 2)]
        assert det.calls[0] == ["top-calib", "bottom-calib"]

    def test_a_single_files_sweep_is_the_files_own(self, monkeypatch, tmp_path):
        from curling_score.ingest import frames as F

        monkeypatch.setattr(F, "keyframe_sweep",
                            lambda path, **kw: iter([(kw.get("start_s"), str(path))]))
        assert list(profile.sweep(tmp_path / "v.mp4", start_s=5.0)) == \
            [(5.0, str(tmp_path / "v.mp4"))]
