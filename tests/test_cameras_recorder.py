"""CameraRecorder: a sheet's four captures, each into its camera's own growing
MPEG-TS, with the processes faked."""

import io
import itertools
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from curling_score.cameras import inventory
from curling_score.cameras import recorder as camrec
from curling_score.feeds.cameras import camera_feed
from curling_score.live.session import LiveError

WALL = 1_761_300_000.5
GOOD = ("h264", 1920, 1080)
ROLES = ("house_a", "house_h", "long_a", "long_h")


class Proc:
    """A capture stand-in. ``out`` is what it prints; it runs until
    terminated, or exits with ``code`` once read to the end."""

    def __init__(self, cmd, events, out=b"", code=None):
        self.cmd, self.events = cmd, events
        self.stdout = self.out = io.BytesIO(out)
        self.code, self.terminated, self.killed = code, False, False

    def poll(self):
        if self.terminated or self.killed or self.code is None:
            return self.code
        return self.code if self.out.tell() == len(self.out.getvalue()) else None

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.events.append(("terminate", _camera(self.cmd)))
        self.terminated, self.code = True, 0

    def kill(self):
        self.killed, self.code = True, -9


def _camera(cmd):
    return cmd[cmd.index("--camera") + 1]


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


class World:
    """Fake popen and probe. A capture writes its first keyframe at once unless
    its camera is in ``silent``, then prints its ``heads`` (camera -> the
    seconds its file reaches, in turn); the probe answers ``sizes`` per
    camera."""

    def __init__(self, tmp_path, *, silent=(), sizes=None, heads=None, dying=None):
        self.dir = tmp_path / "rec"
        self.events, self.procs = [], []
        self.silent, self.sizes = set(silent), dict(sizes or {})
        self.heads = dict(heads or {})
        # camera -> what its capture prints before it exits 1, with no keyframe
        self.dying = dict(dying or {})
        self.t = 100.0

    def popen(self, cmd, **kw):
        assert kw.get("stdout") == subprocess.PIPE and kw.get("stderr") == subprocess.STDOUT
        camera = _camera(cmd)
        self.events.append(("capture", camera))
        if camera in self.dying:
            proc = Proc(cmd, self.events, self.dying[camera].encode(), code=1)
            self.procs.append(proc)
            return proc
        if camera not in self.silent:
            Path(_arg(cmd, "--first")).write_bytes(b"\x00\x00\x00\x01\x65idr")
        out = f"2026-10-24 15:09:01,123 INFO capture {camera}: connected\n"
        out += "".join(f"@head {t:.3f}\n" for t in self.heads.get(camera, ()))
        proc = Proc(cmd, self.events, out.encode())
        self.procs.append(proc)
        return proc

    def probe(self, path):
        camera = next(name for role, name in inventory.sheet_cameras(1).items()
                      if path.name == f"first-{role}.h264")
        self.events.append(("probe", camera))
        return self.sizes.get(camera, GOOD)

    def clock(self):
        return self.t

    def recorder(self, **kw):
        return camrec.CameraRecorder(1, self.dir, popen=self.popen, clock=self.clock,
                                     wall=lambda: WALL, probe=self.probe, **kw)

    @property
    def captures(self):
        return list(self.procs)

    def capture(self, camera):
        (proc,) = [p for p in self.procs if _camera(p.cmd) == camera]
        return proc


def settle(rec):
    """Let the fakes' output be read to its end."""
    for thread in rec._relays.values():
        thread.join(timeout=5)


# Sheet 1's cameras by role.
CAM = inventory.sheet_cameras(1)


def heads(house_a, house_h, long_a, long_h):
    """World's heads, by role."""
    return {CAM["house_a"]: house_a, CAM["house_h"]: house_h,
            CAM["long_a"]: long_a, CAM["long_h"]: long_h}


def test_no_composer_is_started(tmp_path):
    """Four captures, each writing its camera's own file, and nothing else."""
    world = World(tmp_path)
    world.recorder().start()

    cameras = inventory.sheet_cameras(1)
    captures = [e for e in world.events if e[0] == "capture"]
    assert captures == [("capture", cameras[r]) for r in ROLES]
    assert len(world.procs) == 4
    assert sorted(e[1] for e in world.events if e[0] == "probe") == sorted(cameras.values())
    for role, proc in zip(ROLES, world.captures):
        assert proc.cmd[1:4] == ["-m", "curling_score.cameras.capture", "--camera"]
        assert _arg(proc.cmd, "--out") == str(world.dir / f"{role}.ts")
        assert _arg(proc.cmd, "--first") == str(world.dir / f"first-{role}.h264")
        assert _arg(proc.cmd, "--t0") == "100.0"     # the recorder's start, by its clock
        assert "--fifo" not in proc.cmd
    assert not list(world.dir.glob("*.nut"))


