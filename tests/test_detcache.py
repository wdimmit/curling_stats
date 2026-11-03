import numpy as np
import pytest

from curling_score.detect import cache
from curling_score.detect import sequence
from curling_score.detect.rocks import Detection, PlayerBox, Seen
from curling_score.geometry.calibrate import PanelCalib


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CURLING_SCORE_CACHE", str(tmp_path))


def calib(px_per_m=70.0):
    return PanelCalib(center_px=(148.0, 300.0), px_per_m=px_per_m,
                      edge_erosion_px=1.0, residual_m=0.003, flipped=False)


def det(color="red", x=0.3, y=-0.4):
    return Detection(color=color, x_m=x, y_m=y, x_px=10.0, y_px=20.0,
                     area_px=140.0, confidence=0.87)


def seq():
    return [(0.0, [det(), det("yellow", 1.0, 2.0)]), (0.2, []), (0.4, [det()])]


def video(tmp_path, body=b"x" * 64):
    # Written once: rewriting it would change its mtime, and the key is
    # supposed to notice that.
    p = tmp_path / "v.mp4"
    if not p.exists():
        p.write_bytes(body)
    return p


def args(tmp_path, **over):
    base = dict(video_path=video(tmp_path), rect=(0, 0, 297, 514),
                calib=calib(), detector=None, start_s=0.0, end_s=10.0,
                step_s=0.2)
    base.update(over)
    return base


class TestRoundTrip:
    def test_a_stored_sequence_comes_back_unchanged(self, tmp_path):
        k = cache.key(**args(tmp_path))
        cache.save(k, seq())
        assert cache.load(k) == seq()

    def test_empty_frames_survive(self, tmp_path):
        k = cache.key(**args(tmp_path))
        cache.save(k, [(0.0, []), (0.2, [])])
        assert cache.load(k) == [(0.0, []), (0.2, [])]

    def test_a_miss_reads_as_absent(self, tmp_path):
        assert cache.load(cache.key(**args(tmp_path))) is None

    def test_it_computes_once_then_replays(self, tmp_path):
        k = cache.key(**args(tmp_path))
        calls = []

        def produce():
            calls.append(1)
            return seq()

        assert cache.detections(k, produce) == seq()
        assert cache.detections(k, produce) == seq()
        assert len(calls) == 1


class TestKeyCoversWhatChangesDetections:
    """A stale cache reads as a measurement, not as a bug, so the key has to
    cover every input that can move a box."""

    def test_a_different_time_range_is_a_different_key(self, tmp_path):
        assert cache.key(**args(tmp_path)) != cache.key(
            **args(tmp_path, end_s=20.0))

    def test_a_different_sample_step_is_a_different_key(self, tmp_path):
        assert cache.key(**args(tmp_path)) != cache.key(
            **args(tmp_path, step_s=0.1))

    def test_a_different_panel_is_a_different_key(self, tmp_path):
        assert cache.key(**args(tmp_path)) != cache.key(
            **args(tmp_path, rect=(0, 554, 297, 516)))

    def test_a_recalibrated_panel_is_a_different_key(self, tmp_path):
        assert cache.key(**args(tmp_path)) != cache.key(
            **args(tmp_path, calib=calib(px_per_m=71.0)))

    def test_a_reencoded_video_is_a_different_key(self, tmp_path):
        k = cache.key(**args(tmp_path))
        (tmp_path / "v.mp4").write_bytes(b"y" * 128)
        assert cache.key(**args(tmp_path)) != k

    def test_detection_source_is_part_of_the_key(self, tmp_path, monkeypatch):
        k = cache.key(**args(tmp_path))
        monkeypatch.setattr(cache, "_source_digest", lambda: "deadbeef")
        assert cache.key(**args(tmp_path)) != k

    def test_the_stored_format_version_is_part_of_the_key(self, tmp_path,
                                                          monkeypatch):
        k = cache.key(**args(tmp_path))
        monkeypatch.setattr(cache, "FORMAT_VERSION", cache.FORMAT_VERSION + 1)
        assert cache.key(**args(tmp_path)) != k

    def test_the_same_inputs_give_the_same_key(self, tmp_path):
        assert cache.key(**args(tmp_path)) == cache.key(**args(tmp_path))


