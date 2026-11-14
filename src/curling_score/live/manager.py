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
    """A practice watch on a sheet's cameras, not a league game."""
    return job.get("kind") == "practice"


def is_camera_game(job) -> bool:
    """A league game read from its sheet's cameras instead of its stream."""
    return job.get("kind") == "camera_live"


def is_standby(job) -> bool:
    """A sheet's cameras recorded from its league's schedule start, before
    its stream is found: no clock, no session, until the API hands it the
    game (``LiveManager._hand_over``)."""
    return job.get("kind") == "camera_standby"


def _published_at(job):
    from datetime import datetime

    try:
        return datetime.fromisoformat(job["published_at"]) if job.get("published_at") else None
    except ValueError:
        return None


def _name_video(recorder, video_id: str) -> None:
    """Write the game's video id into a camera recording's directory: the
    clips worker finds a game still being recorded by it, whatever the
    directory is called (a standby's is named for the standby)."""
    directory = getattr(recorder, "dir", None)
    if not directory:
        return
    try:
        (Path(directory) / "video_id").write_text(video_id)
    except OSError as exc:
        log.warning("could not name the recording in %s: %s", directory, exc)


def stream_start_s(job, recorder) -> float:
    """Where in a camera recording its game's stream began: past 0 when the
    cameras were recorded ahead of it (a standby), else 0."""
    began, wall = _published_at(job), getattr(recorder, "wall_started", None)
    if began is None or wall is None:
        return 0.0
    return max(began.timestamp() - wall, 0.0)


