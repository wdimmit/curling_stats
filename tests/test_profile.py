"""The activity profile's stone counts: the trained detector's when there is
one, since the colour thresholds also count people (game/profile.py)."""

import json
from pathlib import Path

import numpy as np
import pytest

from curling_score.game import boardsplit, profile, segment
from curling_score.game.segment import EndSegment, GameSegment


class Setup:
    """A panel setup cropping one half of a frame."""

    def __init__(self, x0, calib):
        self.x0, self.calib = x0, calib

    def crop(self, frame):
        return frame[:, self.x0:self.x0 + 4]

    def on_sheet(self, stones):
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

    def test_on_the_detectors_counts_the_night_splits_into_its_two_games(self):
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
        by_counter = [segment.segment_games(s)[0].ends[3] for s in (samples, thresholds)]
        assert [e.house for e in by_counter] == ["top", "top"]
        assert [e.end_s for e in by_counter] == [pytest.approx(8695.0, abs=5),
                                                 pytest.approx(8765.0, abs=5)]
