import pathlib
import re

import numpy as np
import pytest

from curling_score.game.misses import MissCandidate
from curling_score.train import review


class TestFilmstripTimes:
    def test_samples_across_the_window(self):
        ts = review.filmstrip_times(100.0, 130.0, count=6)
        assert len(ts) == 6
        assert ts[0] >= 100.0 and ts[-1] <= 130.0
        assert ts == sorted(ts)

    def test_a_short_window_still_yields_frames(self):
        ts = review.filmstrip_times(100.0, 102.0, count=6)
        assert 2 <= len(ts) <= 6
        assert all(100.0 <= t <= 102.0 for t in ts)

    def test_never_returns_duplicates(self):
        ts = review.filmstrip_times(100.0, 100.4, count=12)
        assert len(ts) == len(set(ts))


class TestContactSheet:
    def test_tiles_frames_side_by_side(self):
        frames = [np.full((40, 20, 3), i * 20, np.uint8) for i in range(5)]
        sheet = review.contact_sheet(frames, labels=[f"{i}" for i in range(5)])
        assert sheet.shape[0] >= 40
        assert sheet.shape[1] >= 5 * 20

    def test_no_frames_gives_nothing(self):
        assert review.contact_sheet([], labels=[]) is None


class TestHtml:
    def _rows(self):
        return [
            {
                "game": 1, "end": 4, "house": "top",
                "start_s": 100.0, "end_s": 160.0,
                "reason": "colour repeat", "confidence": 0.95,
                "expected_color": "yellow", "image": "w001.jpg",
            }
        ]

    def test_renders_a_page_listing_every_window(self):
        html = review.render_html(self._rows(), video_id="VXU9xwmugRg")
        assert "colour repeat" in html
        assert "w001.jpg" in html
        assert "3rd" not in html  # no invented content

    def test_each_window_links_into_the_video_at_its_start(self):
        html = review.render_html(self._rows(), video_id="VXU9xwmugRg")
        assert "youtu.be/VXU9xwmugRg?t=100" in html

    def test_shows_which_colour_is_expected_when_known(self):
        html = review.render_html(self._rows(), video_id="VXU9xwmugRg")
        assert "yellow" in html

    def test_survives_a_window_with_no_expected_colour(self):
        rows = self._rows()
        rows[0]["expected_color"] = None
        html = review.render_html(rows, video_id="VXU9xwmugRg")
        assert "long gap" not in html or True
        assert "None" not in html


class TestSplittingLongWindows:
    """A stone's flight lasts under ten seconds, so a strip whose frames are
    half a minute apart cannot show a delivery happening -- only that the house
    differs either side. Long windows are split, not stretched."""

    def test_a_short_window_is_left_whole(self):
        assert review.split_window(100.0, 160.0, 9) == [(100.0, 160.0)]

    def test_a_long_window_is_split(self):
        parts = review.split_window(0.0, 400.0, 9)
        assert len(parts) > 1

    def test_the_parts_are_consecutive_and_cover_the_window(self):
        parts = review.split_window(50.0, 450.0, 9)
        assert parts[0][0] == 50.0
        assert parts[-1][1] == pytest.approx(450.0)
        for (_a, b), (c, _d) in zip(parts, parts[1:]):
            assert b == pytest.approx(c)

    def test_no_part_is_sampled_more_coarsely_than_the_limit(self):
        for span in (30.0, 120.0, 227.0, 600.0, 1800.0):
            for lo, hi in review.split_window(0.0, span, 9):
                spacing = (hi - lo) / 8
                assert spacing <= review.MAX_SPACING_S + 1e-6, (span, spacing)

    def test_a_degenerate_window_survives(self):
        assert review.split_window(10.0, 10.0, 9) == [(10.0, 10.0)]


