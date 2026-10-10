"""The GPU lane's live side: step the sessions, publish what they build.

On a league night five sheets each settle an end every ~15 minutes, and one
end takes ~100 s of GPU. The lane builds the end that has waited longest
first, whichever stream it belongs to, and fills the gaps with the cheap
work -- calibrating, following the profiles, finishing a stream that has
ended. Everything here runs on the main thread, the only one that decodes.
"""

import json
import logging
import time

from curling_score import timeline
from curling_score.ingest import source
from curling_score.live.manager import is_practice
from curling_score.live.session import LiveError

log = logging.getLogger(__name__)

# A stream's unexpected errors in a row before its job is failed, and how long
# a stream in trouble -- or whose publish did not go through -- is left before
# it is tried again. One stream's trouble never stops the others.
MAX_FAILURES = 3
RETRY_S = 60.0


def _games(doc):
    return [timeline.game_summary(g) for g in doc["games"]]


def _sheet(job):
    return job.get("sheet") or source.sheet_from_title(job.get("title"))


def _practice_summary(doc) -> dict:
    return {"status": doc["status"], "recorded_s": doc["recorded_s"],
            "throws": len(doc["throws"])}


class LiveLane:
    def __init__(self, manager, api, worker_id, *, make_session, clock=time.monotonic):
        self.manager, self.api, self.worker_id = manager, api, worker_id
        self.make_session, self.clock = make_session, clock
        self._turn = 0

    def busy(self) -> bool:
        return self.manager.has_streams()

    def step(self) -> bool:
        """Do one unit of live work; False when there was nothing to do.

        Nothing a stream does can raise out of here: a LiveError fails that
        stream's job, a lost job is let go, and anything else backs that one
        stream off for RETRY_S and fails it only after MAX_FAILURES in a row.
        """
        now = self.clock()
        streams = self.manager.streams()
        for stream in streams:
            if stream.lost:
                log.warning("live job %s was taken away; letting it go", stream.job["id"])
                self.manager.finish(stream)
                return True
        ready = [s for s in streams if s.retry_at <= now]
        for stream in ready:
            if stream.session is None:
                try:
                    stream.session = self.make_session(
                        stream, lambda doc, s=stream: self._publish(s, doc))
                except Exception as exc:  # noqa: BLE001
                    return self._trouble(stream, exc)
        # A document that did not go out goes before anything new is built.
        for stream in ready:
            if stream.pending is not None:
                return self._resend(stream)
        # Practice first: a card is due seconds after a stone stops, an end
        # minutes after it closes. A watch with nothing to read says so, and
        # one step reads at most STEP_MAX_S, so league ends are never starved.
        for stream in ready:
            if not is_practice(stream.job):
                continue
            try:
                if stream.stop_requested:
                    stream.session.request_stop()
                did = stream.session.step()
            except Exception as exc:  # noqa: BLE001
                return self._trouble(stream, exc)
            stream.failures = 0
            if stream.session.done:
                return self._complete(stream)
            if did:
                return True
        ready = [s for s in ready if not is_practice(s.job)]
        first, due, idle = [], [], []
        for i, stream in enumerate(ready):
            try:
                if stream.session.needs_calibration():
                    first.append(stream)
                    continue
                when = stream.session.next_end_due()
            except Exception as exc:  # noqa: BLE001
                return self._trouble(stream, exc)
            (due if when is not None else idle).append((when, i, stream))
        # A stream waiting for its first calibration goes first: it has no end
        # due until it has one, so behind the others' ends it would wait for
        # ever (the rehearsal found exactly that). Then the end that has waited
        # longest; the rest take turns, so a stream whose work keeps failing
        # cannot always go first.
        if idle:
            k = self._turn % len(idle)
            idle = idle[k:] + idle[:k]
            self._turn += 1
        for stream in first + [s for *_, s in sorted(due)] + [s for *_, s in idle]:
            if stream.session.done:
                return self._complete(stream)
            try:
                did = stream.session.step()
            except Exception as exc:  # noqa: BLE001
                return self._trouble(stream, exc)
            stream.failures = 0
            if stream.session.done:
                return self._complete(stream)
            if did:
                return True
        return False

    # --- when things go wrong ---------------------------------------------------

    def _trouble(self, stream, exc) -> bool:
        from curling_score.service.worker import Lost

        job = stream.job["id"]
        if isinstance(exc, LiveError):
            log.warning("live job %s cannot go on: %s", job, exc)
            self._fail(stream, str(exc))
        elif isinstance(exc, Lost):
            log.warning("live job %s is no longer ours", job)
            self.manager.finish(stream)
        else:
            stream.failures += 1
            log.error("live job %s: %s: %s (%d in a row)", job, type(exc).__name__, exc,
                      stream.failures, exc_info=exc)
            if stream.failures >= MAX_FAILURES:
                self._fail(stream, f"{type(exc).__name__}: {exc}")
            else:
                stream.retry_at = self.clock() + RETRY_S
        return True

    def _fail(self, stream, error):
        try:
            self.api.fail(stream.job["id"], self.worker_id, error[:500], "permanent")
        except Exception as exc:  # noqa: BLE001 - the lease returns it
            log.warning("could not report live job %s failing: %s", stream.job["id"], exc)
        self.manager.finish(stream)

    # --- publishing -------------------------------------------------------------

    def _publish(self, stream, doc):
        """The session's publish callback. A document that cannot be sent now
        is kept and sent again; only a lost job is raised to the session."""
        stream.pending = doc
        self._send(stream)

    def _send(self, stream) -> bool:
        from curling_score.service.worker import Lost

        job, doc = stream.job, stream.pending
        if is_practice(job):
            name = "practice.json"
            summary = {"practice": _practice_summary(doc), "sheet": _sheet(job)}
        else:
            name = "timeline.json"
            summary = {"games": _games(doc), "title": job.get("title"),
                       "sheet": _sheet(job), "duration_s": doc["live"]["recorded_s"]}
        try:
            data = json.dumps(doc).encode()
            plan = self.api.artifacts(job["id"], self.worker_id,
                                      [{"name": name, "bytes": len(data)}], [])
            for up in plan["uploads"]:
                self.api.upload(up["url"], up["headers"], data)
            self.api.publish(job["id"], self.worker_id, summary)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - the API may be down; send it again
            log.warning("live job %s: publish did not go through (%s); trying again",
                        job["id"], exc)
            stream.retry_at = self.clock() + RETRY_S
            return False
        stream.pending, stream.last_doc = None, doc
        if is_practice(job):
            log.info("practice job %s: published %d throw(s)", job["id"], len(doc["throws"]))
        else:
            log.info("live job %s: published %s end(s)", job["id"],
                     [len(g["ends"]) for g in doc["games"]])
        return True

    def _resend(self, stream) -> bool:
        from curling_score.service.worker import Lost

        try:
            self._send(stream)
        except Lost:
            log.warning("live job %s is no longer ours", stream.job["id"])
            self.manager.finish(stream)
        return True

    def _complete(self, stream) -> bool:
        """The finished stream's job, completed -- tried again until the API
        takes it, since letting the stream go first would lose the game."""
        from curling_score.service.worker import Lost

        if stream.pending is not None:
            return self._resend(stream)       # the final document goes first
        if is_practice(stream.job):
            return self._complete_practice(stream)
        job, doc = stream.job, stream.last_doc or {"games": [], "live": {"recorded_s": 0}}
        try:
            self.api.complete(job["id"], self.worker_id, {
                "games": _games(doc), "title": job.get("title"),
                "format": job.get("format") or "fours", "sheet": _sheet(job),
                "duration_s": doc["live"]["recorded_s"]})
        except Lost:
            log.warning("live job %s was taken away before it completed", job["id"])
        except Exception as exc:  # noqa: BLE001 - try again shortly
            log.warning("live job %s: completing did not go through (%s)", job["id"], exc)
            stream.retry_at = self.clock() + RETRY_S
            return True
        else:
            log.info("live job %s complete", job["id"])
        # A sheet nobody played has nothing to reprocess: its recording goes.
        self.manager.finish(stream, keep=bool(doc["games"]))
        return True

    def _complete_practice(self, stream) -> bool:
        """A practice watch that has ended: its job completed with a summary.
        Its recording is a window on a stream that runs all day, not a game,
        so it is not kept."""
        from curling_score.service.worker import Lost

        job, doc = stream.job, stream.last_doc
        summary = (_practice_summary(doc) if doc is not None
                   else {"status": "ended", "recorded_s": 0.0, "throws": 0})
        try:
            self.api.complete(job["id"], self.worker_id,
                              {"practice": summary, "sheet": _sheet(job)})
        except Lost:
            log.warning("practice job %s was taken away before it completed", job["id"])
        except Exception as exc:  # noqa: BLE001 - try again shortly
            log.warning("practice job %s: completing did not go through (%s)", job["id"], exc)
            stream.retry_at = self.clock() + RETRY_S
            return True
        self.manager.finish(stream, keep=False)
        return True


