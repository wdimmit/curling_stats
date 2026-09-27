"""The GPU lane's live side: step the sessions, publish what they build.

On a league night five sheets each settle an end every ~15 minutes, and one
end takes ~100 s of GPU. The lane builds the end that has waited longest
first, whichever stream it belongs to, and fills the gaps with the cheap
work -- calibrating, following the profiles, finishing a stream that has
ended. Everything here runs on the main thread, the only one that decodes.
"""

import json
import logging

from curling_score.live.session import LiveError

log = logging.getLogger(__name__)


def _games(doc):
    return [{"index": g["index"], "start_s": g["start_s"], "end_s": g["end_s"],
             "ends": len(g["ends"])} for g in doc["games"]]


class LiveLane:
    def __init__(self, manager, api, worker_id, *, make_session):
        self.manager, self.api, self.worker_id = manager, api, worker_id
        self.make_session = make_session

    def busy(self) -> bool:
        return self.manager.has_streams()

    def step(self) -> bool:
        """Do one unit of live work; False when there was nothing to do."""
        from curling_score.service.worker import Lost

        streams = self.manager.streams()
        for stream in streams:
            if stream.lost:
                log.warning("live job %s was taken away; letting it go", stream.job["id"])
                self.manager.finish(stream)
                return True
            if stream.session is None:
                stream.session = self.make_session(
                    stream, lambda doc, s=stream: self._publish(s, doc))
        due = [(stream.session.next_end_due(), i, stream) for i, stream in enumerate(streams)]
        order = ([s for d, _, s in sorted(x for x in due if x[0] is not None)]
                 + [s for d, _, s in due if d is None])
        for stream in order:
            try:
                did = stream.session.step()
            except LiveError as exc:
                log.warning("live job %s cannot go on: %s", stream.job["id"], exc)
                try:
                    self.api.fail(stream.job["id"], self.worker_id, str(exc)[:500], "permanent")
                except Exception as exc2:  # noqa: BLE001
                    log.warning("could not report it: %s", exc2)
                self.manager.finish(stream)
                return True
            except Lost:
                log.warning("live job %s is no longer ours", stream.job["id"])
                self.manager.finish(stream)
                return True
            if stream.session.done:
                self._complete(stream)
                return True
            if did:
                return True
        return False

    def _publish(self, stream, doc):
        job = stream.job
        data = json.dumps(doc).encode()
        plan = self.api.artifacts(job["id"], self.worker_id,
                                  [{"name": "timeline.json", "bytes": len(data)}], [])
        for up in plan["uploads"]:
            self.api.upload(up["url"], up["headers"], data)
        self.api.publish(job["id"], self.worker_id, {
            "games": _games(doc), "title": job.get("title"), "sheet": job.get("sheet"),
            "duration_s": doc["live"]["recorded_s"]})
        stream.last_doc = doc
        log.info("live job %s: published %s end(s)", job["id"],
                 [len(g["ends"]) for g in doc["games"]])

    def _complete(self, stream):
        from curling_score.service.worker import Lost

        job, doc = stream.job, stream.last_doc or {"games": [], "live": {"recorded_s": 0}}
        try:
            self.api.complete(job["id"], self.worker_id, {
                "games": _games(doc), "title": job.get("title"),
                "format": job.get("format") or "fours", "sheet": job.get("sheet"),
                "duration_s": doc["live"]["recorded_s"]})
            log.info("live job %s complete", job["id"])
        except Lost:
            log.warning("live job %s was taken away before it completed", job["id"])
        except Exception:  # noqa: BLE001 - the lease returns it; the next claim redoes it
            log.exception("live job %s: completing failed", job["id"])
        self.manager.finish(stream)


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
        return live.LiveSession(
            video_id=job["video_id"], url=source.canonical_url(job["video_id"]),
            recording=stream.recorder, fmt=format_mod.by_name(job.get("format") or "fours"),
            pipeline=live.VideoPipeline(weights=weights, skip_longview=skip_longview,
                                        line=models.line_model is not None,
                                        progress=progress),
            sheet=job.get("sheet"), models=models, publish=publish, progress=progress)

    return make
