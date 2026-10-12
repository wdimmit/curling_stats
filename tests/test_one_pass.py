"""One decode for both panels of a live end.

A live end reads the full-resolution recording, and decoding it dominates:
building an end twice over the same window -- the house played to at 10 fps,
the thrower's at 5 fps -- cost ~41 s a pass, most of the gap between ~160 s a
live end and ~100 s a recorded one (whose two passes read the small strip
proxy instead). One decode, cropping both panels from each frame, has to
pick exactly the frames two passes would.
"""

import shutil
import subprocess

import numpy as np
import pytest

from curling_score import analyze
from curling_score.detect import sequence
from curling_score.game import format as format_mod
from curling_score.game.profile import PanelSetup
from curling_score.game.segment import EndSegment, GameSegment
from curling_score.geometry.calibrate import PanelCalib
from curling_score.ingest import frames as F

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

A, B = (0, 0, 160, 240), (160, 0, 160, 240)


@pytest.fixture(scope="module", params=["mp4", "ts"])
def clip(request, tmp_path_factory):
    d = tmp_path_factory.mktemp("onepass")
    mp4 = d / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30", "-t", "30", "-g", "150",
         "-keyint_min", "150", "-sc_threshold", "0", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(mp4)], check=True)
    if request.param == "mp4":
        return mp4
    ts = d / "clip.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
                    "-c", "copy", "-f", "mpegts", str(ts)], check=True)
    return ts


def test_one_decode_gives_each_panel_the_frames_its_own_pass_would(clip):
    specs = [(9.0, 10.0, A), (11.3, 5.0, B)]
    got = {0: [], 1: []}
    for i, t, img in F.windows(clip, specs, 20.0):
        got[i].append((t, img))
    for i, (start, fps, crop) in enumerate(specs):
        want = list(F.window(clip, start, 20.0, fps, crop=crop))
        assert [t for t, _ in got[i]] == pytest.approx([t for t, _ in want], abs=1e-6)
        assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(got[i], want))


def panel(rect):
    return PanelSetup(rect=rect, calib=PanelCalib(center_px=(80.0, 120.0), px_per_m=40.0,
                                                  edge_erosion_px=2.0, residual_m=0.001,
                                                  flipped=False))


def test_detecting_on_both_at_once_is_detecting_on_each(clip):
    near, far = panel(A), panel(B)
    both = sequence.detect_spans(clip, [(near, 9.0, 10.0), (far, 11.3, 5.0)], 20.0)
    one = sequence.detect_span(clip, near, 9.0, 20.0, 10.0, use_cache=False)
    two = sequence.detect_span(clip, far, 11.3, 20.0, 5.0, use_cache=False)
    assert [t for t, _ in both[0]] == [t for t, _ in one]
    assert [t for t, _ in both[1]] == [t for t, _ in two]
    assert [len(d) for _, d in both[0]] == [len(d) for _, d in one]


def test_a_live_end_decodes_its_window_once(monkeypatch):
    calls = []

    def spans(path, specs, end_s, detector=None):
        calls.append(("spans", path, [(s.rect, start, fps) for s, start, fps in specs], end_s))
        return [[] for _ in specs]

    monkeypatch.setattr(analyze.sequence, "detect_spans", spans)
    monkeypatch.setattr(analyze.sequence, "detect_end",
                        lambda *a, **k: calls.append("end") or [])
    monkeypatch.setattr(analyze.sequence, "detect_span",
                        lambda *a, **k: calls.append("span") or [])
    top, bottom = panel((0, 0, 297, 514)), panel((0, 540, 297, 516))
    ctx = analyze.EndContext(path="rec.ts", read_path="rec.ts",
                             read_setups={"top": top, "bottom": bottom}, sideviews=None,
                             detector=None, broom_model=None, line_model=None,
                             fmt=format_mod.FOURS, use_cache=False, one_pass=True)
    game = GameSegment(index=0, start_s=1000.0, end_s=1900.0,
                       ends=[EndSegment(number=1, house="top", start_s=1000.0, end_s=1900.0)])
    analyze.build_one_end(ctx, game, game.ends[0], prev_end_s=None, board_score=None)
    (call,) = calls
    from curling_score.detect.delivery import REQUIRED_LOOKBACK_S

    from_s = analyze.run_up_from(None, 1000.0, crossed_games=True)
    assert call == ("spans", "rec.ts",
                    [(top.rect, min(1000.0 - REQUIRED_LOOKBACK_S, from_s), analyze.SHOT_FPS),
                     (bottom.rect, from_s, analyze.release.RELEASE_FPS)], 1900.0)


