"""A live stream recorded from its first segment into a growing MPEG-TS file.

yt-dlp is asked for the stream's HLS playlist from its first segment
(``-live_start_index 0``) and writes MPEG-TS, which can be read while it is
still being written -- measured on a live stream: readable to within seconds
of the write head, caught up at ~15x from a start 50 minutes back, and frame
for frame on the archived video's clock afterwards. yt-dlp's own
``--live-from-start`` (DASH) is no use here: it assembles the file only when
it stops.

That needs the stream to keep its whole DVR window, as the club's do. A
stream whose playlist does not begin at segment 0 is refused rather than
recorded on a clock that does not match the video's.

The recorder runs as a subprocess, never in-process: the pipeline's decoding
threads and a second thread doing PyAV work have deadlocked before (see
``ingest/frames.py``).
"""

import logging
import re
import subprocess
import sys
import threading
from pathlib import Path

from curling_score.ingest import cache, source
from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

# Nothing the club streams runs longer; anything that does is cut off here.
MAX_RECORD_S = 5 * 3600.0
_TIME_RE = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_SEQ_RE = re.compile(r"#EXT-X-MEDIA-SEQUENCE:(\d+)")


def progress_time(line: str) -> float | None:
    """The ``time=`` in one of ffmpeg's progress lines, in seconds."""
    m = _TIME_RE.search(line)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def first_media_sequence(playlist: str) -> int | None:
    m = _SEQ_RE.search(playlist or "")
    return int(m.group(1)) if m else None


class _Part:
    def __init__(self, path, proc):
        self.path, self.proc, self.head = path, proc, 0.0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        # ffmpeg ends its progress lines with a carriage return, not a newline.
        pending = ""
        for chunk in iter(lambda: self.proc.stdout.read(4096), b""):
            pending += chunk.decode("utf-8", "replace")
            *lines, pending = re.split(r"[\r\n]", pending)
            for line in lines:
                t = progress_time(line)
                if t is not None:
                    self.head = max(self.head, t)
        t = progress_time(pending)
        if t is not None:
            self.head = max(self.head, t)


class YtDlpRecorder:
    """One live stream, recorded into ``directory``.

    ``head_s`` is how far the file reaches on the stream's clock; ``path`` is
    the file to read. A recorder that drops out while the stream is still
    live starts again from the first segment into a new file, which takes
    over once it has caught up -- the stream's DVR window makes that exact.
    """

    def __init__(self, video_id, directory, *, pot_provider=None, cookies=None,
                 resolve=None, fetch=None, popen=subprocess.Popen, is_live=None,
                 max_s: float = MAX_RECORD_S, require_first_segment: bool = True):
        """``require_first_segment=False`` records a stream whose window has
        already moved on, on a clock that starts where the window did -- only
        for exercising the recorder on a stream that is not a game."""
        self.video_id, self.dir = video_id, Path(directory)
        self.require_first_segment = require_first_segment
        self.pot_provider, self.cookies = pot_provider, cookies
        self._resolve = resolve or self._resolve_hls
        self._fetch = fetch or self._fetch_text
        self._popen, self._is_live = popen, is_live or self._still_live
        self.max_s = max_s
        self._parts = []
        self._lock = threading.Lock()
        self._ended = self._stopped = False

    # --- lifecycle ------------------------------------------------------------

    def start(self):
        self._require_first_segment()
        self._spawn()
        return self

    def _require_first_segment(self):
        """Refuse unless the live playlist still begins at the stream's start.

        YouTube's live playlist keeps a rewind window -- the whole stream on a
        new one, about the last hour on a long one (720 five-second segments,
        measured on four 24/7 news streams). A recording that does not begin
        at segment 0 would be on a clock that does not match the video's.
        """
        if not self.require_first_segment:
            return
        url = self._resolve()
        seq = first_media_sequence(self._fetch(url)) if url else None
        if seq not in (None, 0):
            raise LiveError(f"the stream's playlist begins at segment {seq}, not 0: "
                            "its rewind window no longer reaches the start, so it "
                            "cannot be recorded on the video's own clock")

    def _spawn(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            path = self.dir / f"rec.{len(self._parts)}.ts"
        cmd = [sys.executable, "-m", "yt_dlp", "--no-part", "--newline", "--no-warnings",
               *self._extractor_args(), "-f", cache.FORMAT, "--hls-use-mpegts",
               "--downloader-args", "ffmpeg_i:-live_start_index 0",
               "-o", str(path), source.canonical_url(self.video_id)]
        proc = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with self._lock:
            self._parts.append(_Part(path, proc))
        log.info("recording %s into %s", self.video_id, path)

    def check(self):
        """Keep the recording going: called every few seconds by the manager."""
        if self._ended or self._stopped or not self._parts:
            return
        if self.head_s() >= self.max_s:
            log.info("recording %s reached the %.0f h cap", self.video_id, self.max_s / 3600)
            self.stop()
            self._ended = True
            return
        last = self._parts[-1]
        code = last.proc.poll()
        if code is None:
            return
        last.reader.join(timeout=5)
        if self._is_live():
            log.warning("recording %s stopped (exit %s) while still live; restarting",
                        self.video_id, code)
            # Only while the rewind window still reaches the first segment;
            # past it this raises, and the recording's VOD path takes the game.
            self._require_first_segment()
            self._spawn()
        else:
            self._ended = True

    def ended(self) -> bool:
        self.check()
        return self._ended

    def stop(self):
        self._stopped = True
        for part in self._parts:
            if part.proc.poll() is None:
                part.proc.terminate()
                try:
                    part.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    part.proc.kill()

    # --- what readers see -----------------------------------------------------

    def _current(self):
        with self._lock:
            best = self._parts[0]
            for part in self._parts[1:]:
                if part.head >= best.head:
                    best = part
            return best

    @property
    def path(self) -> Path:
        return self._current().path

    def head_s(self) -> float:
        return self._current().head if self._parts else 0.0

    # --- YouTube --------------------------------------------------------------

    def _extractor_args(self):
        args = []
        if self.pot_provider:
            args += ["--extractor-args", f"youtubepot-bgutilhttp:base_url={self.pot_provider}"]
        if self.cookies:
            args += ["--cookies", str(self.cookies)]
        return args

    def _info(self):
        import yt_dlp

        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        if self.pot_provider:
            opts["extractor_args"] = {"youtubepot-bgutilhttp": {"base_url": [self.pot_provider]}}
        if self.cookies:
            opts["cookiefile"] = str(self.cookies)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(source.canonical_url(self.video_id), download=False)

    def _resolve_hls(self):
        """The media playlist yt-dlp will record, to check where it begins."""
        formats = [f for f in self._info().get("formats", [])
                   if str(f.get("protocol", "")).startswith("m3u8")
                   and (f.get("height") or 0) <= 1080 and f.get("vcodec", "none") != "none"]
        return max(formats, key=lambda f: f.get("height") or 0)["url"] if formats else None

    @staticmethod
    def _fetch_text(url):
        import httpx

        return httpx.get(url, timeout=30.0).text

    def _still_live(self) -> bool:
        try:
            return self._info().get("live_status") == "is_live"
        except Exception as exc:  # noqa: BLE001 - unknown is not over
            log.warning("could not ask whether %s is still live: %s", self.video_id, exc)
            return True
