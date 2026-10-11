"""The clips worker: cuts clips of the rocks people say they threw.

A service of its own beside the main workers (deploy/docker-compose.worker.yml
`clips`). Each main worker keeps its own cache, so a game's video is wherever
the worker that ran it keeps it; this one reads all of them, read-only, and
writes only to a scratch of its own. It loads no model, starts no live lane
and never talks to YouTube: a game whose video has left every cache gets no
clips (`video_gone`), not a download.
"""

import logging
import time
from pathlib import Path

from curling_score.game import clips as game_clips
from curling_score.service.worker import Lost

log = logging.getLogger("curling_score.clipworker")

POLL_IDLE_S = 30.0    # a clip job is asked for when a play is saved: seldom
POLL_BUSY_S = 2.0     # after one, look again soon: a report opened asks for several


def sources_from_env(value: str | None) -> list[Path]:
    """``WORKER_CLIP_SOURCES``: the caches to look in, colon-separated."""
    return [Path(p) for p in (value or "").split(":") if p]


def process(job: dict, api, worker_id: str, *, sources, workdir, cut=None) -> dict:
    """Cut, upload and report one clip job's rocks; what came of each."""
    rocks = job["spec"].get("rocks", [])
    video = game_clips.find_video(job["video_id"], sources)
    if video is None:
        results = {r["name"]: {"clip": None, "reason": "video_gone"} for r in rocks}
        api.clip_complete(job["id"], worker_id, results)
        return results
    sideviews = game_clips.sideviews_from_calibration(job["spec"].get("calibration"))

    def deliver(name, path):
        data = Path(path).read_bytes()
        try:
            plan = api.clip_artifacts(job["id"], worker_id,
                                      [{"name": f"{name}.mp4", "bytes": len(data)}])
            for up in plan["uploads"]:
                api.upload(up["url"], up["headers"], data)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - that rock goes without a clip
            log.warning("clip job %s: %s did not upload (%s)", job["id"], name, exc)
            return False
        return True

    def on_rock(name, result):
        try:
            api.clip_progress(job["id"], worker_id)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - the lease has minutes in hand
            log.warning("clip job %s: progress not delivered (%s)", job["id"], exc)

    kw = {} if cut is None else {"cut": cut}
    results = game_clips.clip_rocks(rocks, video, sideviews, Path(workdir) / job["id"],
                                    deliver, on_rock=on_rock, **kw)
    api.clip_complete(job["id"], worker_id, results)
    return results


def run_forever(api, worker_id: str, *, sources, workdir, sleep=time.sleep, once=False,
                cut=None):
    """Claim and cut clip jobs until stopped."""
    log.info("clips worker %s: caches %s, scratch %s", worker_id,
             [str(s) for s in sources], workdir)
    while True:
        try:
            job = api.claim_clips(worker_id)
        except Exception as exc:  # noqa: BLE001 - the API may be down; keep trying
            log.warning("clips claim failed: %s", exc)
            job = None
        if job is None:
            if once:
                return
            sleep(POLL_IDLE_S)
            continue
        try:
            got = process(job, api, worker_id, sources=sources, workdir=workdir, cut=cut)
            log.info("clip job %s: %d of %d rocks clipped", job["id"],
                     sum(1 for r in got.values() if r.get("clip")), len(got))
        except Lost as exc:
            log.warning("clip job %s lost: %s", job["id"], exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("clip job %s failed", job["id"])
            try:
                api.clip_fail(job["id"], worker_id, f"{type(exc).__name__}: {exc}"[:500])
            except Exception as exc2:  # noqa: BLE001 - the lease will return it
                log.warning("could not report clip job %s failing: %s", job["id"], exc2)
        if once:
            return
        sleep(POLL_BUSY_S)
