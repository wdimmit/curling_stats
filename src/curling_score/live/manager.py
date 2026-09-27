"""The worker's live bookkeeping: claim live jobs, record them, keep their leases.

Runs on its own thread, because none of it may wait on the GPU: a stream has
to start recording within seconds of its job being claimed, and a live job's
lease has to be renewed every few minutes however long the lane is busy with
another stream's end. It does nothing but HTTP and subprocesses -- the
sessions, which decode video, belong to the lane on the main thread (see
``ingest/frames.py`` for what PyAV on two threads has cost before).
"""

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

MAX_STREAMS = 6        # five sheets at once, and one to spare
POLL_S = 20.0          # how often to look for live jobs and check recorders
HEARTBEAT_S = 60.0     # well inside the API's ten-minute lease


@dataclass
class Stream:
    """One live job in hand: its recorder, and the lane's session for it."""

    job: dict
    recorder: object
    session: object = None
    lost: bool = False
    last_beat: float = 0.0
    last_doc: dict | None = field(default=None, repr=False)


class LiveManager:
    def __init__(self, api, worker_id, *, model_id, gpu, root=None,
                 max_streams: int = MAX_STREAMS, make_recorder=None,
                 heartbeat_s: float = HEARTBEAT_S, clock=time.monotonic):
        self.api, self.worker_id, self.model_id, self.gpu = api, worker_id, model_id, gpu
        self.root = Path(root) if root is not None else None
        self.max_streams, self.heartbeat_s, self.clock = max_streams, heartbeat_s, clock
        self.make_recorder = make_recorder or self._recorder
        self._streams = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _recorder(self, job):
        import os

        from curling_score.live.recorder import YtDlpRecorder

        return YtDlpRecorder(job["video_id"], self.root / "live" / job["video_id"],
                             pot_provider=os.environ.get("YTDLP_POT_PROVIDER"),
                             cookies=os.environ.get("YTDLP_COOKIES"))

    # --- what the lane sees ---------------------------------------------------

    def streams(self) -> list:
        with self._lock:
            return list(self._streams)

    def has_streams(self) -> bool:
        with self._lock:
            return bool(self._streams)

    def finish(self, stream):
        """Let a stream go: stop recording it and delete the recording."""
        stream.recorder.stop()
        with self._lock:
            if stream in self._streams:
                self._streams.remove(stream)
        if self.root is not None:
            shutil.rmtree(self.root / "live" / stream.job["video_id"], ignore_errors=True)

    # --- the thread -----------------------------------------------------------

    def poll_once(self):
        while len(self.streams()) < self.max_streams:
            try:
                job = self.api.claim(self.worker_id, self.model_id, self.gpu, kinds=["live"])
            except Exception as exc:  # noqa: BLE001 - the API may be down; next poll
                log.warning("live claim failed: %s", exc)
                break
            if job is None:
                break
            self._begin(job)
        for stream in self.streams():
            self._keep(stream)

    def _begin(self, job):
        log.info("live job %s: video %s", job["id"], job["video_id"])
        recorder = self.make_recorder(job)
        try:
            recorder.start()
        except LiveError as exc:
            log.warning("live job %s cannot be recorded: %s", job["id"], exc)
            self._fail(job, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds
            log.exception("live job %s: recorder did not start", job["id"])
            self._fail(job, f"{type(exc).__name__}: {exc}", kind="transient")
            return
        with self._lock:
            self._streams.append(Stream(job=job, recorder=recorder, last_beat=self.clock()))

    def _fail(self, job, error, kind="permanent"):
        try:
            self.api.fail(job["id"], self.worker_id, error[:500], kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not report live job %s failing: %s", job["id"], exc)

    def _keep(self, stream):
        try:
            stream.recorder.check()
        except Exception:  # noqa: BLE001 - a bad check must not stop the others
            log.exception("checking the recorder of %s", stream.job["id"])
        if stream.lost or self.clock() - stream.last_beat < self.heartbeat_s:
            return
        head = getattr(stream.recorder, "head_s", lambda: 0.0)()
        ends = sum(len(g["ends"]) for g in (stream.last_doc or {}).get("games", []))
        try:
            self.api.progress(stream.job["id"], self.worker_id, "live", None,
                              f"recorded {head / 60:.0f} min, {ends} end(s) published")
            stream.last_beat = self.clock()
        except Exception as exc:  # noqa: BLE001
            from curling_score.service.worker import Lost

            if isinstance(exc, Lost):
                log.warning("live job %s is no longer ours", stream.job["id"])
                stream.lost = True
            else:
                log.warning("live heartbeat for %s failed: %s", stream.job["id"], exc)

    def start(self, poll_s: float = POLL_S):
        def run():
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001 - the thread must outlive a bad poll
                    log.exception("live manager poll")
                self._stop.wait(poll_s)

        self._thread = threading.Thread(target=run, name="live-manager", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        for stream in self.streams():
            stream.recorder.stop()
