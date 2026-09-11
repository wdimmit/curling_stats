import numpy as np
import pytest

from curling_score.ingest import proxy


class TestProxyPath:
    def test_names_the_proxy_after_the_video_and_region(self, tmp_path):
        p = proxy.proxy_path("VXU9xwmugRg", (810, 10, 297, 1060), root=tmp_path)
        assert p.parent == tmp_path / "proxies"
        assert "VXU9xwmugRg" in p.name
        assert p.suffix == ".mp4"

    def test_a_different_region_gets_a_different_file(self, tmp_path):
        a = proxy.proxy_path("VID", (810, 10, 297, 1060), root=tmp_path)
        b = proxy.proxy_path("VID", (813, 10, 294, 1060), root=tmp_path)
        assert a != b

    def test_is_cached_is_false_when_absent(self, tmp_path):
        assert proxy.is_cached("VID", (0, 0, 10, 10), root=tmp_path) is False


class TestTranslateRect:
    def test_panel_rects_move_into_proxy_coordinates(self):
        strip = (810, 10, 297, 1060)
        top = (810, 10, 297, 514)
        assert proxy.translate(top, strip) == (0, 0, 297, 514)

    def test_a_lower_panel_keeps_its_offset_within_the_strip(self):
        strip = (810, 10, 297, 1060)
        bottom = (810, 554, 297, 515)
        assert proxy.translate(bottom, strip) == (0, 544, 297, 515)

    def test_refuses_a_rect_outside_the_strip(self):
        with pytest.raises(ValueError):
            proxy.translate((0, 0, 100, 100), (810, 10, 297, 1060))


class TestStripRect:
    def test_covers_both_panels_with_a_margin(self):
        top = (810, 10, 297, 514)
        bottom = (810, 554, 297, 515)
        x, y, w, h = proxy.strip_rect(top, bottom)
        # A superset: it may be padded by a pixel to keep the size even.
        assert x == 810 and w >= 297
        assert y <= 10
        assert y + h >= 554 + 515
        assert proxy.translate(top, (x, y, w, h)) == (0, 0, 297, 514)
        assert proxy.translate(bottom, (x, y, w, h)) == (0, 544, 297, 515)

    def test_is_even_sized_so_h264_can_encode_it(self):
        x, y, w, h = proxy.strip_rect((811, 11, 297, 514), (811, 555, 297, 515))
        assert w % 2 == 0 and h % 2 == 0


class TestProxyPreservesDetections:
    """A lossy transcode must not move the stones."""

    @pytest.mark.slow
    def test_stone_positions_match_the_original(self, primary_video, tmp_path):
        import itertools

        from curling_score.detect import rocks
        from curling_score.game import profile as prof
        from curling_score.geometry import layout
        from curling_score.ingest import frames as F

        cal_frames = [
            img for _, img in itertools.islice(F.keyframe_sweep(primary_video), 0, None, 90)
        ][:24]
        panels = layout.detect_panels(cal_frames)
        setups = prof.calibrate_panels(cal_frames, panels)
        strip = proxy.strip_rect(panels.top, panels.bottom)

        # A short proxy over a busy stretch of play.
        import subprocess

        clip = tmp_path / "clip.mp4"
        x, y, w, h = strip
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "3000", "-i", str(primary_video), "-t", "60",
             "-vf", f"crop={w}:{h}:{x}:{y}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(proxy.CRF),
             "-an", str(clip)],
            check=True,
        )

        top_full = panels.top
        top_proxy = proxy.translate(panels.top, strip)
        cal = setups["top"].calib

        orig = {
            round(t): rocks.find_stones(img, cal)
            for t, img in F.window(primary_video, 3000, 3010, 1.0, crop=top_full)
        }
        prox = {
            round(t) + 3000: rocks.find_stones(img, cal)
            for t, img in F.window(clip, 0, 10, 1.0, crop=top_proxy)
        }

        common = sorted(set(orig) & set(prox))
        assert len(common) >= 8, f"only {len(common)} shared timestamps"

        unmatched = []
        for t in common:
            remaining = list(prox[t])
            for a in orig[t]:
                for i, b in enumerate(remaining):
                    if a.color == b.color and abs(a.x_m - b.x_m) < 0.03 \
                            and abs(a.y_m - b.y_m) < 0.03:
                        remaining.pop(i)
                        break
                else:
                    unmatched.append((t, a))
            unmatched.extend((t, b) for b in remaining)

        # Every stone either matches within 3 cm -- a fifth of a stone radius --
        # or is a marginal blob that the re-encode nudged across the area gate.
        # Confident detections must never differ.
        confident = [(t, d) for t, d in unmatched if d.confidence >= 0.65]
        assert not confident, f"confident detections differ: {confident}"
        assert len(unmatched) <= 2, f"too many marginal differences: {unmatched}"


