"""A camera game's clock while it plays: pieces of the live stream matched
against the long_a camera, from a little after the recording starts, again
every RECHECK_S, and probes where the video lost time."""

from datetime import datetime, timezone

from curling_score.feeds import align
from curling_score.live import clock as C, hls

STARTED = datetime(2026, 11, 5, 18, 0, 9, tzinfo=timezone.utc)   # the stream's start
WALL = STARTED.timestamp() - 908.0                                 # the cameras', 15 min before
OLD, NEW = -908.94, -914.72


class Stream:
    """A fake live stream: 5 s segments from segment 0 up to the live edge,
    which trails the cameras' wall clock by ``lag_s``; the video lost 5.78 s
    at ``step_at`` (video t)."""

    def __init__(self, now, step_at=None, lag_s=20.0, start_seq=0):
        self.now, self.step_at, self.lag_s, self.start_seq = now, step_at, lag_s, start_seq
        self.fetched = []

    def edge_t(self):
        return self.now[0] - STARTED.timestamp() - self.lag_s

    def playlist(self, url):
        n = int(self.edge_t() // 5)
        lines = ["#EXTM3U", f"#EXT-X-MEDIA-SEQUENCE:{self.start_seq}"]
        for k in range(self.start_seq, n):
            lines += ["#EXTINF:5.0,", f"seg/{k}.ts"]
        return "\n".join(lines) + "\n"

    def segment(self, uri):
        self.fetched.append(uri)
        return uri.encode()

    def offset_at(self, video_t):
        return OLD if self.step_at is None or video_t < self.step_at else NEW


def video_time(seg0, segk):
    return float(segk.decode().split("/")[-1].split(".")[0]) * 5.0


def aligner(now, stream, head, published_at=STARTED, **kw):
    looks = []

    def match(piece, piece_t_s, camera, guess_s, sheet, **_):
        looks.append(piece_t_s)
        return align.Match(stream.offset_at(piece_t_s), 2.0)

    a = C.Aligner("vid", 3, "/rec", WALL, published_at,
                  head=lambda: head[0], now=lambda: now[0],
                  resolve=lambda: "https://m.example/index.m3u8",
                  fetch_text=stream.playlist, fetch_bytes=stream.segment,
                  video_time=video_time, match=match,
                  write_piece=lambda data: data.decode(), **kw)
    return a, looks


def run(a, now, head, until_s, tick_s=10.0):
    while now[0] < WALL + until_s:
        now[0] += tick_s
        head[0] = now[0] - WALL
        a.step()


def test_nothing_is_fetched_before_the_recording_has_a_little():
    now, head = [WALL], [0.0]
    stream = Stream(now)
    a, _ = aligner(now, stream, head)
    run(a, now, head, C.ALIGN_AFTER_S - 20)
    assert stream.fetched == [] and a.clock() is None


def test_two_agreeing_pieces_give_the_clock():
    now, head = [WALL], [0.0]
    stream = Stream(now)
    a, _ = aligner(now, stream, head)
    run(a, now, head, 1200)
    clock = a.clock()
    assert clock["aligned"] == "segment" and clock["steps"] == [[0.0, OLD]]


def test_a_step_partway_is_found_and_placed():
    now, head = [WALL], [0.0]
    stream = Stream(now, step_at=3000.0)
    a, _ = aligner(now, stream, head)
    run(a, now, head, 5400)
    (_, first), (camera_from, offset) = a.clock()["steps"]
    assert (first, offset) == (OLD, NEW)
    assert abs(camera_from + NEW - 3000.0) <= align.PLACE_S + 5.0   # to a segment
    assert len(stream.fetched) <= C.MAX_FETCHES


def test_no_segment_0_falls_back_to_the_streams_start():
    now, head = [WALL], [0.0]
    stream = Stream(now, start_seq=700)
    a, _ = aligner(now, stream, head)
    run(a, now, head, 600)
    clock = a.clock()
    assert clock["aligned"] == "start_time"
    assert clock["steps"] == [[0.0, WALL - STARTED.timestamp()]]


def test_pieces_that_never_agree_fall_back_to_the_streams_start():
    now, head = [WALL], [0.0]
    stream = Stream(now)
    stream.offset_at = lambda t, k=[0]: OLD + (k.append(0) or len(k)) * 0.5
    a, _ = aligner(now, stream, head)
    run(a, now, head, 600 * (C.ALIGN_TRIES + 1))
    assert a.clock()["aligned"] == "start_time"


def test_a_fetch_that_fails_falls_back_and_never_stops_the_game():
    now, head = [WALL], [0.0]
    stream = Stream(now)

    def broken(url):
        raise OSError("403")

    stream.playlist = broken
    a, _ = aligner(now, stream, head)
    run(a, now, head, 600)
    assert a.clock()["aligned"] == "start_time"


def test_it_stops_fetching_at_the_budget():
    now, head = [WALL], [0.0]
    stream = Stream(now)
    a, _ = aligner(now, stream, head)
    run(a, now, head, 3600 * 6)
    assert len(stream.fetched) <= C.MAX_FETCHES