class FakeDetector:
    """One 'stone' per panel -- its mean pixel -- and where each batch ran."""

    def __init__(self, fail_on=None):
        self.threads, self.calls, self.fail_on = set(), 0, fail_on

    def find_stones_batch(self, panels, calib):
        import threading

        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("the GPU went away")
        self.threads.add(threading.current_thread().name)
        return [[float(p.mean())] for p in panels]


def serially(clip, setup, start, fps, end):
    return [(t, [float(img.mean())]) for t, img in F.window(clip, start, end, fps, crop=setup.rect)]


class TestDetectingBehindTheDecode:
    def test_both_panels_get_what_a_serial_loop_gave(self, clip, monkeypatch):
        monkeypatch.setattr(sequence, "BATCH", 16)
        near, far = panel(A), panel(B)
        det = FakeDetector()
        both = sequence.detect_spans(clip, [(near, 9.0, 10.0), (far, 11.3, 5.0)], 20.0, det)
        assert both[0] == serially(clip, near, 9.0, 10.0, 20.0)
        assert both[1] == serially(clip, far, 11.3, 5.0, 20.0)
        assert det.threads and all(n.startswith("detect") for n in det.threads)

    def test_one_panel_too(self, clip, monkeypatch):
        monkeypatch.setattr(sequence, "BATCH", 16)
        near = panel(A)
        got = sequence.detect_span(clip, near, 9.0, 20.0, 10.0, FakeDetector(), use_cache=False)
        assert got == serially(clip, near, 9.0, 10.0, 20.0)

    def test_the_decode_runs_at_most_one_batch_ahead(self, clip, monkeypatch):
        monkeypatch.setattr(sequence, "BATCH", 8)
        seen = []
        collect = sequence._DetectBehind._collect

        def watch(self, keep):
            seen.append(len(self.pending))
            return collect(self, keep)

        monkeypatch.setattr(sequence._DetectBehind, "_collect", watch)
        sequence.detect_spans(clip, [(panel(A), 9.0, 10.0)], 20.0, FakeDetector())
        assert max(seen) <= 2

    def test_a_detector_that_fails_fails_the_caller(self, clip, monkeypatch):
        monkeypatch.setattr(sequence, "BATCH", 8)
        with pytest.raises(RuntimeError, match="GPU went away"):
            sequence.detect_spans(clip, [(panel(A), 9.0, 10.0)], 20.0, FakeDetector(fail_on=2))


def test_the_kept_converter_gives_what_to_ndarray_gives(clip):
    import av

    to_bgr = F._converter()
    with av.open(str(clip)) as container:
        for i, frame in enumerate(container.decode(video=0)):
            assert np.array_equal(to_bgr(frame), frame.to_ndarray(format="bgr24"))
            if i >= 40:
                break


class TwinnedDetector(FakeDetector):
    """A fake whose twin is another fake, as `YoloDetector.twin` gives one."""

    def __init__(self, fail_on=None):
        super().__init__(fail_on)
        self.made = []

    def twin(self):
        if not self.made:
            self.made.append(FakeDetector())
        return self.made[0]


class TestTwoDetectionThreads:
    def test_both_panels_still_get_what_a_serial_loop_gave(self, clip, monkeypatch):
        monkeypatch.setattr(sequence, "BATCH", 8)
        near, far = panel(A), panel(B)
        det = TwinnedDetector()
        both = sequence.detect_spans(clip, [(near, 9.0, 10.0), (far, 11.3, 5.0)], 20.0, det)
        assert both[0] == serially(clip, near, 9.0, 10.0, 20.0)
        assert both[1] == serially(clip, far, 11.3, 5.0, 20.0)
        assert det.calls and det.made[0].calls          # both detectors were used

    def test_at_most_a_batch_a_thread_waits(self, clip, monkeypatch):
        monkeypatch.setattr(sequence, "BATCH", 4)
        seen = []
        collect = sequence._DetectBehind._collect

        def watch(self, keep):
            seen.append(len(self.pending))
            return collect(self, keep)

        monkeypatch.setattr(sequence._DetectBehind, "_collect", watch)
        sequence.detect_spans(clip, [(panel(A), 9.0, 10.0)], 20.0, TwinnedDetector())
        assert max(seen) <= 2 * sequence.DETECT_THREADS - 1 + 1
