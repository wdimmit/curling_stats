"""CameraRecorder: a sheet's four captures composed into one growing MPEG-TS,
with the processes faked."""

import io
import logging
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from curling_score.cameras import inventory, layout
from curling_score.cameras import recorder as camrec
from curling_score.live.session import LiveError

WALL = 1_761_300_000.5
GOOD = ("h264", 1920, 1080)
ROLES = ("house_a", "house_h", "long_a", "long_h")


class Proc:
    """A capture or composer stand-in. ``out`` is what it prints; it runs
    until terminated, or exits with ``code`` once read to the end."""

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
        who = "ffmpeg" if self.cmd[0] == "ffmpeg" else _camera(self.cmd)
        self.events.append(("terminate", who))
        self.terminated, self.code = True, 0

    def kill(self):
        self.killed, self.code = True, -9


def _camera(cmd):
    return cmd[cmd.index("--camera") + 1]


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


class World:
    """Fake popen and probe. A capture writes its first keyframe at once unless
    its camera is in ``silent``; the probe answers ``sizes`` per camera."""

    def __init__(self, tmp_path, *, silent=(), sizes=None, composer_out=b"", composer_code=None):
        self.dir = tmp_path / "rec"
        self.events, self.procs = [], []
        self.silent, self.sizes = set(silent), dict(sizes or {})
        self.composer_out, self.composer_code = composer_out, composer_code
        self.t = 100.0

    def popen(self, cmd, **kw):
        assert kw.get("stdout") == subprocess.PIPE and kw.get("stderr") == subprocess.STDOUT
        if cmd[0] == "ffmpeg":
            self.events.append(("composer",))
            proc = Proc(cmd, self.events, self.composer_out, self.composer_code)
        else:
            camera = _camera(cmd)
            self.events.append(("capture", camera))
            if camera not in self.silent:
                Path(_arg(cmd, "--first")).write_bytes(b"\x00\x00\x00\x01\x65idr")
            proc = Proc(cmd, self.events, f"2026-10-24 15:09:01,123 INFO capture {camera}: "
                                          "connected\n".encode())
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
        return [p for p in self.procs if p.cmd[0] != "ffmpeg"]

    @property
    def composer(self):
        (proc,) = [p for p in self.procs if p.cmd[0] == "ffmpeg"]
        return proc


def progress(*times):
    return "".join(f"frame=  1 fps= 60 q=20.0 size= 1KiB time={t} bitrate=20000.0kbits/s\r"
                   for t in times).encode()


def settle(rec):
    """Let the fakes' output be read to its end."""
    for thread in [rec._composer.reader, *rec._relays]:
        thread.join(timeout=5)


def test_start_spawns_four_captures_then_composer(tmp_path):
    world = World(tmp_path)
    rec = world.recorder().start()

    cameras = inventory.sheet_cameras(1)
    captures = [e for e in world.events if e[0] == "capture"]
    assert captures == [("capture", cameras[r]) for r in ROLES]
    # Every camera is probed before the composer starts, and it starts last.
    assert world.events.index(("composer",)) == len(world.events) - 1
    assert sorted(e[1] for e in world.events if e[0] == "probe") == sorted(cameras.values())
    for role, proc in zip(ROLES, world.captures):
        assert proc.cmd[1:4] == ["-m", "curling_score.cameras.capture", "--camera"]
        fifo = world.dir / f"cam-{role}.h264"
        assert _arg(proc.cmd, "--fifo") == str(fifo)
        assert _arg(proc.cmd, "--first") == str(world.dir / f"first-{role}.h264")
        assert stat.S_ISFIFO(fifo.stat().st_mode)
    assert rec.path == world.dir / "rec.0.ts"


def test_start_refuses_a_camera_that_is_not_1080p_h264(tmp_path):
    world = World(tmp_path, sizes={"1H-POV": ("h264", 1280, 720)})
    rec = world.recorder()

    with pytest.raises(LiveError, match=r"^camera 1H-POV sent no H\.264 1920x1080 within 15 s$"):
        rec.start()
    assert ("composer",) not in world.events
    assert all(p.terminated for p in world.captures)
    assert rec.ended()
    # The other cameras' keyframes are frames of people: none is left behind.
    assert sorted(world.dir.glob("first-*")) == []
    assert sorted(world.dir.glob("cam-*")) == []


