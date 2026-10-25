"""One throw measured by the per-rock passes, over a list of one."""

from types import SimpleNamespace

from curling_score.detect import longview
from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.game import releasetime
from curling_score.practice import enrich as E
from tests.test_delivery import det

# No lateral fit: the passes are stubs, and the release keeper is the tests' own.
SIDEVIEWS = {n: SimpleNamespace(name=n, has_lateral=False) for n in ("left", "right")}
SETUPS = {h: SimpleNamespace(view_y_min_m=-4.0, view_x_limit_m=2.2, hog_line=None)
          for h in ("top", "bottom")}
MODELS = SimpleNamespace(broom_model="broom", line_model="line", release_model="release")


def arrival(color="red", t_enter=40.0, x=0.1, y=-0.4):
    return Delivery(color=color, t_enter=t_enter, t_rest=t_enter + 9.4, entry_y_m=3.8,
                    rest_x_m=x, rest_y_m=y, travel_m=4.2,
                    track=((t_enter, x, 3.8), (t_enter + 9.4, x, y)))


def recording(calls, fail=(), kwargs=None, firsts=None):
    def stage(name):
        def run(shots, video, *views, model=None, **kw):
            calls.append((name, *[v.name for v in views], model))
            if kwargs is not None:
                kwargs[name] = kw
            if firsts is not None:
                firsts[name] = (video, longview._keeping)
            if name in fail:
                raise RuntimeError(f"{name} fell over")
        return run
    return E.Stages(hog=stage("hog"), side_release=stage("side"),
                    broom=stage("broom"), line=stage("line"), release=stage("release"),
                    spin=stage("spin"))


def measure(calls, *, house="top", dv=None, rel=None, fail=(), sideviews=SIDEVIEWS,
            models=MODELS, firsts=None):
    return E.enrich("rec.ts", SETUPS, sideviews, models, house=house, arrival=dv,
                    release=rel, house_frames=[], throw_frames=[],
                    stages=recording(calls, fail, firsts=firsts))


def test_the_passes_run_in_order_on_the_cameras_an_end_uses():
    calls = []
    measure(calls, dv=arrival(), rel=Release("red", 22.0, 1.0, 2.0))
    # Thrown to the top house: the camera facing the thrower is the bottom
    # end's ("right"), the one seeing the house thrown to is "left".
    assert calls == [("hog", "right", None), ("side", "right", None),
                     ("broom", "left", "broom"), ("line", "right", "left", "line"),
                     ("release", "right", "release"), ("spin", "right", "line")]


def test_a_throw_that_never_arrived_still_gets_its_line():
    """A hogged rock was still thrown down a line: the line pass reads it from
    the near hog line on, and its far-camera path ends where it stopped."""
    calls = []
    shot = measure(calls, rel=Release("yellow", 30.0, 1.0, 2.0))
    assert [c[0] for c in calls] == ["hog", "side", "broom", "line", "spin"]
    assert shot.delivery is None and shot.color == "yellow"


def test_one_pass_failing_costs_only_its_own_figure():
    calls = []
    measure(calls, dv=arrival(), fail=("hog",))
    assert [c[0] for c in calls] == ["hog", "side", "broom", "line", "release", "spin"]


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


def test_every_held_broom_is_kept_for_the_line_to_choose_from():
    calls, kwargs = [], {}
    E.enrich("rec.ts", SETUPS, SIDEVIEWS, MODELS, house="top", arrival=arrival(),
             release=Release("red", 22.0, 1.0, 2.0), house_frames=[], throw_frames=[],
             stages=recording(calls, kwargs=kwargs))
    assert kwargs["broom"] == {"all_held": True}


class FakeKeeper:
    def __init__(self, calls=None):
        self.cleared, self.calls = False, calls

    def rewindow(self, windows):
        if self.calls is not None:
            self.calls.append(("rewindow", windows))

    def clear(self):
        self.cleared = True


def test_the_release_is_read_after_the_line_on_the_frames_the_passes_kept(monkeypatch):
    calls, firsts, made = [], {}, []
    keeper = FakeKeeper()

    def keeper_for(shots, view, model):
        made.append((view.name, model))
        return keeper

    monkeypatch.setattr(releasetime, "keeper_for", keeper_for)
    measure(calls, dv=arrival(), rel=Release("red", 22.0, 1.0, 2.0), firsts=firsts)
    # The keeper is the hog camera's, and watches the passes that decode it.
    assert made == [("right", "release")]
    for name in ("hog", "side", "broom", "line"):
        assert firsts[name][1] is keeper
    # The release read takes what it kept, then lets it go.
    assert firsts["release"][0] is keeper and keeper.cleared
    assert longview._keeping is None


def test_the_keeper_is_let_go_when_a_pass_fails(monkeypatch):
    keeper = FakeKeeper()
    monkeypatch.setattr(releasetime, "keeper_for", lambda shots, view, model: keeper)
    measure([], dv=arrival(), fail=("line", "release"))
    assert keeper.cleared and longview._keeping is None


def test_a_release_the_long_camera_found_is_kept_for(monkeypatch):
    # The overhead missed the release; the side release pass finds it, and the
    # keeper is pointed at its seconds before the line's read decodes them.
    calls = []
    keeper = FakeKeeper(calls)
    monkeypatch.setattr(releasetime, "keeper_for", lambda shots, view, model: keeper)
    stages = recording(calls)
    found = Release("red", 31.0, 1.0, 2.0)

    def side(shots, video, view, model=None):
        calls.append(("side", view.name, model))
        shots[0].release = found

    stages.side_release = side
    E.enrich("rec.ts", SETUPS, SIDEVIEWS, MODELS, house="top", arrival=arrival(),
             release=None, house_frames=[], throw_frames=[], stages=stages)
    names = [c[0] for c in calls]
    assert names.index("side") < names.index("rewindow") < names.index("broom")
    assert calls[names.index("rewindow")][1] == [(30.0, 38.0)]


def test_a_throw_that_never_arrived_keeps_nothing(monkeypatch):
    made = []
    monkeypatch.setattr(releasetime, "keeper_for",
                        lambda shots, view, model: made.append(1) or FakeKeeper())
    calls = []
    measure(calls, rel=Release("yellow", 30.0, 1.0, 2.0))
    assert made == [] and "release" not in [c[0] for c in calls]


def test_models_from_before_the_release_model_read_none():
    calls = []
    measure(calls, dv=arrival(), models=SimpleNamespace(broom_model="broom", line_model="line"))
    assert calls[-2] == ("release", "right", None)
