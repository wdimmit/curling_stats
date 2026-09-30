"""The parts of ``analyze`` that can be checked without a video."""

import json
import logging
from types import SimpleNamespace

import pytest

from curling_score import analyze, timeline


class TestWriteIsAtomic:
    def _doc(self):
        return timeline.build_document(
            video_id="v", url="u", sheet=2, duration_s=10.0, calibration={},
            games=[], window=(100.0, 200.0), processing_version="p+m")

    def test_it_writes_the_document(self, tmp_path):
        path = analyze.write(self._doc(), tmp_path)
        assert path == tmp_path / "timeline.json"
        assert json.loads(path.read_text())["source"]["video_id"] == "v"

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        analyze.write(self._doc(), tmp_path)
        assert [p.name for p in tmp_path.iterdir()] == ["timeline.json"]

    def test_overrides_alongside_are_folded_in(self, tmp_path):
        doc = self._doc()
        doc["games"] = [{"index": 0, "ends": [{"number": 1, "shots": [
            {"number": 1, "color": "red"}]}]}]
        (tmp_path / "overrides.json").write_text(json.dumps(
            {"0.1.1": {"user_score": 4}}))
        analyze.write(doc, tmp_path)
        shot = json.loads((tmp_path / "timeline.json").read_text())[
            "games"][0]["ends"][0]["shots"][0]
        assert shot["user_score"] == 4 and shot["corrected"] is True


class TestDocumentCarriesItsProvenance:
    def test_window_and_version_are_recorded(self):
        doc = timeline.build_document(
            video_id="v", url="u", sheet=2, duration_s=10.0, calibration={},
            games=[], window=(100.0, 200.0), processing_version="2026.09.1+m-abc")
        assert doc["source"]["window"] == {"start_s": 100.0, "end_s": 200.0}
        assert doc["processing_version"] == "2026.09.1+m-abc"

    def test_a_whole_video_analysis_says_so(self):
        doc = timeline.build_document(
            video_id="v", url="u", sheet=None, duration_s=10.0, calibration={},
            games=[])
        assert doc["source"]["window"] == {"start_s": None, "end_s": None}
        assert doc["processing_version"] is None


class TestPhases:
    def test_the_stages_a_progress_bar_can_show_are_named(self):
        assert analyze.PHASES[0] == "download"
        assert "detect" in analyze.PHASES
        assert analyze.PHASES[-1] == "rules"

    def test_the_board_is_read_before_the_ends_are_built(self):
        # The board is the score, so it cannot be a stage that runs after
        # detection any more -- build_end has to be handed it.
        assert analyze.PHASES.index("scoreboard") < analyze.PHASES.index("detect")

    def test_the_status_page_shows_the_stages_in_the_order_they_run(self):
        # The service keeps its own copy of the order, for the ETA. Two copies
        # that disagree show the user a bar that runs backwards.
        pytest.importorskip("fastapi")
        from curling_score.service import api

        assert [p for p in api.PHASE_ORDER if p in analyze.PHASES] == \
               list(analyze.PHASES)


class TestDownloadChatter:
    def test_a_caller_watching_phases_silences_ytdlp_s_own_bar(self, tmp_path, monkeypatch):
        """Both at once makes a container log unreadable and says nothing extra."""
        seen = {}

        def fake_ensure(url, root=None, progress=True, **kw):
            seen["progress"] = progress
            raise RuntimeError("stop here: the download flag is all we need")

        from curling_score.ingest import cache
        monkeypatch.setattr(cache, "ensure_cached", fake_ensure)
        from curling_score import analyze as analyze_mod
        for on_phase, expected in ((lambda *a, **k: None, False), (None, True)):
            seen.clear()
            try:
                analyze_mod.analyze("VXU9xwmugRg", on_phase=on_phase,
                                    info=type("I", (), {
                                        "title": "t", "duration_s": 1.0, "sheet": 2,
                                        "video_id": "VXU9xwmugRg"})())
            except RuntimeError:
                pass
            assert seen["progress"] is expected