def test_first_keyframes_are_deleted_once_probed(tmp_path):
    """They are only for the probe; the composer reads the FIFOs."""
    world = World(tmp_path)
    probed = []

    def probe(path):
        probed.append(path.exists())
        return GOOD

    world.probe = probe
    world.recorder().start()

    assert probed == [True] * 4
    assert sorted(world.dir.glob("first-*")) == []
    assert all(stat.S_ISFIFO((world.dir / f"cam-{r}.h264").stat().st_mode) for r in ROLES)


def test_start_refuses_a_camera_that_sends_nothing(tmp_path):
    world = World(tmp_path, silent={"1A-POV"})
    world.clock = time.monotonic
    rec = world.recorder(first_timeout_s=0.3)

    with pytest.raises(LiveError, match=r"^camera 1A-POV sent no H\.264 1920x1080 within 0\.3 s$"):
        rec.start()
    assert ("composer",) not in world.events
    assert all(p.terminated for p in world.captures)


def test_start_does_not_take_a_stale_first_keyframe(tmp_path):
    """A first keyframe left by an earlier recording in the same directory is
    not this camera's answer."""
    world = World(tmp_path, silent={"1H-HOUSE"})
    world.dir.mkdir()
    (world.dir / "first-house_h.h264").write_bytes(b"old")
    world.clock = time.monotonic

    with pytest.raises(LiveError, match="camera 1H-HOUSE"):
        world.recorder(first_timeout_s=0.3).start()


def test_composer_args_carry_filtergraph_and_nvenc_at_20M(tmp_path):
    world = World(tmp_path)
    world.recorder().start()
    cmd = world.composer.cmd

    assert "-copyts" in cmd
    fifos = [world.dir / f"cam-{r}.h264" for r in ROLES]
    for fifo in fifos:
        at = cmd.index(str(fifo))
        assert cmd[at - 7:at + 1] == ["-thread_queue_size", "1024", "-use_wallclock_as_timestamps",
                                      "1", "-f", "h264", "-i", str(fifo)]
    inputs = {r: i for i, r in enumerate(ROLES)}
    assert _arg(cmd, "-filter_complex") == layout.filtergraph(layout.load(1), inputs=inputs,
                                                              t0=WALL)
    assert _arg(cmd, "-map") == "[out]"
    tail = cmd[cmd.index("-c:v"):]
    assert tail == ["-c:v", "h264_nvenc", "-b:v", "20M", "-maxrate", "25M", "-g", "60",
                    "-bf", "0", "-f", "mpegts", str(world.dir / "rec.0.ts")]
    # The progress the head is read from, even with the log turned down.
    assert "-stats" in cmd and "-nostdin" in cmd and "-y" in cmd


def test_composer_threads_are_capped(tmp_path):
    """FFmpeg's default of a thread per CPU, all waking for every frame at a
    live 60 fps, cost 9 cores a sheet."""
    world = World(tmp_path)
    world.recorder().start()
    cmd = world.composer.cmd

    assert _arg(cmd, "-filter_complex_threads") == str(camrec.FILTER_THREADS)
    for role in ROLES:
        at = cmd.index(str(world.dir / f"cam-{role}.h264"))
        assert cmd[at - 9:at - 7] == ["-threads", str(camrec.DECODE_THREADS)]
    assert camrec.FILTER_THREADS == camrec.DECODE_THREADS == 2


def test_lookback_is_zero_and_wall_started_set(tmp_path):
    world = World(tmp_path)
    rec = world.recorder()
    assert rec.lookback_got_s == 0.0 and rec.wall_started is None

    rec.start()
    assert rec.lookback_got_s == 0.0
    assert rec.wall_started == WALL
    assert f"setpts=PTS-{WALL:.6f}/TB" in _arg(world.composer.cmd, "-filter_complex")
    assert rec.whole() is False


def test_head_is_the_composers_time(tmp_path):
    world = World(tmp_path, composer_out=progress("00:00:01.50", "00:01:02.25"))
    rec = world.recorder()
    assert rec.head_s() == 0.0
    rec.start()
    settle(rec)

    assert rec.head_s() == pytest.approx(62.25)
    rec.check()
    assert not rec.ended()


