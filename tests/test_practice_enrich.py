"""One throw measured by the per-rock passes, over a list of one."""

from types import SimpleNamespace

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.practice import enrich as E
from tests.test_delivery import det

SIDEVIEWS = {"left": SimpleNamespace(name="left"), "right": SimpleNamespace(name="right")}
SETUPS = {h: SimpleNamespace(view_y_min_m=-4.0, view_x_limit_m=2.2, hog_line=None)
          for h in ("top", "bottom")}
MODELS = SimpleNamespace(broom_model="broom", line_model="line")


def arrival(color="red", t_enter=40.0, x=0.1, y=-0.4):
    return Delivery(color=color, t_enter=t_enter, t_rest=t_enter + 9.4, entry_y_m=3.8,
                    rest_x_m=x, rest_y_m=y, travel_m=4.2,
                    track=((t_enter, x, 3.8), (t_enter + 9.4, x, y)))


def recording(calls, fail=(), kwargs=None):
    def stage(name):
        def run(shots, video, *views, model=None, **kw):
            calls.append((name, *[v.name for v in views], model))
            if kwargs is not None:
                kwargs[name] = kw
            if name in fail:
                raise RuntimeError(f"{name} fell over")
        return run
    return E.Stages(hog=stage("hog"), side_release=stage("side"),
                    broom=stage("broom"), line=stage("line"))


def measure(calls, *, house="top", dv=None, rel=None, fail=(), sideviews=SIDEVIEWS):
    return E.enrich("rec.ts", SETUPS, sideviews, MODELS, house=house, arrival=dv,
                    release=rel, house_frames=[], throw_frames=[],
                    stages=recording(calls, fail))


def test_the_passes_run_in_order_on_the_cameras_an_end_uses():
    calls = []
    measure(calls, dv=arrival(), rel=Release("red", 22.0, 1.0, 2.0))
    # Thrown to the top house: the camera facing the thrower is the bottom
    # end's ("right"), the one seeing the house thrown to is "left".
    assert calls == [("hog", "right", None), ("side", "right", None),
                     ("broom", "left", "broom"), ("line", "right", "left", "line")]


def test_a_throw_that_never_arrived_gets_no_line():
    calls = []
    shot = measure(calls, rel=Release("yellow", 30.0, 1.0, 2.0))
    assert [c[0] for c in calls] == ["hog", "side", "broom"]
    assert shot.delivery is None and shot.color == "yellow"


def test_one_pass_failing_costs_only_its_own_figure():
    calls = []
    measure(calls, dv=arrival(), fail=("hog",))
    assert [c[0] for c in calls] == ["hog", "side", "broom", "line"]


def test_without_side_views_only_the_overhead_passes_run():
    calls = []
    shot = measure(calls, dv=arrival(), sideviews=None)
    assert calls == [] and shot.delivery is not None


def test_the_house_is_read_just_before_the_arrival_and_just_after_the_rest():
    dv = arrival()                                   # enters 40.0, rests 49.4
    frames = []
    for i in range(30 * 5, 60 * 5):
        t = i / 5
        here = [det("yellow", 0.8, 0.2)]
        if t >= dv.t_rest:
            here.append(det("red", 0.1, -0.4))
        if t >= 53.0:                                # after the 3 s house read
            here.append(det("yellow", -1.0, 1.0))
        frames.append((t, here))
    shot = E.build_shot(dv, None, frames)
    assert sorted((s.color, round(s.x_m, 2)) for s in shot.stones) == [
        ("red", 0.1), ("yellow", 0.8)]
    assert shot.stones[shot.delivered_stone_index].color == "red"
    assert [s["color"] for s in shot.house_delta["added"]] == ["red"]


def test_a_throw_nobody_held_a_broom_for_still_gets_its_line():
    # Practice is often solo: Qzh8 had a broom on 17 of 42 throws, uKWn on none.
    calls, kwargs = [], {}
    E.enrich("rec.ts", SETUPS, SIDEVIEWS, MODELS, house="top", arrival=arrival(),
             release=Release("red", 22.0, 1.0, 2.0), house_frames=[], throw_frames=[],
             stages=recording(calls, kwargs=kwargs))
    assert kwargs["line"] == {"without_broom": True}