@dataclass
class Stream:
    """One live job in hand: its recorder, and the lane's session for it."""

    job: dict
    recorder: object
    session: object = None
    lost: bool = False
    # The API said no session is on this practice watch any more.
    stop_requested: bool = False
    # A practice watch's way to send a throw's clip up: set by the lane.
    send_clip: object = field(default=None, repr=False)
    last_beat: float = 0.0
    # A camera game's clock (live/clock.Aligner), and the YouTube stream
    # recorded beside it when CAMERA_KEEP_YOUTUBE asks.
    aligner: object = None
    reference: object = None
    stream_finished: bool = False
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
                 prune=None, background=_in_background, kinds=("live",), cameras=False,
                 keep_youtube=False, make_aligner=None, cams_root=None):
        """``prune`` is called once a recording has been kept, to hold the
        cache to its budget; ``background`` runs the keeping, which copies
        gigabytes, somewhere other than the lane's thread. ``kinds`` are the
        jobs it claims: league games, and practice where the worker allows.
        ``cameras``: it can see the club's cameras, and says so when it
        claims, which is what a camera practice job waits for. A camera game
        has its clock followed (``make_aligner(job, recorder)``) and, with
        ``keep_youtube``, its stream recorded beside it and kept as any live
        game's recording is. A camera game records under ``cams_root`` (the
        workers' shared place), and one that ended with its stream is kept
        there for reading again."""
        self.api, self.worker_id, self.model_id, self.gpu = api, worker_id, model_id, gpu
        self.kinds = tuple(kinds)
        self.cameras = cameras
        self.root = Path(root) if root is not None else None
        self.max_streams, self.heartbeat_s, self.clock = max_streams, heartbeat_s, clock
        self.make_recorder = make_recorder or self._recorder
        self.keep_youtube = keep_youtube
        self.cams_root = Path(cams_root) if cams_root is not None else None
        self.make_aligner = make_aligner or self._aligner
        self.prune, self.background = prune, background
        self._streams = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _recorder(self, job):
        import os

        if is_practice(job) or is_camera_game(job) or is_standby(job):
            from curling_score.cameras import recorder as cameras

            if (is_camera_game(job) or is_standby(job)) and self.cams_root is not None:
                # Where it is kept from by a rename (the same mount), under
                # this worker's name: the other's start-up never clears it.
                directory = self.cams_root / "live" / self.worker_id / job["video_id"]
            else:
                # In the directory finish() clears: a practice recording is never kept.
                directory = self.root / "live" / job["video_id"]
            return cameras.CameraRecorder(int(job["sheet"]), directory)
        return self._youtube(job["video_id"], self.root / "live" / job["video_id"])

    def _youtube(self, video_id, directory):
        import os

        from curling_score.live import recorder

        return recorder.YtDlpRecorder(video_id, directory,
                                      pot_provider=os.environ.get("YTDLP_POT_PROVIDER"),
                                      cookies=os.environ.get("YTDLP_COOKIES"))

    def _aligner(self, job, recorder):
        import os

        from curling_score.live.clock import Aligner

        # Its waits count from where the stream began: behind a standby, the
        # recording is long already when the stream is found.
        start = stream_start_s(job, recorder)
        return Aligner(job["video_id"], int(job["sheet"]), recorder.dir,
                       recorder.wall_started, _published_at(job),
                       head=lambda: recorder.head_s() - start,
                       pot_provider=os.environ.get("YTDLP_POT_PROVIDER"),
                       cookies=os.environ.get("YTDLP_COOKIES")).start()

    # --- what the lane sees ---------------------------------------------------

    def streams(self) -> list:
        with self._lock:
            return list(self._streams)

    def has_streams(self) -> bool:
        with self._lock:
            return bool(self._streams)

    def finish(self, stream, keep: bool = True, on_kept=None):
        """Let a stream go: stop recording it, and delete the recording --
        after filing it, unless ``keep`` is turned down: a whole recording as
        the video's cached copy, so reprocessing the game needs no download; a
        partial one under ``kept/``, for investigation. The pruner lets either
        go after a week (WORKER_RECORDING_DAYS), or sooner if the disk needs it.
        A practice recording is never kept, however its stream ends: it is a
        window on a sheet's all-day stream, not a game.

        ``on_kept`` is called, on the keeping thread, with the file the
        recording became -- or with the recording itself when it was not filed
        -- before the recording is deleted: the last moment its frames can be
        read. It is not called for a recording that is not kept, and what it
        raises is logged and goes no further."""
        keep = keep and not is_practice(stream.job)
        if stream.aligner is not None:
            stream.aligner.stop()
        self._keep_reference(stream)
        stream.recorder.stop()
        with self._lock:
            if stream in self._streams:
                self._streams.remove(stream)
        if is_camera_game(stream.job) or is_standby(stream.job):
            # A standby never handed its stream is never whole: deleted.
            self._file_camera_game(stream, keep)
            return
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
            kept = None
            try:
                kept = file_it(path, vid, self.root)
                if kept is not None:
                    log.info("kept the %s recording of %s as %s",
                             "whole" if whole else "partial", vid, kept)
            except Exception:  # noqa: BLE001 - only a replay is lost
                log.exception("could not keep the recording of %s", vid)
            finally:
                try:
                    if on_kept is not None:
                        on_kept(kept if kept is not None else path)
                except Exception:  # noqa: BLE001 - only the frames are lost
                    log.exception("could not save the frames of %s", vid)
                finally:
                    shutil.rmtree(directory, ignore_errors=True)
            if self.prune is not None:
                try:
                    self.prune()
                except Exception as exc:  # noqa: BLE001
                    log.warning("prune failed: %s", exc)

        self.background(keep_it)

    def _file_camera_game(self, stream, keep: bool):
        """A camera game that ended with its stream: its four files kept under
        ``cams_root/kept/<video_id>/`` with ``clock.json`` -- the sheet, the
        recording's wall start and the clock it was published on -- for
        reading it again (``camera_vod``). Anything less is deleted."""
        import json
        import os

        rec, vid = stream.recorder, stream.job["video_id"]
        directory = Path(getattr(rec, "dir", "") or "")
        whole = getattr(rec, "whole", lambda: False)()
        if not (keep and whole and self.cams_root is not None and directory.is_dir()):
            if directory.is_dir():
                shutil.rmtree(directory, ignore_errors=True)
            return
        clock = stream.aligner.clock() if stream.aligner is not None else (
            getattr(stream.session, "clock", None))
        for extra in directory.iterdir():
            if extra.suffix != ".ts":
                extra.unlink(missing_ok=True)
        dest = self.cams_root / "kept" / vid
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        from curling_score.cameras import trim

        os.rename(directory, dest)
        # Recorded from the schedule's start (a standby): only the broadcast
        # is kept. Cut on the keeping thread -- it copies gigabytes. Until it
        # is, the clips worker leaves the files alone (trim.PENDING).
        cut = stream_start_s(stream.job, rec)
        meta = {"sheet": int(stream.job["sheet"]), "wall_started": rec.wall_started,
                "clock": clock, "kept_at": time.time()}
        if cut >= trim.MIN_CUT_S:
            meta[trim.PENDING] = True
        (dest / "clock.json").write_text(json.dumps(meta))
        log.info("kept the cameras of %s in %s", vid, dest)

        def trim_then_prune():
            if cut >= trim.MIN_CUT_S:
                try:
                    trim.trim_kept(dest, cut)
                except Exception:  # noqa: BLE001 - kept uncut; pruned in days
                    log.exception("could not cut the kept cameras of %s at %.0f s", vid, cut)
                finally:
                    trim.clear_pending(dest)
            if self.prune is not None:
                self.prune()

        self.background(trim_then_prune)

    def _keep_reference(self, stream):
        """The YouTube stream recorded beside a camera game, stopped and kept
        as any live game's recording is: whole as the video's cached copy."""
        ref, stream.reference = stream.reference, None
        if ref is None:
            return
        ref.stop()
        if self.root is None:
            return
        vid = stream.job["video_id"]
        directory = self.root / "live" / f"{vid}-youtube"
        try:
            path = ref.path
        except (AttributeError, IndexError):
            shutil.rmtree(directory, ignore_errors=True)
            return
        file_it = cache.keep_recording if ref.whole() else cache.keep_partial

        def keep_it():
            try:
                file_it(path, vid, self.root)
            except Exception:  # noqa: BLE001
                log.exception("could not keep the stream beside %s", vid)
            finally:
                shutil.rmtree(directory, ignore_errors=True)

        self.background(keep_it)

    # --- the thread -----------------------------------------------------------

    def poll_once(self):
        while len(self.streams()) < self.max_streams:
            try:
                job = self.api.claim(self.worker_id, self.model_id, self.gpu,
                                     kinds=list(self.kinds),
                                     **({"cameras": True} if self.cameras else {}))
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
        try:
            recorder = self.make_recorder(job)
            recorder.start()
        except LiveError as exc:
            log.warning("live job %s cannot be recorded: %s", job["id"], exc)
            self._fail(job, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds
            log.exception("live job %s: recorder did not start", job["id"])
            self._fail(job, f"{type(exc).__name__}: {exc}", kind="transient")
            return
        stream = Stream(job=job, recorder=recorder, last_beat=self.clock())
        if is_camera_game(job):
            _name_video(recorder, job["video_id"])
            self._follow(stream, job)
        with self._lock:
            self._streams.append(stream)

    def _follow(self, stream, job):
        """A camera game's clock, and its stream recorded beside it when
        CAMERA_KEEP_YOUTUBE asks."""
        stream.aligner = self.make_aligner(job, stream.recorder)
        if self.keep_youtube and self.root is not None:
            try:
                stream.reference = self._youtube(
                    job["video_id"], self.root / "live" / f"{job['video_id']}-youtube")
                stream.reference.start()
            except Exception as exc:  # noqa: BLE001 - the game goes on without it
                log.warning("live job %s: its stream is not recorded beside it: %s",
                            job["id"], exc)
                stream.reference = None

    def _hand_over(self, stream, game: dict):
        """A standby's stream was found: the recording goes on, now the
        game's. Its clock and YouTube copy start, then the job says it is the
        game -- last, so the lane, on its own thread, never sees a camera game
        without them -- and the lane gives it its session."""
        job = {**stream.job, **game, "kind": "camera_live"}
        log.info("live job %s: the stream of sheet %s was found: %s", job["id"],
                 job.get("sheet"), job.get("video_id"))
        try:
            self._follow(stream, job)
        except Exception:  # noqa: BLE001 - recorded still; tried again next heartbeat
            log.exception("live job %s: its clock did not start", job["id"])
            stream.aligner = None
            return
        _name_video(stream.recorder, job["video_id"])
        stream.job = job

    def _fail(self, job, error, kind="permanent"):
        try:
            self.api.fail(job["id"], self.worker_id, error[:500], kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not report live job %s failing: %s", job["id"], exc)

    def _keep(self, stream):
        if stream.reference is not None:
            try:
                stream.reference.check()
            except Exception as exc:  # noqa: BLE001 - only the reference is lost
                log.warning("live job %s: the stream beside it stopped: %s",
                            stream.job["id"], exc)
                self._keep_reference(stream)
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
            # A practice watch nobody is on any more: the API says stop.
            if isinstance(got, dict) and got.get("stop"):
                stream.stop_requested = True
            # A standby whose stream the poll found: it is the game now.
            if isinstance(got, dict) and got.get("game") and is_standby(stream.job):
                self._hand_over(stream, got["game"])
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
