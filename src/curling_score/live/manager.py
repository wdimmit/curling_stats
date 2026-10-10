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

from curling_score.ingest import cache
from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

MAX_STREAMS = 6        # five sheets at once, and one to spare
POLL_S = 20.0          # how often to look for live jobs and check recorders
HEARTBEAT_S = 60.0     # well inside the API's ten-minute lease


def is_practice(job) -> bool:
    """A practice watch on a sheet's stream, not a league game."""
    return job.get("kind") == "practice"


@dataclass
class Stream:
    """One live job in hand: its recorder, and the lane's session for it."""

    job: dict
    recorder: object
    session: object = None
    lost: bool = False
    # The API said no session watches this practice stream any more.
    stop_requested: bool = False
    last_beat: float = 0.0
    last_doc: dict | None = field(default=None, repr=False)
    # The lane's: a document not yet published, unexpected errors in a row,
    # and when a stream in trouble may be tried again.
    pending: dict | None = field(default=None, repr=False)
    failures: int = 0
    retry_at: float = 0.0


def _in_background(fn):
    threading.Thread(target=fn, name="live-keep", daemon=True).start()


class LiveManager:
    def __init__(self, api, worker_id, *, model_id, gpu, root=None,
                 max_streams: int = MAX_STREAMS, make_recorder=None,
                 heartbeat_s: float = HEARTBEAT_S, clock=time.monotonic,
                 prune=None, background=_in_background, kinds=("live",)):
        """``prune`` is called once a recording has been kept, to hold the
        cache to its budget; ``background`` runs the keeping, which copies
        gigabytes, somewhere other than the lane's thread. ``kinds`` are the
        jobs it claims: league games, and practice where the worker allows."""
        self.api, self.worker_id, self.model_id, self.gpu = api, worker_id, model_id, gpu
        self.kinds = tuple(kinds)
        self.root = Path(root) if root is not None else None
        self.max_streams, self.heartbeat_s, self.clock = max_streams, heartbeat_s, clock
        self.make_recorder = make_recorder or self._recorder
        self.prune, self.background = prune, background
        self._streams = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _recorder(self, job):
        import os

        from curling_score.live.recorder import YtDlpRecorder

        lookback = None
        if is_practice(job):
            from curling_score.practice.watch import LOOKBACK_S

            lookback = float(job.get("lookback_s") or LOOKBACK_S)
        return YtDlpRecorder(job["video_id"], self.root / "live" / job["video_id"],
                             pot_provider=os.environ.get("YTDLP_POT_PROVIDER"),
                             cookies=os.environ.get("YTDLP_COOKIES"), lookback_s=lookback)

    # --- what the lane sees ---------------------------------------------------

    def streams(self) -> list:
        with self._lock:
            return list(self._streams)

    def has_streams(self) -> bool:
        with self._lock:
            return bool(self._streams)

    def finish(self, stream, keep: bool = True):
        """Let a stream go: stop recording it, and delete the recording --
        after filing it, unless ``keep`` is turned down: a whole recording as
        the video's cached copy, so reprocessing the game needs no download; a
        partial one under ``kept/``, for investigation. The pruner lets either
        go after a week (WORKER_RECORDING_DAYS), or sooner if the disk needs it.
        A practice recording is never kept, however its stream ends: it is a
        window on a sheet's all-day stream, not a game."""
        keep = keep and not is_practice(stream.job)
        stream.recorder.stop()
        with self._lock:
            if stream in self._streams:
                self._streams.remove(stream)
        if self.root is None:
            return
        vid = stream.job["video_id"]
        directory = self.root / "live" / vid
        whole = getattr(stream.recorder, "whole", lambda: False)()
        try:
            path = stream.recorder.path
        except (AttributeError, IndexError):     # never started: nothing written
            path = None
        if not keep or path is None:
            shutil.rmtree(directory, ignore_errors=True)
            return
        # The whole stream becomes the video's cached copy; anything less is
        # kept beside it for a week, for looking into what went wrong, and is
        # never taken for the game itself.
        file_it = cache.keep_recording if whole else cache.keep_partial

        def keep_it():
            try:
                kept = file_it(path, vid, self.root)
                if kept is not None:
                    log.info("kept the %s recording of %s as %s",
                             "whole" if whole else "partial", vid, kept)
            except Exception:  # noqa: BLE001 - only a replay is lost
                log.exception("could not keep the recording of %s", vid)
            finally:
                shutil.rmtree(directory, ignore_errors=True)
            if self.prune is not None:
                try:
                    self.prune()
                except Exception as exc:  # noqa: BLE001
                    log.warning("prune failed: %s", exc)

        self.background(keep_it)

    # --- the thread -----------------------------------------------------------

    def poll_once(self):
        while len(self.streams()) < self.max_streams:
            try:
                job = self.api.claim(self.worker_id, self.model_id, self.gpu,
                                     kinds=list(self.kinds))
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
        except LiveError as exc:
            # The recording cannot go on (it could not resume on the video's
            # clock): the job fails, and the lane lets the stream go.
            log.warning("live job %s: %s", stream.job["id"], exc)
            self._fail(stream.job, str(exc))
            stream.lost = True
            return
        except Exception:  # noqa: BLE001 - a bad check must not stop the others
            log.exception("checking the recorder of %s", stream.job["id"])
        if stream.lost or self.clock() - stream.last_beat < self.heartbeat_s:
            return
        head = getattr(stream.recorder, "head_s", lambda: 0.0)()
        ends = sum(len(g["ends"]) for g in (stream.last_doc or {}).get("games", []))
        try:
            got = self.api.progress(stream.job["id"], self.worker_id, "live", None,
                                    f"recorded {head / 60:.0f} min, {ends} end(s) published")
            stream.last_beat = self.clock()
            # A practice stream nobody watches any more: the API says stop.
            if isinstance(got, dict) and got.get("stop"):
                stream.stop_requested = True
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
