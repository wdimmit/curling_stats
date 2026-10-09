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