def test_head_moves_with_each_progress_line(tmp_path):
    """ffmpeg prints a progress line twice a second, ~120 bytes: read through
    a buffer that waits for 4 KiB, the head would move every ~20 s."""
    world = World(tmp_path)
    fake = world.popen

    def popen(cmd, **kw):
        if cmd[0] != "ffmpeg":
            return fake(cmd, **kw)
        line = "frame= 180 fps= 60 q=20.0 size= 1KiB time=00:00:03.00 bitrate=20000.0kbits/s\r"
        script = f"import sys, time; sys.stderr.write({line!r}); sys.stderr.flush(); time.sleep(30)"
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


def test_composer_exit_ends_recording_and_raises(tmp_path):
    said = b"[h264_nvenc @ 0x5] No capable devices found\n"
    world = World(tmp_path, composer_out=progress("00:00:05.00") + said, composer_code=1)
    rec = world.recorder().start()
    settle(rec)

    with pytest.raises(LiveError, match=r"composer exited \(1\): .*No capable devices found$"):
        rec.check()
    assert rec.ended()
    assert all(p.terminated for p in world.captures)
    rec.check()  # said once: the manager fails the job once


def test_a_stalled_composer_ends_recording_and_raises(tmp_path):
    world = World(tmp_path, composer_out=progress("00:00:05.00"))
    rec = world.recorder().start()
    settle(rec)
    rec.check()
    world.t += camrec.STALL_S - 1
    rec.check()
    assert not rec.ended()

    world.t += 1
    with pytest.raises(LiveError, match="stalled"):
        rec.check()
    assert rec.ended()
    assert world.composer.terminated and all(p.terminated for p in world.captures)


def test_one_camera_gone_keeps_recording(tmp_path, caplog):
    """Its panel holds its last frame; the other three go on."""
    world = World(tmp_path, composer_out=progress("00:00:05.00"))
    rec = world.recorder().start()
    settle(rec)
    world.captures[2].code = 1

    with caplog.at_level(logging.WARNING, logger=camrec.__name__):
        rec.check()
        rec.check()
    assert not rec.ended()
    assert not world.composer.terminated
    warnings = [r for r in caplog.records if "1A-POV" in r.getMessage()]
    assert len(warnings) == 1


def test_every_camera_gone_ends_recording(tmp_path):
    """With no camera left the composer would run on held frames, unpaced."""
    world = World(tmp_path, composer_out=progress("00:00:05.00"))
    rec = world.recorder().start()
    settle(rec)
    for proc in world.captures:
        proc.code = 1

    with pytest.raises(LiveError, match="every camera"):
        rec.check()
    assert rec.ended() and world.composer.terminated


def test_it_caps_like_the_youtube_recorder(tmp_path):
    world = World(tmp_path, composer_out=progress("00:00:10.00"))
    rec = world.recorder(max_s=10.0).start()
    settle(rec)

    rec.check()
    assert rec.ended()
    assert world.composer.terminated


def test_stop_terminates_all_five_processes(tmp_path):
    world = World(tmp_path)
    rec = world.recorder().start()
    rec.stop()

    terminated = [e[1] for e in world.events if e[0] == "terminate"]
    # The captures first: a composer stopped under them would read as their
    # reader going away, an error.
    assert terminated == [*inventory.sheet_cameras(1).values(), "ffmpeg"]
    rec.check()
    assert not rec.ended()
    rec.stop()  # again: nothing left to stop
    assert [e[0] for e in world.events].count("terminate") == 5


def test_capture_logs_are_passed_on(tmp_path, caplog):
    world = World(tmp_path)
    with caplog.at_level(logging.INFO, logger=camrec.__name__):
        settle(world.recorder().start())
    assert "capture 1A-HOUSE: connected" in caplog.text


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_probe_reads_a_keyframe_with_ffprobe(tmp_path):
    path = tmp_path / "first.h264"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=1920x1080:r=60", "-frames:v", "1", "-c:v", "libx264",
                    "-f", "h264", str(path)], check=True)

    assert camrec.probe(path) == GOOD
    path.write_bytes(b"not video")
    assert camrec.probe(path) != GOOD