class TestProxyGivesTheSameDeliveries:
    """The proxy is an optimisation; it must not change any conclusion."""

    @pytest.mark.slow
    def test_deliveries_match_the_full_frame_pipeline(self, primary_video, tmp_path):
        import subprocess

        from curling_score.analyze import _proxy_setups
        from curling_score.detect import delivery, rocks
        from curling_score.game import profile as prof
        from curling_score.geometry import layout
        from curling_score.ingest import frames as F

        cal_frames = F.sample_keyframes(primary_video, count=24, stride=90)
        panels = layout.detect_panels(cal_frames)
        setups = prof.calibrate_panels(cal_frames, panels)
        strip = proxy.strip_rect(panels.top, panels.bottom)

        # A 90 s slice of end 4 covering two known deliveries.
        clip = tmp_path / "clip.mp4"
        x, y, w, h = strip
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "2645", "-i", str(primary_video), "-t", "120",
             "-vf", f"crop={w}:{h}:{x}:{y}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(proxy.CRF),
             "-an", str(clip)],
            check=True,
        )
        pset = _proxy_setups(setups, strip)

        full = delivery.find_deliveries([
            (t, rocks.find_stones(img, setups["top"].calib))
            for t, img in F.window(primary_video, 2645, 2765, 10.0,
                                   crop=setups["top"].rect)
        ])
        via = delivery.find_deliveries([
            (t + 2645, rocks.find_stones(img, pset["top"].calib))
            for t, img in F.window(clip, 0, 120, 10.0, crop=pset["top"].rect)
        ])

        assert [d.color for d in full] == [d.color for d in via]
        assert len(full) >= 2, "expected at least two deliveries in this slice"
        for a, b in zip(full, via):
            # Scoring keys off where and when the stone stopped, so those must
            # agree closely.
            assert abs(a.t_rest - b.t_rest) < 0.3
            assert abs(a.rest_x_m - b.rest_x_m) < 0.05
            assert abs(a.rest_y_m - b.rest_y_m) < 0.05
            # First sighting is inherently marginal: the stone is faint and
            # motion-blurred as it enters, so the re-encode can shift which
            # frame first catches it by a few tenths of a second.
            assert abs(a.t_enter - b.t_enter) < 1.5


class TestProxyKeyframeSpacing:
    """The profile sweep reads keyframes, so the proxy must carry as many.

    Left to itself x264 chose an 8.33 s GOP against the source's 5.00 s, giving
    the activity profile 40% fewer samples and inventing a spurious one-end
    "game" out of a changeover.
    """

    @pytest.mark.slow
    def test_proxy_keyframes_are_no_sparser_than_the_source(
        self, primary_video, tmp_path
    ):
        import subprocess

        from curling_score.ingest import frames as F

        clip = tmp_path / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "3000", "-i", str(primary_video), "-t", "150",
             "-vf", "crop=298:1060:810:10",
             *proxy.encode_args(), str(clip)],
            check=True,
        )
        gaps = np.diff([t for t, _ in F.keyframe_sweep(clip, decode=False)])
        assert len(gaps) > 0
        assert float(np.median(gaps)) <= 5.2, f"median GOP {np.median(gaps):.2f}s"