class TestDetectorIdentity:
    class Fake:
        class _M:
            ckpt_path = None
            overrides: dict = {}

        def __init__(self, conf, imgsz=448, half=False, weights=None):
            self.conf, self.imgsz = conf, imgsz
            self.model = self._M()
            self.model.ckpt_path = weights
            self.model.overrides = {"half": half}

    def test_confidence_is_part_of_the_key(self, tmp_path):
        a = cache.key(**args(tmp_path, detector=self.Fake(0.3)))
        b = cache.key(**args(tmp_path, detector=self.Fake(0.05)))
        assert a != b

    def test_image_size_is_part_of_the_key(self, tmp_path):
        a = cache.key(**args(tmp_path, detector=self.Fake(0.3, imgsz=448)))
        b = cache.key(**args(tmp_path, detector=self.Fake(0.3, imgsz=640)))
        assert a != b

    def test_half_precision_is_part_of_the_key(self, tmp_path):
        a = cache.key(**args(tmp_path, detector=self.Fake(0.3, half=True)))
        b = cache.key(**args(tmp_path, detector=self.Fake(0.3, half=False)))
        assert a != b

    def test_retrained_weights_are_a_different_key(self, tmp_path):
        w = tmp_path / "best.pt"
        w.write_bytes(b"weights")
        a = cache.key(**args(tmp_path, detector=self.Fake(0.3, weights=w)))
        w.write_bytes(b"retrained weights")
        b = cache.key(**args(tmp_path, detector=self.Fake(0.3, weights=w)))
        assert a != b

    def test_the_classical_detector_differs_from_a_model(self, tmp_path):
        assert cache.key(**args(tmp_path, detector=None)) != cache.key(
            **args(tmp_path, detector=self.Fake(0.3)))


class TestWeightsAreKnownByContent:
    def test_touching_the_weights_file_does_not_change_the_key(self, tmp_path):
        import os

        from curling_score.detect import cache

        w = tmp_path / "m.pt"
        w.write_bytes(b"weights" * 1000)
        before = cache._weights_identity(w)
        os.utime(w, ns=(1, 1))
        assert cache._weights_identity(w) == before

    def test_different_weights_are_different(self, tmp_path):
        from curling_score.detect import cache

        a, b = tmp_path / "a.pt", tmp_path / "b.pt"
        a.write_bytes(b"one"); b.write_bytes(b"two")
        assert cache._weights_identity(a)[2] != cache._weights_identity(b)[2]


def box(x=0.5, conf=0.8):
    return PlayerBox(x0_px=10.0 + x, y0_px=20.0, x1_px=40.0 + x, y1_px=90.0,
                     x0_m=x - 0.2, y0_m=-1.1, x1_m=x + 0.2, y1_m=1.3,
                     confidence=conf)


class TestPlayers:
    def test_cache_round_trips_players(self, tmp_path):
        k = cache.key(**args(tmp_path))
        a, b = box(0.5), box(-1.25, 0.61)
        frames = [(0.0, Seen([det()], players=())),
                  (0.2, Seen([], players=(a,))),
                  (0.4, Seen([det("yellow", 1.0, 2.0)], players=(a, b)))]
        cache.save(k, frames)
        got = cache.load(k)
        assert got == frames
        assert all(isinstance(f, Seen) for _, f in got)
        assert [f.players for _, f in got] == [(), (a,), (a, b)]
        assert all(type(v) is float for _, f in got for p in f.players
                   for v in vars(p).values())

    def test_plain_lists_load_as_seen_without_players(self, tmp_path):
        k = cache.key(**args(tmp_path))
        cache.save(k, seq())
        got = cache.load(k)
        assert got == seq()
        assert all(isinstance(f, Seen) and f.players == () for _, f in got)

    def test_the_stored_format_is_version_2(self):
        assert cache.FORMAT_VERSION == 2

    def test_on_sheet_keeps_players(self):
        class Setup:
            def on_sheet(self, stones):
                return [d for d in stones if d.x_m < 1.0]

        p = box()
        out = sequence._on_sheet(
            Setup(), [(0.0, Seen([det(), det("yellow", 5.0, 0.0)], players=(p,)))])
        assert [len(f) for _, f in out] == [1]
        assert out[0][1].players == (p,)

    def test_on_sheet_from_the_classical_detector_has_no_players(self):
        class Setup:
            def on_sheet(self, stones):
                return list(stones)

        out = sequence._on_sheet(Setup(), [(0.0, [det()])])
        assert out[0][1] == [det()]
        assert out[0][1].players == ()

    def test_detect_spans_keeps_players(self, monkeypatch):
        p = box()

        class Setup:
            rect = (0, 0, 10, 10)
            calib = calib()

            def on_sheet(self, stones):
                return list(stones)

        class Detector:
            def find_stones_batch(self, imgs, calib):
                return [Seen([det()], players=(p,)) for _ in imgs]

        img = np.zeros((4, 4, 3), np.uint8)
        monkeypatch.setattr(sequence.F, "windows",
                            lambda path, wins, end_s: iter([(0, 0.0, img), (0, 0.2, img)]))
        (got,) = sequence.detect_spans("v.mp4", [(Setup(), 0.0, 5.0)], 1.0,
                                       Detector())
        assert len(got) == 2
        assert all(f.players == (p,) and len(f) == 1 for _, f in got)