def video_sessions(weights, *, imgsz: int = 448, device=None,
                   skip_longview: bool = False, progress=log.info):
    """A ``make_session`` for the lane that processes real video.

    The models are loaded once, on the first stream, and shared by every
    session after: five copies of the detectors would not fit on the GPU.
    """
    from curling_score import analyze
    from curling_score.game import format as format_mod
    from curling_score.ingest import source
    from curling_score.live import session as live

    loaded = []

    def make(stream, publish):
        if not loaded:
            detector, broom_model, line_model = analyze.load_models(
                weights, imgsz, device, skip_longview=skip_longview, skip_line=False,
                progress=progress)
            loaded.append(live.Models(detector=detector, broom_model=broom_model,
                                      line_model=line_model))
        models, job = loaded[0], stream.job
        if is_practice(job):
            import time as _time

            from curling_score.practice.pipeline import PracticePipeline
            from curling_score.practice.watch import LOOKBACK_S, PracticeWatch

            # Until the API passes the session's Start (phase 3), Start is when
            # the recorder began: as far behind the live edge as it actually
            # got (a young stream gives what it has), by the recorder's own
            # wall clock -- the lane may build this session minutes later.
            got = getattr(stream.recorder, "lookback_got_s", None)
            lookback = float(got or job.get("lookback_s") or LOOKBACK_S)
            began = getattr(stream.recorder, "wall_started", None) or _time.time()
            return PracticeWatch(
                recording=stream.recorder,
                pipeline=PracticePipeline(weights=weights, skip_longview=skip_longview,
                                          line=models.line_model is not None,
                                          progress=progress),
                models=models, since_s=lookback, wall_t0=began - lookback,
                publish=publish, progress=progress)
        return live.LiveSession(
            video_id=job["video_id"], url=source.canonical_url(job["video_id"]),
            recording=stream.recorder, fmt=format_mod.by_name(job.get("format") or "fours"),
            pipeline=live.VideoPipeline(weights=weights, skip_longview=skip_longview,
                                        line=models.line_model is not None,
                                        progress=progress),
            sheet=_sheet(job), models=models, publish=publish, progress=progress)

    return make
