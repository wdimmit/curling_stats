"""One live stream's session: calibrate from the first minutes, follow the
profile as it grows, build each end once it has settled, publish the games
one end at a time, and finish when the stream does.

The pipeline is faked -- these are the session's own decisions: when to
calibrate and recalibrate, which end to build next and with what run-up,
which board scores go on which end, and what each published document says.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from curling_score import analyze, timeline
from curling_score.game import format as format_mod, scoreboard as sb
from curling_score.game.profile import PanelSetup
from curling_score.game.segment import Sample
from curling_score.geometry.calibrate import CalibrationError, PanelCalib
from curling_score.live import session as live

NOW = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)


def panel(flipped, hog=True):
    return PanelSetup(rect=(0, 0, 297, 514),
                      calib=PanelCalib(center_px=(150.0, 160.0), px_per_m=76.0,
                                       edge_erosion_px=2.0, residual_m=0.001,
                                       flipped=flipped),
                      hog_line=(SimpleNamespace(to_json=lambda: {"fake": True})
                                if hog else None),
                      hog_line_error=None if hog else "no paint")


def side(tee, hog, lateral=True):
    return SimpleNamespace(rect=(0, 600, 800, 480), tee_row=tee, hog_row=hog,
                           has_lateral=lateral, centre_col=400.0,
                           lat_px_per_m_at_tee=140.0, centre_line=None)


def calibration(until_s, hog=True, tee=450.0, lateral=True):
    return live.Calibration(
        panels=None, setups={"top": panel(False, hog), "bottom": panel(True, hog)},
        sideviews={"left": side(tee, 530.0, lateral), "right": side(tee, 531.0, lateral)},
        until_s=until_s)


class Recording:
    def __init__(self):
        self.path = "rec.ts"
        self.head = 0.0
        self.finished = False

    def head_s(self):
        return self.head

    def ended(self):
        return self.finished


# Practice 0-600 s in the bottom house, then ends alternating every 900 s:
# top 600-1500, bottom 1500-2400, top 2400-3300, then an empty sheet.
def activity(t):
    if t < 600:
        return 0, 3
    if t >= 3300:
        return 0, 0
    k = int((t - 600) // 900)
    return (5, 0) if k % 2 == 0 else (0, 5)


class Pipeline:
    def __init__(self, calibrations=None):
        self.calibrations = list(calibrations or [])
        self.calibrated_at, self.built, self.boards_read = [], [], []
        self.board = None

    def calibrate(self, path, until_s):
        self.calibrated_at.append(until_s)
        got = self.calibrations.pop(0) if self.calibrations else calibration(until_s)
        if isinstance(got, Exception):
            raise got
        return got

    def samples(self, path, setups, from_s, until_s):
        out, t = [], (int(from_s // 5) + 1) * 5.0 if from_s is not None else 0.0
        while t <= until_s:
            top, bottom = activity(t)
            out.append(Sample(t=t, top_stones=top, bottom_stones=bottom))
            t += 5.0
        return out

    def build_end(self, ctx, game, end, prev_end_s):
        self.built.append({"game": game.index, "end": end.number,
                           "prev_end_s": prev_end_s, "setups": ctx.read_setups,
                           "use_cache": ctx.use_cache, "read_path": ctx.read_path})
        built = timeline.build_end(end.number, end.house, end.start_s, end.end_s, [],
                                   fmt=ctx.fmt)
        return built, end.end_s - 20.0

    def read_board(self, path, game):
        self.boards_read.append((game.index, game.end_s))
        return self.board

    def processing_version(self, sideviews):
        return "test-version"


def session(pipe, rec, published):
    return live.LiveSession(
        video_id="liveVid0001", url="https://www.youtube.com/watch?v=liveVid0001",
        recording=rec, fmt=format_mod.FOURS, pipeline=pipe, sheet=3,
        publish=published.append, progress=lambda m: None, now=lambda: NOW)


def run_until(s, rec, head, step=30.0):
    """Advance the recording to ``head`` and let the session do all it can."""
    while rec.head < head:
        rec.head = min(head, rec.head + step)
        while s.step():
            pass


class TestCalibration:
    def test_nothing_happens_before_fifteen_minutes_are_recorded(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        rec.head = 890.0
        assert s.step() is None
        assert pipe.calibrated_at == []

    def test_it_calibrates_from_what_has_been_recorded(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        rec.head = 905.0
        assert s.step() == "calibrated"
        assert pipe.calibrated_at == [905.0]

    def test_it_recalibrates_until_the_hog_lines_and_side_views_hold_still(self):
        pipe = Pipeline([calibration(900, hog=False), calibration(1800, tee=452.0),
                         calibration(2700, tee=454.0), calibration(3600, tee=455.0)])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 5000.0)
        # 900: no hog line. 1800: first good one. 2700: side views 2 px from
        # the last -- steady, so no more.
        assert pipe.calibrated_at == [900.0, 1800.0, 2700.0]

    def test_a_side_view_that_moved_keeps_it_recalibrating(self):
        pipe = Pipeline([calibration(900, tee=420.0), calibration(1800, tee=447.0),
                         calibration(2700, tee=448.0)])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 5000.0)
        assert pipe.calibrated_at == [900.0, 1800.0, 2700.0]

    def test_side_views_that_worked_are_not_given_up_for_worse_ones(self):
        """Sheet 3, 2026-09-27 at 19:31: the recalibration lost both side views
        where the one before had them, and every end after it had no splits.
        The newer panels are taken; the side views that worked are kept."""
        first = calibration(900)
        worse = live.Calibration(panels=None, setups=calibration(1800).setups,
                                 sideviews=None, until_s=1800)
        pipe = Pipeline([first, worse, calibration(2700), calibration(3600)])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 2000.0)
        assert s.calibration.setups is worse.setups
        assert s.calibration.sideviews is first.sideviews
        assert not s.steady          # a kept view proves nothing: go on trying
        run_until(s, rec, 2750.0)
        assert len(pipe.calibrated_at) == 3

    def test_a_side_view_without_lateral_is_not_given_up_for_none(self):
        first = calibration(900)
        first.sideviews["right"] = side(450.0, 531.0, lateral=False)
        worse = live.Calibration(panels=None, setups=calibration(1800).setups,
                                 sideviews=None, until_s=1800)
        pipe = Pipeline([first, worse])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 2000.0)
        assert s.calibration.sideviews is first.sideviews

    def test_better_side_views_replace_worse_ones(self):
        first = live.Calibration(panels=None, setups=calibration(900).setups,
                                 sideviews=None, until_s=900)
        better = calibration(1800)
        pipe = Pipeline([first, better])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 2000.0)
        assert s.calibration is better

    def test_each_end_is_built_with_the_newest_calibration(self):
        first, second = calibration(900, hog=False), calibration(1800)
        pipe, rec, pub = Pipeline([first, second]), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 2200.0)
        assert pipe.built and pipe.built[-1]["setups"] is second.setups

    def test_calibration_that_never_works_fails_the_session(self):
        pipe = Pipeline([CalibrationError("dark")] * 10)
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        with pytest.raises(live.LiveError):
            run_until(s, rec, 4000.0)


class TestEnds:
    def test_each_settled_end_is_built_once_in_order_off_the_growing_file(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 3300.0)
        # Practice, top, bottom have settled; the last top end is still open.
        assert [(b["game"], b["end"]) for b in pipe.built] == [(0, 1), (0, 2), (0, 3)]
        assert all(b["use_cache"] is False and b["read_path"] == "rec.ts"
                   for b in pipe.built)

    def test_the_run_up_starts_where_the_previous_end_closed(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 3300.0)
        prevs = [b["prev_end_s"] for b in pipe.built]
        assert prevs[0] is None
        assert prevs[1:] == [pytest.approx(600.0 - 20, abs=10),
                             pytest.approx(1500.0 - 20, abs=10)]

    def test_every_built_end_is_published_with_the_game_still_in_progress(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 3300.0)
        assert [len(d["games"][0]["ends"]) for d in pub] == [1, 2, 3]
        last = pub[-1]
        assert last["live"]["in_progress"] is True
        assert last["games"][0]["in_progress"] is True
        assert last["source"]["video_id"] == "liveVid0001"
        assert last["processing_version"] == "test-version"
        # Published when end 3 settled, once the next end had run five minutes.
        assert last["live"]["recorded_s"] == pytest.approx(2700.0)

    def test_the_next_end_due_is_the_oldest_settled_end_not_yet_built(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        rec.head = 905.0
        s.step()                       # calibrate only
        rec.head = 2000.0
        assert s.next_end_due() == pytest.approx(600.0, abs=10)


def board(*cards, n_ends=4):
    red = tuple(c for c in cards if c[0] == "red")
    yellow = tuple(c for c in cards if c[0] == "yellow")
    cb = sb.CardBoard(red=tuple(sb.Card(slot=c[1], end=c[2], confidence=1.0) for c in red),
                      yellow=tuple(sb.Card(slot=c[1], end=c[2], confidence=1.0) for c in yellow))
    got = sb.GameBoard(scores=sb.per_end_from_cards(cb, n_ends), read_at_s=0.0, reads=1,
                       board=cb)
    return analyze.board_block(got)


class TestBoard:
    def test_the_boards_latest_scores_go_on_every_published_end(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        pipe.board = board(("red", 1, 1))
        run_until(s, rec, 2250.0)            # ends 1 and 2 built
        pipe.board = board(("red", 1, 1), ("yellow", 2, 2), ("red", 3, 3))
        run_until(s, rec, 3300.0)            # end 3 built, board re-read
        ends = pub[-1]["games"][0]["ends"]
        assert [e["score"] for e in ends] == [{"red": 1, "yellow": 0},
                                              {"red": 0, "yellow": 2},
                                              {"red": 2, "yellow": 0}]

    def test_an_open_games_board_is_read_behind_the_recording(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 2250.0)
        index, read_to = pipe.boards_read[-1]
        assert index == 0
        assert read_to <= 2250.0 - live.BOARD_BEHIND_S


class TestTheStreamEnding:
    def test_the_rest_settles_and_the_final_document_is_published(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 3400.0)
        rec.finished = True
        while s.step():
            pass
        assert s.done
        assert [b["end"] for b in pipe.built] == [1, 2, 3, 4]
        final = pub[-1]
        assert final["live"]["in_progress"] is False
        assert final["games"][0]["in_progress"] is False
        assert s.step() is None              # nothing more to do

    def test_a_sheet_with_no_game_still_publishes_when_its_stream_ends(self):
        """Sheets 1 and 5 on 2026-09-27: streamed, calibrated, never played.
        The API completes a job only once its timeline is uploaded, so the
        empty stream must publish too, or its job is refused and comes back."""
        class Empty(Pipeline):
            def samples(self, path, setups, from_s, until_s):
                return [Sample(t=s.t, top_stones=0, bottom_stones=0)
                        for s in super().samples(path, setups, from_s, until_s)]

        pipe, rec, pub = Empty(), Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 3600.0)
        assert pub == [] and pipe.built == []
        rec.finished = True
        for _ in range(5):
            s.step()
        assert s.done
        assert pub[-1]["games"] == []
        assert pub[-1]["live"]["in_progress"] is False


class TestCalibrationGivesUp:
    def test_a_stream_that_ends_before_it_ever_calibrates_fails_after_a_try(self):
        pipe = Pipeline([CalibrationError("slate only")] * 50)
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        rec.head, rec.finished = 1200.0, True
        with pytest.raises(live.LiveError):
            for _ in range(10):
                s.step()
        assert len(pipe.calibrated_at) <= live.ENDED_CALIB_TRIES

    def test_a_stream_that_ended_with_nothing_recorded_fails(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        rec.finished = True
        with pytest.raises(live.LiveError):
            s.step()

    def test_once_it_works_it_stops_trying_to_improve_it_after_an_hour(self):
        # Hog paint never found: never "complete", so never steady -- but a
        # calibration that works is kept once an hour has passed.
        pipe = Pipeline([calibration(t, hog=False) for t in (900, 1800, 2700, 3600, 4500)])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 5400.0)
        assert pipe.calibrated_at == [900.0, 1800.0, 2700.0]

    def test_side_views_left_out_on_purpose_count_as_complete(self):
        def no_sides(t):
            c = calibration(t)
            c.sideviews, c.side_expected = None, False
            return c

        pipe = Pipeline([no_sides(900), no_sides(1800), no_sides(2700)])
        rec, pub = Recording(), []
        s = session(pipe, rec, pub)
        run_until(s, rec, 4000.0)
        assert pipe.calibrated_at == [900.0, 1800.0]



class TestNeedingACalibration:
    def test_it_needs_one_once_fifteen_minutes_are_recorded_and_until_it_has_one(self):
        pipe, rec, pub = Pipeline(), Recording(), []
        s = session(pipe, rec, pub)
        rec.head = 600.0
        assert not s.needs_calibration()
        rec.head = 905.0
        assert s.needs_calibration()
        s.step()
        assert not s.needs_calibration()          # recalibrating is not urgent


class TestABoardReadDuringPlay:
    def test_is_scored_against_the_ends_the_game_has_now_not_then(self):
        # Found comparing live with VOD on jgZ9wlxGYHM: the board read with
        # five ends played kept "every end posted" and its five-end total as
        # the game's final after nine ends -- the end-of-game read had failed,
        # so that earlier read was the one kept.
        class FullEnds(Pipeline):
            def build_end(self, ctx, game, end, prev_end_s):
                built, closed = super().build_end(ctx, game, end, prev_end_s)
                built["shots_expected"] = 0     # no practice signature here
                return built, closed

        pipe, rec, pub = FullEnds(), Recording(), []
        s = session(pipe, rec, pub)
        pipe.board = board(("red", 1, 1), n_ends=1)   # scored when one end was known
        run_until(s, rec, 1000.0)               # end 1 built, board read
        pipe.board = None                       # later reads fail
        rec.head = 3400.0
        rec.finished = True
        while s.step():
            pass
        game = pub[-1]["games"][0]
        assert len(game["ends"]) == 4
        assert game["ends"][0]["score"] == {"red": 1, "yellow": 0}
        assert game["scoreboard"]["unread_ends"] == [2, 3, 4]
        assert game["scoreboard"]["final"] is None
        assert game["final"] is None