class TestADetectorPerSpan:
    """overhead-4 reads the receiving panel and overhead-3 the thrower's,
    from one decode: a span may name its own detector."""

    class Setup:
        calib = calib()

        def __init__(self, rect):
            self.rect = rect

        def on_sheet(self, stones):
            return list(stones)

    class Tagger:
        def __init__(self, tag):
            self.tag, self.batches = tag, 0

        def find_stones_batch(self, imgs, calib):
            self.batches += 1
            return [Seen([det(self.tag)]) for _ in imgs]

    def windows(self, monkeypatch):
        img = np.zeros((4, 4, 3), np.uint8)
        monkeypatch.setattr(sequence.F, "windows", lambda path, wins, end_s: iter(
            [(0, 0.0, img), (1, 0.0, img), (0, 0.2, img), (1, 0.4, img)]))

    def test_detect_spans_uses_each_spans_detector(self, monkeypatch):
        self.windows(monkeypatch)
        house, stones = self.Tagger("house"), self.Tagger("stones")
        near, far = self.Setup((0, 0, 4, 4)), self.Setup((4, 0, 4, 4))
        got = sequence.detect_spans("v.mp4", [(near, 0.0, 5.0, house), (far, 0.0, 2.5)],
                                    1.0, stones)
        assert [[d.color for d in f] for _, f in got[0]] == [["house"], ["house"]]
        assert [[d.color for d in f] for _, f in got[1]] == [["stones"], ["stones"]]

    def test_spans_sharing_a_detector_share_its_threads(self, monkeypatch):
        """One model in memory and one pair of detection threads, as before:
        a detector given twice is not run twice over."""
        self.windows(monkeypatch)
        made = []
        real = sequence._DetectBehind

        class Counted(real):
            def __init__(self, detector):
                made.append(detector)
                super().__init__(detector)

        monkeypatch.setattr(sequence, "_DetectBehind", Counted)
        one = self.Tagger("stones")
        near, far = self.Setup((0, 0, 4, 4)), self.Setup((4, 0, 4, 4))
        got = sequence.detect_spans("v.mp4", [(near, 0.0, 5.0, one), (far, 0.0, 2.5)],
                                    1.0, one)
        assert made == [one]
        assert [len(s) for s in got] == [2, 2]

    def test_a_span_may_name_the_classical_detector(self, monkeypatch):
        self.windows(monkeypatch)
        monkeypatch.setattr("curling_score.detect.rocks.find_stones",
                            lambda img, calib: [det("classical")])
        near, far = self.Setup((0, 0, 4, 4)), self.Setup((4, 0, 4, 4))
        got = sequence.detect_spans("v.mp4", [(near, 0.0, 5.0, None), (far, 0.0, 2.5)],
                                    1.0, self.Tagger("stones"))
        assert {d.color for _, f in got[0] for d in f} == {"classical"}
        assert {d.color for _, f in got[1] for d in f} == {"stones"}
