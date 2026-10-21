from curling_score.detect import delivery
from curling_score.detect.rocks import Detection
from curling_score.fpframes import Settings, settings
from curling_score.fpframes.record import (
    Recorder, active, recording, width_check)
from curling_score.geometry.sideview import SideView

NEAR_RECT = (800, 554, 300, 516)
VIEW = SideView(rect=(1110, 0, 810, 1080), tee_row=465.0, hog_row=547.0,
                centre_col=411.0, lat_px_per_m_at_tee=140.0,
                centre_line=(411.0, 0.0))


def det(color, x, y, *, x_px=100.0, y_px=50.0, area=400.0, conf=0.77):
    return Detection(color=color, x_m=x, y_m=y, x_px=x_px, y_px=y_px,
                     area_px=area, confidence=conf)


def track(color, points):
    (t, x, y), *rest = points
    tr = delivery._Track(color, t, x, y)
    for p in rest:
        tr.add(*p)
    return tr


def recorder():
    return Recorder("vid", models={"overhead": "o-1", "side": None},
                    pipeline="2026.10.21.1")


def close(rec, game=0, end=3, near_seq=(), cameras=None):
    rec.close_end(game=game, end=end, house="bottom", video=None,
                  near_rect=NEAR_RECT, near_seq=list(near_seq),
                  cameras=cameras or {})


def test_settings_read_the_switch_and_cap():
    assert settings({}) == Settings(False, 20.0)
    assert settings({"WORKER_FP_HARVEST": "1",
                     "WORKER_FP_HARVEST_GB": "5"}) == Settings(True, 5.0)


def test_nothing_is_active_by_default_and_recording_none_is_a_no_op():
    assert active() is None
    with recording(None):
        assert active() is None
    rec = recorder()
    with recording(rec):
        assert active() is rec
    assert active() is None


def test_an_overhead_track_is_joined_to_its_detection_box():
    rec = recorder()
    rec.overhead_track("ovh.wander", track("red", [(1.0, 0.1, 2.0)]))
    close(rec, near_seq=[(1.0, [det("red", 0.1, 2.0)])])
    (f,) = rec.firings()
    assert f.box == (890.0, 594.0, 910.0, 614.0)
    assert f.where == "bottom"
    assert f.conf == 0.77
    assert (f.game, f.end, f.house) == (0, 3, "bottom")


def test_a_track_with_no_matching_detection_is_dropped():
    rec = recorder()
    rec.overhead_track("ovh.quiet", track("red", [(1.0, 0.1, 2.0)]))
    close(rec, near_seq=[(1.0, [det("yellow", 0.1, 2.0)])])
    assert rec.firings() == []


def test_a_track_keeps_five_samples_and_joins_on_the_middle_one():
    pts = [(i * 0.2, 0.0, 3.0 - i * 0.1) for i in range(20)]
    rec = recorder()
    rec.overhead_track("ovh.lateral", track("red", pts), end_i=9, width=1.2)
    (pending,) = rec.pending()
    assert pending.score == 10
    assert len(pending.samples) == 5
    assert pending.info == {"width": 1.2}
    seq = [(t, [det("red", x, y, x_px=t * 10)]) for t, x, y in pts]
    close(rec, near_seq=seq)
    (f,) = rec.firings()
    assert f.t == pending.samples[2][0]


def test_width_bands():
    assert width_check(0.4) == width_check(2.0) == "side.width_far"
    assert width_check(0.45) == width_check(1.6) == "side.width_off"


def test_side_boxes_are_thinned_per_half_second():
    rec = recorder()
    for t, ratio in [(10.0, 2.5), (10.1, 3.0), (10.2, 2.2)]:
        rec.side_width(VIEW, t, (100, 200, 130, 230), 0.6, ratio,
                       color="red", pass_="release", top_estimated=False)
    close(rec, cameras={VIEW.rect: "far_left"})
    (f,) = rec.firings()
    assert f.info["ratio"] == 3.0
    assert f.check == "side.width_far"
    assert f.box == (1210, 200, 1240, 230)
    assert f.where == "far_left"


