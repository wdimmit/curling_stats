"""DFW's live stream processed live against a local API you can watch: `live_e2e.py`, served.

    python live_local.py VIDEO_ID NORM_DIR --port 8799 --base http://100.80.66.81:8799 \\
        --title "..." [--format fours]

The API runs in this process (memory repo and store) and is served on
``--port`` for a browser; the worker's live lane talks to the same app in
process. One live job is made for VIDEO_ID. Its recording is not YouTube's: it
is ``NORM_DIR/norm.ts``, the three-panel copy `normalize_live.py` writes as
the stream is recorded (DFW's broadcast cuts between layouts, which nothing
downstream reads). `FollowRecorder` hands the lane that file, how far it
reaches (``norm.head``) and whether it is over (``norm.done``).

The session calibrates by the dallas profile and runs the weights in the
environment (CURLING_SCORE_WEIGHTS / CURLING_SCORE_SIDE_WEIGHTS: dfw1a,
dfw2a). The game is at /g/<source id>/ once its first end is published; the
id is logged and written to NORM_DIR/game_url. Every publish is logged to
NORM_DIR/publishes.jsonl and the latest timeline to NORM_DIR/live-timeline.json.
State lives in this process: stop it and the served game is gone (the
recordings are not).
"""

import argparse
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from curling_score import clubs, version
from curling_score import weights as weights_mod
from curling_score.live import lane as lane_mod, manager as manager_mod
from curling_score.service.api import Settings, create_app
from curling_score.service.records import Job, Run
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore, timeline_key
from curling_score.service.worker import ApiClient
from curling_score.service.youtube import FakeYouTube, VideoMeta

log = logging.getLogger("live_local")


class FollowRecorder:
    """A recording another process writes: `YtDlpRecorder`'s interface over a
    growing file, its head and its end read from the files beside it."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.path = self.dir / "norm.ts"

    def start(self):
        return self

    def check(self):
        pass

    def head_s(self) -> float:
        try:
            return float((self.dir / "norm.head").read_text())
        except (OSError, ValueError):
            return 0.0

    def ended(self) -> bool:
        return (self.dir / "norm.done").exists()

    def whole(self) -> bool:
        return False       # never filed as the video's cached copy: the raw recording is kept apart

    def stop(self):
        pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("video_id")
    p.add_argument("norm_dir", type=Path)
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--base", required=True, help="the URL a browser reaches --port at")
    p.add_argument("--title", default=None)
    p.add_argument("--format", default="fours")
    p.add_argument("--club", default="dallas")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    club = clubs.CLUBS[a.club]
    weights = str(weights_mod.default_path(club))
    repo, store, yt = MemoryRepo(), MemoryStore(), FakeYouTube()
    settings = Settings(public_base_url=a.base, worker_token="w", admin_token="a",
                        model_id=version.model_id(weights))
    app = create_app(repo, store, yt, settings)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=a.port, log_level="warning"))
    threading.Thread(target=server.run, name="api", daemon=True).start()

    now = datetime.now(timezone.utc)
    vid, title = a.video_id, a.title or a.video_id
    yt.add(VideoMeta(vid, title, "UCle7XmcABZIpyveRduQH10w", 0.0, "live", now))
    repo.put_run(Run(id=f"r_{vid}", video_id=vid, status="queued", created_at=now,
                     processing_version=settings.processing_version, kind="live",
                     title=title, format=a.format, sheet=None))
    repo.put_job(Job(id=f"j_{vid}", run_id=f"r_{vid}", state="queued", created_at=now,
                     run_after=now, kind="live"))

    def client():
        # In process, but under the served base: the worker sends its token
        # only to upload URLs under its own base, and the API makes them
        # from public_base_url.
        return ApiClient(a.base, "w", http=TestClient(app, base_url=a.base))

    mgr = manager_mod.LiveManager(client(), "dfw-live", model_id=settings.model_id, gpu=None,
                                  root=None, max_streams=1,
                                  make_recorder=lambda job: FollowRecorder(a.norm_dir)).start(poll_s=5.0)
    lane = lane_mod.LiveLane(mgr, client(), "dfw-live", make_session=lane_mod.video_sessions(
        weights, progress=lambda m: log.info("%s", m), club=club))

    original, said = lane._publish, []

    def publish(stream, doc):
        original(stream, doc)
        src = next(iter(repo.sources_for_video(vid)), None)
        if src is not None and not said:
            url = f"{a.base}/g/{src.id}/"
            (a.norm_dir / "game_url").write_text(url + "\n")
            log.info("GAME %s", url)
            said.append(url)
        ends = [len(g["ends"]) for g in doc["games"]]
        rec = {"wall": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "head_s": round(stream.recorder.head_s(), 1), "ends": ends,
               "final": not doc["live"]["in_progress"]}
        log.info("PUBLISH %s", json.dumps(rec))
        with open(a.norm_dir / "publishes.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        (a.norm_dir / "live-timeline.json").write_text(json.dumps(doc))

    lane._publish = publish
    next_report = 0.0
    try:
        while True:
            t = time.monotonic()
            if t >= next_report:
                next_report = t + 60.0
                for st in mgr.streams():
                    log.info("STREAM head %.1f s, session %s", st.recorder.head_s(),
                             "none" if st.session is None else
                             ("calibrated" if st.session.calibration else "uncalibrated"))
            run = repo.get_run(f"r_{vid}")
            if run.status in ("ready", "failed") and not lane.busy():
                log.info("run %s: %s %s", run.id, run.status, run.error or "")
                break
            if not (lane.busy() and lane.step()):
                time.sleep(2.0)
    finally:
        mgr.stop()
    data = store.get_bytes(timeline_key(vid, f"r_{vid}"))
    if data:
        (a.norm_dir / "final-timeline.json").write_bytes(data)
    log.info("finished; the API keeps serving until this process is stopped")
    while True:          # keep serving the finished game
        time.sleep(3600)


if __name__ == "__main__":
    main()