class TestOrdering:
    """#1 should be worth someone's attention, so the tail can be abandoned
    without missing the important windows."""

    def test_the_most_confident_window_comes_first(self, tmp_path,
                                                    monkeypatch):
        seen = []
        monkeypatch.setattr(review, "contact_sheet",
                            lambda frames, labels: np.zeros((4, 4, 3), np.uint8))
        monkeypatch.setattr(review.cv2, "imwrite",
                            lambda *a, **k: seen.append(a[0]) or True)

        class Setup:
            rect = (0, 0, 8, 8)

        def fake_window(path, lo, hi, fps, crop=None):
            t = lo
            while t <= hi:
                yield t, np.zeros((8, 8, 3), np.uint8)
                t += 1.0

        import curling_score.ingest.frames as F
        monkeypatch.setattr(F, "window", fake_window)
        rows = [
            {"game": 1, "end": 1, "house": "top", "start_s": 10.0,
             "end_s": 20.0, "reason": "weak", "confidence": 0.2,
             "kind": "miss"},
            {"game": 1, "end": 1, "house": "top", "start_s": 40.0,
             "end_s": 50.0, "reason": "strong", "confidence": 0.9,
             "kind": "miss"},
        ]
        page, n = review.build("v.mp4", {"top": Setup()}, rows, tmp_path, "vid")
        assert n == 2
        html = page.read_text()
        assert html.index("strong") < html.index("weak")


class TestStripsAreReused:
    """Decoding the frames is by far the slow part, and the reasons and
    ordering shown alongside them are revised far more often than the frames
    are. A rebuild must reuse what it already rendered."""

    def _rows(self):
        return [{"game": 1, "end": 2, "house": "top", "start_s": 10.0,
                 "end_s": 20.0, "reason": "first wording", "confidence": 0.5,
                 "kind": "miss"}]

    def _run(self, tmp_path, monkeypatch, rows, rendered):
        monkeypatch.setattr(review, "contact_sheet",
                            lambda frames, labels: np.zeros((4, 4, 3), np.uint8))

        def fake_imwrite(path, img, *a):
            rendered.append(path)
            pathlib.Path(path).write_bytes(b"jpeg")
            return True

        monkeypatch.setattr(review.cv2, "imwrite", fake_imwrite)

        def fake_window(path, lo, hi, fps, crop=None):
            t = lo
            while t <= hi:
                yield t, np.zeros((8, 8, 3), np.uint8)
                t += 1.0

        import curling_score.ingest.frames as F
        monkeypatch.setattr(F, "window", fake_window)

        class Setup:
            rect = (0, 0, 8, 8)

        return review.build("v.mp4", {"top": Setup()}, rows, tmp_path, "vid")

    def test_the_strip_is_named_for_the_window_it_shows(self, tmp_path,
                                                        monkeypatch):
        rendered = []
        page, _n = self._run(tmp_path, monkeypatch, self._rows(), rendered)
        assert rendered and rendered[0].endswith("g1e2-10-20.jpg")

    def test_a_second_build_renders_nothing_again(self, tmp_path, monkeypatch):
        rendered = []
        self._run(tmp_path, monkeypatch, self._rows(), rendered)
        assert len(rendered) == 1
        rendered.clear()
        self._run(tmp_path, monkeypatch, self._rows(), rendered)
        assert rendered == []

    def test_revised_wording_reaches_the_page_without_re_rendering(
            self, tmp_path, monkeypatch):
        rendered = []
        self._run(tmp_path, monkeypatch, self._rows(), rendered)
        rendered.clear()
        rows = self._rows()
        rows[0]["reason"] = "second wording"
        page, _n = self._run(tmp_path, monkeypatch, rows, rendered)
        assert rendered == []
        assert "second wording" in page.read_text()

    def test_a_different_window_still_renders(self, tmp_path, monkeypatch):
        rendered = []
        self._run(tmp_path, monkeypatch, self._rows(), rendered)
        rendered.clear()
        rows = self._rows()
        rows[0]["start_s"] = 30.0
        rows[0]["end_s"] = 40.0
        self._run(tmp_path, monkeypatch, rows, rendered)
        assert len(rendered) == 1