def test_path_is_the_camera_feed(tmp_path):
    world = World(tmp_path)
    rec = world.recorder()
    assert rec.path == camera_feed(world.dir, 1)
    with pytest.raises(TypeError):
        os.fspath(rec.path)


def test_start_refuses_a_camera_that_is_not_1080p_h264(tmp_path):
    world = World(tmp_path, sizes={"1H-POV": ("h264", 1280, 720)})
    rec = world.recorder()

    world.dir.mkdir(parents=True)
    for role in ROLES:                    # what the captures had written
        (world.dir / f"{role}.ts").write_bytes(b"ts")
    with pytest.raises(LiveError, match=r"^camera 1H-POV sent no H\.264 1920x1080 within 15 s$"):
        rec.start()
    assert all(p.terminated for p in world.captures)
    assert rec.ended()
    # The other cameras' keyframes and files are frames of people: none is
    # left behind.
    assert sorted(world.dir.glob("first-*")) == []
    assert sorted(world.dir.glob("*.ts")) == []


def test_first_keyframes_are_deleted_once_probed(tmp_path):
    """They are only for the probe; readers read the cameras' files."""
    world = World(tmp_path)
    probed = []

    def probe(path):
        probed.append(path.exists())
        return GOOD

    world.probe = probe
    world.recorder().start()

    assert probed == [True] * 4
    assert sorted(world.dir.glob("first-*")) == []


def test_start_refuses_a_camera_that_sends_nothing(tmp_path):
    world = World(tmp_path, silent={"1A-POV"})
    world.clock = time.monotonic
    rec = world.recorder(first_timeout_s=0.3)

    with pytest.raises(LiveError, match=r"^camera 1A-POV sent no H\.264 1920x1080 within 0\.3 s$"):
        rec.start()
    assert all(p.terminated for p in world.captures)


def test_a_capture_that_dies_at_start_gives_its_reason(tmp_path):
    # Without ffmpeg a capture exits at once, saying why, and that is the
    # error: not "sent no H.264", which sends whoever reads it to the camera.
    world = World(tmp_path, dying={"1A-HOUSE": "ffmpeg is not installed: capture runs only in "
                                                "the worker image\n"})
    world.clock = time.monotonic
    with pytest.raises(LiveError, match=r"^camera 1A-HOUSE's capture exited \(1\): ffmpeg is "
                                        r"not installed: capture runs only in the worker image$"):
        world.recorder().start()


def test_a_capture_that_dies_at_start_names_its_error_not_its_chatter(tmp_path):
    world = World(tmp_path, dying={"1A-HOUSE": (
        "2026-11-03 15:09:01,100 INFO capture 1A-HOUSE: connecting to "
        "srt://192.168.0.241:1600?mode=caller&latency=500000&timeout=5000000\n"
        "2026-11-03 15:09:16,100 ERROR capture 1A-HOUSE: stopping: no keyframe within 15 s\n")})
    world.clock = time.monotonic
    with pytest.raises(LiveError, match=r"^camera 1A-HOUSE's capture exited \(1\): stopping: "
                                        r"no keyframe within 15 s$"):
        world.recorder().start()


def test_start_does_not_take_a_stale_first_keyframe(tmp_path):
    """A first keyframe left by an earlier recording in the same directory is
    not this camera's answer."""
    world = World(tmp_path, silent={"1H-HOUSE"})
    world.dir.mkdir()
    (world.dir / "first-house_h.h264").write_bytes(b"old")
    world.clock = time.monotonic

    with pytest.raises(LiveError, match="camera 1H-HOUSE"):
        world.recorder(first_timeout_s=0.3).start()


def test_wall_started_is_set_at_start_and_it_is_never_whole(tmp_path):
    world = World(tmp_path)
    rec = world.recorder()
    assert rec.wall_started is None

    rec.start()
    assert rec.wall_started == WALL
    assert rec.whole() is False


def test_the_sessions_zero_and_wall_started_are_read_together_before_any_capture(tmp_path):
    """A frame at t in the recording was taken at wall_started + t, as the
    lane dates a throw: the captures time their packets from the recorder's
    zero on its monotonic clock, and wall_started is that same moment by the
    wall clock."""
    world = World(tmp_path)
    ticks = itertools.count(100.0, 0.25)

    def clock():
        world.events.append(("clock", t := next(ticks)))
        return t

    def wall():
        world.events.append(("wall",))
        return WALL

    rec = camrec.CameraRecorder(1, world.dir, popen=world.popen, clock=clock, wall=wall,
                                probe=world.probe).start()

    at = world.events.index(("wall",))
    assert world.events[at - 1][0] == "clock"
    assert at < world.events.index(("capture", inventory.sheet_cameras(1)["house_a"]))
    t0 = world.events[at - 1][1]
    assert [_arg(p.cmd, "--t0") for p in world.captures] == [str(t0)] * 4
    assert rec.wall_started == WALL and world.events.count(("wall",)) == 1


