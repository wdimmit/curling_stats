"""Recording a live stream from its first segment into a growing MPEG-TS."""

import io
import time

import pytest

from curling_score.live import recorder
from curling_score.live.session import LiveError

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:{seq}
#EXT-X-PROGRAM-DATE-TIME:2026-09-26T20:58:15.060+00:00
#EXTINF:5.0,
seg0.ts
"""


class Proc:
    """A yt-dlp stand-in: prints ffmpeg's progress, then exits with ``code``
    -- or, with ``code=None``, keeps recording."""

    def __init__(self, times, code=0):
        text = "".join(f"frame= 1 fps= 30 q=-1.0 size= 1KiB time={t} bitrate=1\r"
                       for t in times)
        self.stdout = io.BytesIO(text.encode())
        self.code, self.killed = code, False

    def poll(self):
        if self.code is None:
            return None
        return self.code if self.stdout.tell() == len(self.stdout.getvalue()) else None

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.killed = True

    kill = terminate


def make(tmp_path, procs, seq=0, still_live=False, **kw):
    spawned = []

    def popen(cmd, **k):
        spawned.append(cmd)
        return procs.pop(0)

    live = still_live if callable(still_live) else (lambda: still_live)
    kw.setdefault("exit_grace_s", 0.0)
    rec = recorder.YtDlpRecorder(
        "liveVid0001", tmp_path, resolve=lambda: "https://example/playlist.m3u8",
        fetch=lambda url: PLAYLIST.format(seq=seq), popen=popen,
        is_live=live, **kw)
    return rec, spawned


def settle(rec):
    for _ in range(50):
        rec.check()
        time.sleep(0.01)


def test_ffmpegs_progress_time_is_read_as_seconds():
    assert recorder.progress_time("frame= 8099 fps= 31 time=00:04:29.96 bitrate=2.5") \
        == pytest.approx(269.96)
    assert recorder.progress_time("time=01:02:03.50") == pytest.approx(3723.5)
    assert recorder.progress_time("size=N/A time=N/A") is None


def test_it_records_from_the_first_segment(tmp_path):
    rec, spawned = make(tmp_path, [Proc(["00:00:05.00", "00:01:00.00"])])
    rec.start()
    settle(rec)
    (cmd,) = spawned
    assert "--hls-use-mpegts" in cmd and "ffmpeg_i:-live_start_index 0" in cmd
    assert rec.path == tmp_path / "rec.0.ts"
    assert rec.head_s() == pytest.approx(60.0)


def test_a_stream_without_rewind_is_refused(tmp_path):
    rec, spawned = make(tmp_path, [Proc([])], seq=55450)
    with pytest.raises(LiveError):
        rec.start()
    assert spawned == []


def test_it_has_ended_once_the_process_exits_and_the_stream_is_over(tmp_path):
    rec, _ = make(tmp_path, [Proc(["00:10:00.00"], code=0)], still_live=False)
    rec.start()
    settle(rec)
    assert rec.ended()


def test_a_dropout_while_still_live_restarts_into_a_new_file(tmp_path):
    procs = [Proc(["00:10:00.00"], code=1),
             Proc(["00:05:00.00", "00:12:00.00"], code=None)]
    rec, spawned = make(tmp_path, procs, still_live=True)
    rec.start()
    settle(rec)
    assert len(spawned) == 2
    assert not rec.ended()
    # The new file catches up from the start; it takes over once past the old.
    assert rec.path == tmp_path / "rec.1.ts"
    assert rec.head_s() == pytest.approx(720.0)


def test_until_the_new_file_catches_up_the_old_one_is_read(tmp_path):
    procs = [Proc(["00:10:00.00"], code=1), Proc(["00:05:00.00"], code=None)]
    rec, _ = make(tmp_path, procs, still_live=True)
    rec.start()
    settle(rec)
    assert rec.path == tmp_path / "rec.0.ts"
    assert rec.head_s() == pytest.approx(600.0)


def test_a_recording_is_cut_off_at_the_cap(tmp_path):
    rec, _ = make(tmp_path, [Proc(["05:00:01.00"], code=None)], still_live=True)
    rec.max_s = 5 * 3600
    rec.start()
    settle(rec)
    assert rec.ended()


def test_a_dropout_after_the_rewind_window_has_moved_on_cannot_resume(tmp_path):
    # YouTube's live playlist keeps about an hour: past that, starting again
    # from "the first segment" would start somewhere later, on a clock that
    # no longer matches the video's. The stream is given up instead.
    seqs = [0, 720]
    rec, spawned = make(tmp_path, [Proc(["01:10:00.00"], code=1)], still_live=True)
    rec._fetch = lambda url: PLAYLIST.format(seq=seqs.pop(0))
    rec.start()
    with pytest.raises(LiveError):
        for _ in range(50):
            rec.check()
            time.sleep(0.01)
    assert len(spawned) == 1



class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_ended_is_a_plain_read_that_never_touches_the_network(tmp_path):
    # Only the manager's thread keeps the recording going; the lane reads.
    asked = []
    rec, _ = make(tmp_path, [Proc(["00:10:00.00"], code=0)],
                  still_live=lambda: asked.append(1) or False)
    rec.start()
    time.sleep(0.05)
    assert rec.ended() is False and asked == []
    settle(rec)
    assert rec.ended() is True


def test_two_threads_checking_at_once_restart_it_once(tmp_path):
    import threading

    def slow_live():
        time.sleep(0.2)
        return True

    procs = [Proc(["00:10:00.00"], code=1), Proc(["00:01:00.00"], code=None),
             Proc(["00:01:00.00"], code=None)]
    rec, spawned = make(tmp_path, procs, still_live=slow_live)
    rec.start()
    time.sleep(0.05)
    threads = [threading.Thread(target=rec.check) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(spawned) == 2


def test_a_stalled_recorder_whose_stream_is_over_is_ended(tmp_path):
    clock = Clock()
    proc = Proc(["00:30:00.00"], code=None)
    rec, _ = make(tmp_path, [proc], still_live=False, clock=clock)
    rec.start()
    settle(rec)
    assert not rec.ended()
    clock.t = recorder.STALL_S + 1
    rec.check()
    assert rec.ended() and proc.killed


def test_a_stalled_recorder_of_a_live_stream_is_restarted(tmp_path):
    clock = Clock()
    stuck, fresh = Proc(["00:30:00.00"], code=None), Proc(["00:31:00.00"], code=None)
    rec, spawned = make(tmp_path, [stuck, fresh], still_live=True, clock=clock)
    rec.start()
    settle(rec)
    clock.t = recorder.STALL_S + 1
    rec.check()                      # kills the stuck one
    stuck.code = -15                 # and it exits
    rec.check()
    assert stuck.killed and len(spawned) == 2 and not rec.ended()


def test_a_stale_still_live_at_the_very_end_is_waited_out(tmp_path):
    # yt-dlp exits when the stream ends, but YouTube can say "live" for a
    # minute or two after. Restarting then would find the window moved on
    # and fail a game whose every end is already published.
    clock = Clock()
    answers = [True, True, False]
    rec, spawned = make(tmp_path, [Proc(["01:40:00.00"], code=0)],
                        still_live=lambda: answers.pop(0) if answers else False,
                        clock=clock, exit_grace_s=240.0, seq=0)
    rec._fetch = lambda url: PLAYLIST.format(seq=0 if len(spawned) == 0 else 900)
    rec.start()
    for t in (1.0, 60.0, 120.0, 250.0):
        clock.t = t
        settle(rec)
    assert len(spawned) == 1
    assert rec.ended()


def test_a_recording_is_cut_off_by_the_wall_clock_too(tmp_path):
    clock = Clock()
    rec, _ = make(tmp_path, [Proc(["00:10:00.00"], code=None)], still_live=True,
                  clock=clock)
    rec.max_s = 3600.0
    rec.start()
    settle(rec)
    clock.t = 3600.0 + recorder.WALL_MARGIN_S + 1
    rec.check()
    assert rec.ended()