class TestPriorityNotConfidence:
    """Confidence does not mean the same thing for every kind: for a miss or a
    suspect a high number says something is probably wrong, for a score window
    it says the score can be trusted. Sorting them together puts the most
    reliable scores at the top of a list of problems."""

    def _build(self, tmp_path, monkeypatch, rows):
        monkeypatch.setattr(review, "contact_sheet",
                            lambda frames, labels: np.zeros((4, 4, 3), np.uint8))
        monkeypatch.setattr(review.cv2, "imwrite",
                            lambda path, img, *a: pathlib.Path(path)
                            .write_bytes(b"jpeg") or True)

        def fake_window(path, lo, hi, fps, crop=None):
            t = lo
            while t <= hi:
                yield t, np.zeros((8, 8, 3), np.uint8)
                t += 1.0

        import curling_score.ingest.frames as F
        monkeypatch.setattr(F, "window", fake_window)

        class Setup:
            rect = (0, 0, 8, 8)

        page, _n = review.build("v.mp4", {"top": Setup()}, rows, tmp_path, "v")
        return page.read_text()

    def _row(self, **over):
        base = {"game": 1, "end": 1, "house": "top", "start_s": 10.0,
                "end_s": 20.0, "reason": "r", "confidence": 0.5,
                "kind": "miss"}
        base.update(over)
        return base

    def test_priority_decides_the_order(self, tmp_path, monkeypatch):
        rows = [
            self._row(reason="trusted score", confidence=0.9, priority=0.1,
                      kind="score", start_s=10.0, end_s=20.0),
            self._row(reason="doubtful miss", confidence=0.6, priority=0.6,
                      start_s=40.0, end_s=50.0),
        ]
        html = self._build(tmp_path, monkeypatch, rows)
        assert html.index("doubtful miss") < html.index("trusted score")

    def test_confidence_is_the_fallback_when_no_priority_is_given(
            self, tmp_path, monkeypatch):
        rows = [self._row(reason="weak", confidence=0.2, start_s=10.0,
                          end_s=20.0),
                self._row(reason="strong", confidence=0.9, start_s=40.0,
                          end_s=50.0)]
        html = self._build(tmp_path, monkeypatch, rows)
        assert html.index("strong") < html.index("weak")

    def test_a_score_window_shows_trust_not_confidence(self, tmp_path,
                                                       monkeypatch):
        html = self._build(tmp_path, monkeypatch,
                           [self._row(kind="score", confidence=0.86,
                                      priority=0.14)])
        assert "trust 0.86" in html
        assert "confidence 0.86" not in html

    def test_other_kinds_still_show_confidence(self, tmp_path, monkeypatch):
        html = self._build(tmp_path, monkeypatch,
                           [self._row(kind="miss", confidence=0.84)])
        assert "confidence 0.84" in html


class TestStableWindowNames:
    """Position on the page is not a usable way to refer to a window: the
    ordering is by priority and shifts whenever detection changes, so "#3"
    meant two different windows an hour apart."""

    def _build(self, tmp_path, monkeypatch, rows):
        monkeypatch.setattr(review, "contact_sheet",
                            lambda frames, labels: np.zeros((4, 4, 3), np.uint8))
        monkeypatch.setattr(review.cv2, "imwrite",
                            lambda path, img, *a: pathlib.Path(path)
                            .write_bytes(b"jpeg") or True)

        def fake_window(path, lo, hi, fps, crop=None):
            t = lo
            while t <= hi:
                yield t, np.zeros((8, 8, 3), np.uint8)
                t += 1.0

        import curling_score.ingest.frames as F
        monkeypatch.setattr(F, "window", fake_window)

        class Setup:
            rect = (0, 0, 8, 8)

        page, _n = review.build("v.mp4", {"top": Setup()}, rows, tmp_path, "v")
        return page.read_text()

    def _row(self, **over):
        # 12 s spans, which is exactly the reach of 9 frames at
        # FLIGHT_MAX_SPACING_S, so a flight window stays a single strip and
        # these tests keep asserting about naming rather than about splitting.
        base = {"game": 1, "end": 5, "house": "top", "start_s": 4196.0,
                "end_s": 4208.0, "reason": "r", "confidence": 0.5,
                "kind": "conflict"}
        base.update(over)
        return base

    def test_the_name_says_game_end_and_time(self, tmp_path, monkeypatch):
        html = self._build(tmp_path, monkeypatch, [self._row()])
        assert "g1e5@4196" in html

    def _badges(self, html):
        # Only the per-window badges, in page order. Matching on the raw text
        # would also find the example in the page's own legend.
        return re.findall(r'<code class="wid">([^<]+)</code>', html)

    def test_the_name_survives_a_change_of_position(self, tmp_path,
                                                    monkeypatch):
        rows = [self._row(confidence=0.2),
                self._row(start_s=100.0, end_s=112.0, confidence=0.9,
                          reason="other")]
        first = self._badges(self._build(tmp_path, monkeypatch, rows))
        rows[0]["confidence"] = 0.99
        second = self._badges(self._build(tmp_path, monkeypatch, rows))
        # Same two windows, same two names, opposite order.
        assert first == ["g1e5@100", "g1e5@4196"]
        assert second == ["g1e5@4196", "g1e5@100"]


