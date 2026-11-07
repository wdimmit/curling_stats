"""A live recording converted through its club's broadcast layout as it is recorded."""

import queue
import threading
import time
from pathlib import Path

import pytest

from curling_score import clubs
from curling_score.game import clips as game_clips
from curling_score.live import converting as CV
from curling_score.live import manager as manager_mod
from curling_score.live.session import LiveError


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeInner:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.path = self.dir / "rec.0.ts"
        self._ended = self._whole = False
        self.checked = 0

    def start(self):
        return self

    def check(self):
        self.checked += 1

    def head_s(self):
        return 0.0

    def ended(self):
        return self._ended

    def whole(self):
        return self._whole

    def stop(self):
        pass


class FakeConv:
    def __init__(self, frames=0, first_match=None, frac=1.0):
        self.frames, self.first_match, self.frac = frames, first_match, frac

    def stats(self, window):
        return self.first_match is not None, self.frac


class Script:
    """A stand-in for convert_file: per source file, the heads (and converter
    states) the test feeds it; None ends that conversion."""

    def __init__(self):
        self.q = {}
        self.calls = []

    def feed(self, name, *items):
        for it in items:
            self.q.setdefault(name, queue.Queue()).put(it)

    def __call__(self, src, dst, layout, *, follow, timeout_s, on_head, stop, **kw):
        self.calls.append((Path(src).name, Path(dst).name))
        Path(dst).write_bytes(b"ts")
        q = self.q.setdefault(Path(src).name, queue.Queue())
        while not stop():
            try:
                item = q.get(timeout=0.02)
            except queue.Empty:
                continue
            if item is None:
                break
            head, conv = item
            on_head(head, conv)
        return {}


def make(tmp_path, script):
    inner = FakeInner(tmp_path)
    rec = CV.ConvertingRecorder(inner, "dfw-5u", tmp_path, convert=script).start()
    return inner, rec


def test_head_follows_the_converted_file(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    script.feed("rec.0.ts", (5.0, FakeConv(150, 0)), (10.0, FakeConv(300, 0)))
    assert wait_until(lambda: rec.head_s() == 10.0)
    assert rec.path == tmp_path / "norm.0.ts"
    rec.stop()


def test_restarted_part_is_followed_without_going_back(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    script.feed("rec.0.ts", (60.0, FakeConv(1800, 0)))
    assert wait_until(lambda: rec.head_s() == 60.0)
    inner.path = tmp_path / "rec.1.ts"           # yt-dlp started a new part from segment 0
    rec.check()
    assert wait_until(lambda: ("rec.1.ts", "norm.1.ts") in script.calls)
    script.feed("rec.1.ts", (20.0, FakeConv(600, 0)))
    time.sleep(0.1)
    assert rec.head_s() == 60.0 and rec.path == tmp_path / "norm.0.ts"
    script.feed("rec.1.ts", (61.0, FakeConv(1830, 0)))
    assert wait_until(lambda: rec.path == tmp_path / "norm.1.ts")
    assert rec.head_s() == 61.0
    rec.stop()


def test_a_conversion_that_gave_up_on_a_stall_starts_again(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    script.feed("rec.0.ts", (30.0, FakeConv(900, 0)), None)     # -follow timed out mid-stream
    assert wait_until(lambda: len(script.calls) == 1 and not rec._parts[-1].running())
    rec.check()
    assert wait_until(lambda: ("rec.0.ts", "norm.1.ts") in script.calls)
    rec.stop()


def test_ended_waits_for_the_converter(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    inner._ended = True
    assert not rec.ended()
    script.feed("rec.0.ts", (30.0, FakeConv(900, 0)), None)
    assert wait_until(rec.ended)
    rec.stop()


def test_slate_does_not_fail_before_twenty_minutes(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    script.feed("rec.0.ts", (1190.0, FakeConv(35_700, None, 0.0)))
    assert wait_until(lambda: rec.head_s() == 1190.0)
    rec.check()
    script.feed("rec.0.ts", (1200.0, FakeConv(36_000, None, 0.0)))
    assert wait_until(lambda: rec.head_s() == 1200.0)
    with pytest.raises(LiveError, match="broadcast layout mismatch"):
        rec.check()
    rec.stop()


def test_mostly_unmatched_after_first_match_fails(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    # matched from frame 100, then a long intermission graphic: under 40% of the last ten minutes
    script.feed("rec.0.ts", (300.0, FakeConv(9_000, 100, 0.1)))
    assert wait_until(lambda: rec.head_s() == 300.0)
    rec.check()                                   # not ten minutes past the first match yet
    script.feed("rec.0.ts", (700.0, FakeConv(21_000, 100, 0.39)))
    assert wait_until(lambda: rec.head_s() == 700.0)
    with pytest.raises(LiveError):
        rec.check()
    rec.stop()


def test_whole_is_the_inner_recorders(tmp_path):
    script = Script()
    inner, rec = make(tmp_path, script)
    assert rec.whole() is False
    inner._whole = True
    assert rec.whole() is True
    assert rec.layout_name == "dfw-5u"
    rec.stop()


def test_find_video_prefers_the_converted_recording(tmp_path):
    live = tmp_path / "live" / "vid"
    live.mkdir(parents=True)
    (live / "rec.0.ts").write_bytes(b"x" * 100)
    (live / "norm.0.ts").write_bytes(b"x" * 10)
    assert game_clips.find_video("vid", [tmp_path]) == live / "norm.0.ts"


def test_manager_wraps_when_the_club_has_a_layout(tmp_path):
    job = {"id": "j", "video_id": "vid", "kind": "live"}
    m = manager_mod.LiveManager(None, "w", model_id="m", gpu=None, root=tmp_path, club=clubs.DALLAS)
    assert isinstance(m._recorder(job), CV.ConvertingRecorder)
    m = manager_mod.LiveManager(None, "w", model_id="m", gpu=None, root=tmp_path)
    assert not isinstance(m._recorder(job), CV.ConvertingRecorder)