def test_head_is_the_slowest_camera(tmp_path):
    world = World(tmp_path, heads=heads([4.0], [3.5], [5.0], [4.2]))
    rec = world.recorder()
    assert rec.head_s() == 0.0
    rec.start()
    settle(rec)

    assert rec.head_s() == pytest.approx(3.5)
    rec.check()
    assert not rec.ended()


def test_head_is_nothing_until_every_camera_has_said(tmp_path):
    world = World(tmp_path, heads=heads([4.0], [], [5.0], [4.2]))
    rec = world.recorder().start()
    settle(rec)
    assert rec.head_s() == 0.0


def test_head_lines_are_not_logged(tmp_path, caplog):
    world = World(tmp_path, heads=heads([1.0], [1.0], [1.0], [1.0]))
    with caplog.at_level(logging.DEBUG, logger=camrec.__name__):
        settle(world.recorder().start())
    assert "@head" not in caplog.text
    assert "capture 1A-HOUSE: connected" in caplog.text


def test_head_follows_the_cameras_still_running(tmp_path, caplog):
    """A camera whose capture has gone stops at its last frame; the recording
    goes on, as far as the others reach."""
    world = World(tmp_path, heads=heads([4.0], [3.5], [5.0], [4.2]))
    rec = world.recorder().start()
    settle(rec)
    world.capture(CAM["house_h"]).code = 1

    with caplog.at_level(logging.WARNING, logger=camrec.__name__):
        rec.check()
        rec.check()
    assert not rec.ended()
    assert rec.head_s() == pytest.approx(4.0)
    warnings = [r.getMessage() for r in caplog.records if CAM["house_h"] in r.getMessage()]
    assert warnings == [f"camera {CAM['house_h']}'s capture exited (1); its view stops at 3.5 s"]


def test_every_camera_gone_ends_the_recording(tmp_path):
    world = World(tmp_path, heads=heads([4.0], [3.5], [5.0], [4.2]))
    rec = world.recorder().start()
    settle(rec)
    for proc in world.captures:
        proc.code = 1

    with pytest.raises(LiveError, match="^every camera of sheet 1 has stopped$"):
        rec.check()
    assert rec.ended()
    assert rec.head_s() == pytest.approx(3.5)


def test_a_head_that_stops_moving_is_a_stall(tmp_path):
    world = World(tmp_path, heads=heads([4.0], [4.0], [4.0], [4.0]))
    rec = world.recorder().start()
    settle(rec)
    rec.check()
    world.t += camrec.STALL_S - 1
    rec.check()
    assert not rec.ended()

    world.t += 1
    with pytest.raises(LiveError, match="^sheet 1's recording stalled at 4 s$"):
        rec.check()
    assert rec.ended()
    assert all(p.terminated for p in world.captures)


def test_head_moves_with_each_head_line(tmp_path):
    """A capture prints a head line twice a second: read through a buffer
    that waits for 4 KiB, the head would move every few minutes."""
    world = World(tmp_path)
    fake = world.popen

    def popen(cmd, **kw):
        fake(cmd, **kw)                   # its first keyframe, for the probe
        script = ("import sys, time; sys.stdout.write('@head 3.000\\n'); sys.stdout.flush(); "
                  "time.sleep(30)")
        return subprocess.Popen([sys.executable, "-c", script], **kw)

    world.popen = popen
    rec = world.recorder().start()
    try:
        deadline = time.monotonic() + 5
        while rec.head_s() == 0.0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert rec.head_s() == pytest.approx(3.0)
    finally:
        rec.stop()


def test_it_caps_like_the_youtube_recorder(tmp_path):
    world = World(tmp_path, heads=heads([10.0], [10.0], [10.0], [10.0]))
    rec = world.recorder(max_s=10.0).start()
    settle(rec)

    rec.check()
    assert rec.ended()
    assert all(p.terminated for p in world.captures)


def test_stop_terminates_the_four_captures(tmp_path):
    world = World(tmp_path)
    rec = world.recorder().start()
    rec.stop()

    terminated = [e[1] for e in world.events if e[0] == "terminate"]
    assert terminated == list(inventory.sheet_cameras(1).values())
    rec.check()
    assert not rec.ended()
    rec.stop()  # again: nothing left to stop
    assert [e[0] for e in world.events].count("terminate") == 4


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_probe_reads_a_keyframe_with_ffprobe(tmp_path):
    path = tmp_path / "first.h264"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=1920x1080:r=60", "-frames:v", "1", "-c:v", "libx264",
                    "-f", "h264", str(path)], check=True)

    assert camrec.probe(path) == GOOD
    path.write_bytes(b"not video")
    assert camrec.probe(path) != GOOD
