"""The practice replay's harness: when footage arrived, and each throw's latency."""

import json

import pytest

from curling_score.practice import harness


def heads(*samples):
    h = harness.HeadClock()
    for wall, head in samples:
        h.sample(head, wall=wall)
    return h


class TestHeadClock:
    def test_a_moment_is_reached_between_samples_at_a_steady_rate(self):
        h = heads((0.0, 0.0), (10.0, 10.0), (20.0, 12.0))
        assert h.reached(5.0) == pytest.approx(5.0)
        assert h.reached(11.0) == pytest.approx(15.0)
        assert h.reached(-1.0) == 0.0
        assert h.reached(13.0) is None

    def test_a_head_that_did_not_move_adds_no_sample(self):
        h = heads((0.0, 5.0), (1.0, 5.0))
        assert h.samples == [(0.0, 5.0)]


def doc(*throws, t0=1000.0):
    return {"practice": 1, "status": "watching", "t0_s": t0, "throws": list(throws)}


def throw(id, t_rest=None, t_release=None, arrived=True):
    return {"id": id, "t_rest_s": t_rest, "t_release_s": t_release, "arrived": arrived,
            "house": "top", "color": "red", "split_s": None, "line": None, "rest": None}


def test_each_throw_is_written_once_with_its_latency(tmp_path):
    now = [160.0]
    sink = harness.Sink(tmp_path, heads((100.0, 0.0), (200.0, 100.0)),
                        clock=lambda: now[0], show=lambda line: None)
    sink.publish(doc(throw("t_1030.0", t_rest=1050.0)))
    now[0] = 185.0
    sink.publish(doc(throw("t_1030.0", t_rest=1050.0),
                     throw("t_1010.0", t_release=1010.0, arrived=False)))
    lines = [json.loads(x) for x in (tmp_path / "throws.jsonl").read_text().splitlines()]
    assert [x["id"] for x in lines] == ["t_1030.0", "t_1010.0"]
    assert lines[0]["latency_s"] == 10.0          # rested at 50 s, readable at 150 s
    assert lines[1]["latency_s"] == 15.0          # given up at 10 + 60 s, readable at 170 s
    assert json.loads((tmp_path / "practice.json").read_text())["throws"][1]["id"] == "t_1010.0"


def test_run_steps_until_the_watch_is_done_and_rests_when_idle():
    class Watch:
        done, steps = False, [True, False, True]

        def step(self):
            did = self.steps.pop(0)
            self.done = not self.steps
            return did

    slept = []
    harness.run(Watch(), sleep=slept.append, idle_s=0.25)
    assert slept == [0.25]
