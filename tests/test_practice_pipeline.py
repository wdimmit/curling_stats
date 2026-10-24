"""The watch's pipeline for real: one decode for both panels, then the passes."""

from types import SimpleNamespace

from curling_score.detect import sequence
from curling_score.practice import enrich as enrich_mod
from curling_score.practice.pipeline import PracticePipeline


def test_both_panels_come_from_one_decode(monkeypatch):
    asked = {}

    def spans(path, specs, end_s, detector):
        asked.update(path=path, specs=specs, end_s=end_s, detector=detector)
        return [["top frames"], ["bottom frames"]]

    monkeypatch.setattr(sequence, "detect_spans", spans)
    setups = {"top": "T", "bottom": "B"}
    got = PracticePipeline().detect("rec.ts", setups, 64.0, 94.0, "yolo")
    assert got == {"top": ["top frames"], "bottom": ["bottom frames"]}
    assert asked == {"path": "rec.ts", "specs": [("T", 64.0, 10.0), ("B", 64.0, 10.0)],
                     "end_s": 94.0, "detector": "yolo"}


def test_a_throw_is_measured_with_the_calibrations_panels_and_views(monkeypatch):
    seen = {}
    monkeypatch.setattr(enrich_mod, "enrich",
                        lambda video, setups, sideviews, models, **kw: seen.update(
                            video=video, setups=setups, sideviews=sideviews, **kw) or "shot")
    cal = SimpleNamespace(setups="S", sideviews="V")
    assert PracticePipeline().enrich("rec.ts", cal, "M", house="top", arrival=None,
                                     release="r", house_frames=[], throw_frames=[]) == "shot"
    assert (seen["video"], seen["setups"], seen["sideviews"], seen["house"]) == (
        "rec.ts", "S", "V", "top")


def test_a_saved_calibration_is_checked_against_eight_spread_keyframes(monkeypatch):
    from curling_score.cameras import calstore
    from curling_score.ingest import frames as F

    asked = []

    def spread(path, until_s, count=24, with_times=False, start_s=None):
        asked.append({"path": path, "start_s": start_s, "until_s": until_s, "count": count})
        return ["frame"] * count

    def check(cal, frames):
        asked.append({"cal": cal, "frames": frames})
        return calstore.HouseCheck.UNREADABLE

    monkeypatch.setattr(F, "spread_keyframes", spread)
    monkeypatch.setattr(calstore, "check_house", check)
    got = PracticePipeline().check_saved("rec.ts", 0.0, 10.0, "CAL")
    assert got is calstore.HouseCheck.UNREADABLE
    assert asked == [{"path": "rec.ts", "start_s": None, "until_s": 10.0, "count": 8},
                     {"cal": "CAL", "frames": ["frame"] * 8}]
    # A check made again reads the frames recorded since the last.
    PracticePipeline().check_saved("rec.ts", 10.0, 25.0, "CAL")
    assert asked[2] == {"path": "rec.ts", "start_s": 10.0, "until_s": 25.0, "count": 8}
