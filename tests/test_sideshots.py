from curling_score.harvest import sideshots


class FakeShot:
    def __init__(self, n, color, t_rel=None, t_rest=None):
        self.number, self.color = n, color
        self.release = type("R", (), {"t_s": t_rel})() if t_rel else None
        self.t_rest_s = t_rest


class TestWindows:
    def test_the_camera_is_the_one_that_watches_the_throwing_end(self):
        # A top-house end is thrown from the bottom, and the camera that can
        # see the bottom hog line is the one at the top -- CAMERA_FOR's job.
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rel=100.0)], "top")
        assert w.camera == "left"

    def test_the_other_house_gets_the_other_camera(self):
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rel=100.0)], "bottom")
        assert w.camera == "right"

    def test_a_window_spans_the_flight_from_the_release(self):
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rel=100.0)], "top")
        assert (w.t0, w.t1) == (100.0 + 1.0, 100.0 + 6.5)   # longview.WINDOW_S

    def test_a_shot_with_no_release_falls_back_to_the_arrival(self):
        (w,) = sideshots.windows_for_end([FakeShot(1, "red", t_rest=200.0)], "top")
        assert w.t0 < 200.0 and w.t1 < 200.0

    def test_a_shot_with_neither_gets_no_window(self):
        assert sideshots.windows_for_end([FakeShot(1, "red")], "top") == []

    def test_the_colour_travels_with_the_window(self):
        (w,) = sideshots.windows_for_end([FakeShot(3, "yellow", t_rel=50.0)], "top")
        assert w.color == "yellow" and w.shot_number == 3