def test_a_lone_side_box_is_a_stone_wide_with_an_estimated_top():
    rec = recorder()
    with rec.side_view(VIEW):
        rec.side_lone(5.0, 400.0, 465.0, 0.55)
    w = 0.291 * 140.0
    close(rec)
    (f,) = rec.firings()
    assert f.check == "side.lone"
    assert f.top_estimated
    assert f.score == 0.55
    assert f.box == (400.0 - w / 2 + 1110, 465.0 - w / 2, 400.0 + w / 2 + 1110,
                     465.0)
    assert f.where == "side"


def test_a_lone_box_outside_side_view_is_ignored():
    rec = recorder()
    rec.side_lone(5.0, 400.0, 465.0, 0.55)
    assert rec.pending() == []


def test_closing_the_same_end_again_replaces_its_firings():
    rec = recorder()
    seq = [(1.0, [det("red", 0.1, 2.0)]), (2.0, [det("red", 0.2, 1.0)])]
    rec.overhead_track("ovh.wander", track("red", [(1.0, 0.1, 2.0)]))
    rec.overhead_track("ovh.quiet", track("red", [(2.0, 0.2, 1.0)]))
    close(rec, near_seq=seq)
    assert len(rec.firings()) == 2
    rec.overhead_track("ovh.wander", track("red", [(1.0, 0.1, 2.0)]))
    close(rec, near_seq=seq)
    assert len(rec.firings()) == 1


def test_begin_end_drops_what_is_pending():
    rec = recorder()
    rec.overhead_track("ovh.wander", track("red", [(1.0, 0.1, 2.0)]))
    rec.begin_end()
    assert rec.pending() == []


def test_a_raising_hook_is_swallowed():
    rec = recorder()
    rec.overhead_track("ovh.wander", object())
    assert rec.pending() == []


def test_a_hook_that_keeps_failing_logs_one_traceback(caplog):
    from curling_score.fpframes import record
    record._FAILURES.clear()
    rec = recorder()
    with caplog.at_level("ERROR", logger="curling_score.fpframes.record"):
        for _ in range(50):
            rec.overhead_track("ovh.wander", object())
    assert [r for r in caplog.records if r.exc_info] == caplog.records
    assert len(caplog.records) == 1


def test_a_failing_hook_logs_a_count_every_so_often(caplog):
    from curling_score.fpframes import record
    record._FAILURES.clear()
    rec = recorder()
    with caplog.at_level("ERROR", logger="curling_score.fpframes.record"):
        for _ in range(record.LOG_EVERY):
            rec.overhead_track("ovh.wander", object())
    assert len(caplog.records) == 2
    assert str(record.LOG_EVERY) in caplog.records[-1].getMessage()
    assert not caplog.records[-1].exc_info


def test_side_box_notes_only_a_box_off_the_width_of_a_stone_at_its_row():
    from curling_score.detect import longview
    row = 500.0
    expect = VIEW.stone_width_at(row, longview.STONE_WIDTH_AT_HOG_PX)
    rec = recorder()
    rec.side_box(VIEW, 3.0, 400.0, row, expect, 0.8, color="red", pass_="slide")
    assert rec.pending() == []
    rec.side_box(VIEW, 3.0, 400.0, row, expect * 3, 0.8, color="red", pass_="slide")
    (f,) = rec.pending()
    assert f.check == "side.width_far" and f.info["pass"] == "slide"
    assert f.top_estimated and f.box == (
        400.0 - expect * 1.5, row - expect * 1.5, 400.0 + expect * 1.5, row)


def test_side_box_swallows_a_view_that_cannot_say_its_width():
    rec = recorder()
    rec.side_box(object(), 3.0, 400.0, 500.0, 30.0, 0.8, color=None, pass_="follow")
    assert rec.pending() == []
