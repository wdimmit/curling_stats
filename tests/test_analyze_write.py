"""The parts of ``analyze`` that can be checked without a video."""

import json

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
        assert analyze.PHASES[-1] == "scoreboard"