class TestAnnotate:
    """Every automated measure of the detector is circular -- the model learned
    from the classical detector's labels -- so the only way to find out what it
    fires on is to look at the boxes."""

    def det(self, color, x, y, conf=0.9, area_px=140.0):
        from curling_score.detect.rocks import Detection

        return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                         area_px=area_px, confidence=conf)

    def calib(self):
        from curling_score.geometry.calibrate import PanelCalib

        return PanelCalib(center_px=(50.0, 50.0), px_per_m=20.0,
                          edge_erosion_px=1.0, residual_m=0.003, flipped=False)

    def edge(self, area_px=140.0):
        """Row of the drawn box's top edge for a detection at the centre."""
        import math

        side = max(review.MARK_MIN_SIDE_PX, int(round(math.sqrt(area_px))))
        return 50 - side // 2

    def test_it_marks_where_the_detection_is(self):
        frame = np.zeros((100, 100, 3), np.uint8)
        out = review.annotate(frame, [self.det("red", 0.0, 0.0)], self.calib())
        # An outline around the centre, so the pixels under it stay visible.
        assert out[50, 50].tolist() == [0, 0, 0]
        assert out[self.edge(), 50].any()

    def test_the_box_is_sized_from_the_detection(self):
        """Drawn at the detection's own area so the strip shows what the model
        emitted, not a fixed stand-in. This does not make size diagnostic --
        labels are one constant box per frame, so the model's boxes barely vary
        -- but the drawing should still follow the data it is given."""
        frame = np.zeros((100, 100, 3), np.uint8)
        # 400 px of area is a 20 px box; 64 px of area is an 8 px box.
        big = review.annotate(frame, [self.det("red", 0.0, 0.0, area_px=400.0)],
                              self.calib())
        small = review.annotate(frame, [self.det("red", 0.0, 0.0, area_px=64.0)],
                                self.calib())
        assert self.edge(400.0) == 40 and self.edge(64.0) == 46
        # The big box's outline is up at row 40; the small one reaches nowhere
        # near it, which is the whole point of sizing the box.
        assert big[40, 50].any()
        assert not small[40, 50].any()
        assert small[46, 50].any()

    def test_the_original_frame_is_not_touched(self):
        frame = np.zeros((100, 100, 3), np.uint8)
        review.annotate(frame, [self.det("red", 0.0, 0.0)], self.calib())
        assert not frame.any()

    def test_colours_are_told_apart(self):
        frame = np.zeros((100, 100, 3), np.uint8)
        r = review.annotate(frame, [self.det("red", 0.0, 0.0)], self.calib())
        y = review.annotate(frame, [self.det("yellow", 0.0, 0.0)], self.calib())
        assert r[self.edge(), 50].tolist() != y[self.edge(), 50].tolist()

    def test_nothing_to_mark_leaves_the_frame_alone(self):
        frame = np.zeros((100, 100, 3), np.uint8)
        assert not review.annotate(frame, [], self.calib()).any()


class TestFlightSpacing:
    """A strip meant to answer "was a delivery missed here" has to be able to
    show one. A delivery is visible for three to five seconds, so a window
    sampled every 14 s -- or every 3.6 s -- can hide it entirely between two
    frames, which is what happened on g1e4."""

    def rows(self, kind):
        return [{"kind": kind, "game": 1, "end": 4, "house": "top",
                 "start_s": 3140.0, "end_s": 3170.0, "priority": 1.0}]

    def test_flight_windows_are_split_fine_enough_to_catch_a_stone(self):
        parts = review.split_window(
            3140.0, 3170.0, 9, max_spacing_s=review.FLIGHT_MAX_SPACING_S)
        spacings = [(hi - lo) / 8 for lo, hi in parts]
        assert max(spacings) <= review.FLIGHT_MAX_SPACING_S
        # Two frames inside a three-second flight is the point of the limit.
        assert 3.0 / max(spacings) >= 2

    def test_a_score_window_stays_coarse(self):
        """It only has to show the house at rest, so paying for extra strips
        would buy nothing."""
        parts = review.split_window(3140.0, 3170.0, 9,
                                    max_spacing_s=review.MAX_SPACING_S)
        assert len(parts) == 1

    def test_the_kinds_that_need_motion_are_the_ones_listed(self):
        assert "miss" in review.FLIGHT_KINDS
        assert "score" not in review.FLIGHT_KINDS