class TestAStreamThatJoinedAnEndLate:
    """The Tuesday Super League of 2026-09-29 started just before its streams
    came up: each sheet's first end began at the recording's first moment and
    showed only its last rocks. Only the recording's first end can have been
    joined late, and only if it begins where the recording does."""

    def test_the_first_end_opening_with_the_recording_was_joined_late(self):
        assert analyze.joined_late(None, 0.0) is True
        assert analyze.joined_late(None, 20.0) is True

    def test_a_first_end_the_stream_was_up_for_was_not(self):
        assert analyze.joined_late(None, 300.0) is False

    def test_no_later_end_was(self):
        assert analyze.joined_late(1815.0, 1940.0) is False

    def test_a_window_s_start_is_the_recording_s(self):
        assert analyze.joined_late(None, 3610.0, recording_start_s=3600.0) is True
        assert analyze.joined_late(None, 3610.0) is False


class TestTheRunUpStartsWhereThePreviousEndClosed:
    """The segmenter opens an end when a stone first rests in its house, so
    first rocks thrown before that -- through the house, or just early --
    were discarded as the previous end's run-up."""

    def test_the_first_end_of_a_game_gets_the_whole_game_gap(self):
        # Game 5's opening yellow ran through the house 67 s before the
        # segmenter saw the end begin; a half-minute would have missed it.
        assert analyze.run_up_from(None, 300.0) == 300.0 - 240.0

    def test_a_later_game_s_first_end_is_bounded_by_the_previous_game(self):
        # Between games the sheet is open and players slide practice rocks, so
        # the gap still bounds the reach even though a close is known.
        assert analyze.run_up_from(6435.0, 7590.0, crossed_games=True) == 7590.0 - 240.0
        assert analyze.run_up_from(7500.0, 7590.0, crossed_games=True) == 7500.0

    def test_a_later_end_reaches_back_to_the_previous_close(self):
        assert analyze.run_up_from(1815.0, 1940.0) == 1815.0   # game 3 end 3: yellow through at 1903

    def test_a_long_turnaround_still_reaches_the_previous_close(self):
        # A game gap is both houses empty; a turnaround is the clearing and
        # the walk down, with stones still on the sheet. Clamping this to the
        # game gap cut game 4RrNWSeNnMU end 4 off 19 s short of its first
        # rock: the close was 443.8 s back, the clamp allowed 240.
        assert analyze.run_up_from(1000.0, 2000.0) == 1000.0
        assert analyze.run_up_from(3131.2, 3575.0) == 3131.2
        # ...but a game break of the same length is still clamped.
        assert analyze.run_up_from(1000.0, 2000.0, crossed_games=True) == 2000.0 - 240.0

    def test_a_close_that_lands_inside_the_half_minute_still_gets_the_half_minute(self):
        assert analyze.run_up_from(985.0, 1000.0) == 1000.0 - 36.0

    def test_it_never_goes_before_the_start_of_the_video(self):
        assert analyze.run_up_from(None, 10.0) == 0.0
        assert analyze.run_up_from(None, 135.0) == 0.0


class TestTheBoardPassCannotAbortTheJob:
    """I5: by the time the board is read, the job has paid for the download,
    the proxy and the profile. Every other way the read can fail returns None
    and the game simply has no score; a decode or geometry exception has to
    degrade the same way rather than throw all of that away.
    """

    def _game(self):
        return SimpleNamespace(index=0, start_s=0.0, end_s=3000.0,
                               ends=[SimpleNamespace(number=1)])

    def test_an_exception_degrades_to_no_score_for_the_game(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("cv2 fell over")

        monkeypatch.setattr(analyze.sb, "read_game_board", boom)
        assert analyze.read_board("v.mp4", self._game(), progress=lambda m: None) is None

    def test_the_failure_is_logged_rather_than_swallowed(self, monkeypatch, caplog):
        def boom(*a, **k):
            raise RuntimeError("cv2 fell over")

        monkeypatch.setattr(analyze.sb, "read_game_board", boom)
        with caplog.at_level(logging.ERROR):
            analyze.read_board("v.mp4", self._game(), progress=lambda m: None)
        assert "cv2 fell over" in caplog.text

    def test_a_good_read_is_handed_straight_back(self, monkeypatch):
        monkeypatch.setattr(analyze.sb, "read_game_board",
                            lambda *a, **k: "the board")
        assert analyze.read_board("v.mp4", self._game()) == "the board"
