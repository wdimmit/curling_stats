"""The whole live path in one process: the API, and the worker's live lane.

    python scripts/live_e2e.py --out DIR --youtube VIDEO_ID [...]
    python scripts/live_e2e.py --out DIR --replay VIDEO.mp4 [...] --speed 1

The API runs in-process (memory repo and store, LIVE_ENABLED) and the worker
talks to it through FastAPI's test client, so nothing listens on a port. Each
video becomes a queued live job; the worker's manager claims and records it
(from YouTube, or a cached video played back as if live) and the lane builds
and publishes its ends. It reports, per publish, how long after the end's own
finish it went out and what the game page served at that moment, how busy the
lane was, and writes every run's final timeline to DIR.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from curling_score import version
from curling_score import weights as weights_mod
from curling_score.live import lane as lane_mod, manager as manager_mod
from curling_score.live import replay, session as live_session
from curling_score.service.api import Settings, create_app
from curling_score.service.records import Job, Run
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore, timeline_key
from curling_score.service.worker import ApiClient
from curling_score.service.youtube import FakeYouTube, VideoMeta

log = logging.getLogger("live_e2e")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--youtube", nargs="*", default=[])
    p.add_argument("--replay", nargs="*", default=[])
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--format", default="fours")
    p.add_argument("--timeout", type=float, default=6 * 3600)
    p.add_argument("--max-streams", type=int, default=6)
    p.add_argument("--calib-first", type=float, default=None,
                   help="seconds of footage before the first calibration (default the session's)")
    p.add_argument("--no-longview", action="store_true")
    p.add_argument("--any-window", action="store_true",
                   help="record a YouTube stream even if its window has moved on "
                        "(a mechanics check on a stream that is not a game)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.calib_first is not None:
        live_session.CALIB_FIRST_S = args.calib_first

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = str(weights_mod.default_path())
    repo, store, yt = MemoryRepo(), MemoryStore(), FakeYouTube()
    settings = Settings(public_base_url="http://testserver", worker_token="w",
                        admin_token="a", model_id=version.model_id(weights),
                        live_enabled=True)
    app = create_app(repo, store, yt, settings)
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)

    videos = [(v, None) for v in args.youtube] + [(Path(r).stem, Path(r)) for r in args.replay]
    for i, (vid, _) in enumerate(videos):
        yt.add(VideoMeta(vid, f"{vid} - Sheet {i + 1}", "UCclub", 0.0, "live", now))
        repo.put_run(Run(id=f"r_{vid}", video_id=vid, status="queued", created_at=now,
                         processing_version=settings.processing_version, kind="live",
                         title=f"{vid} - Sheet {i + 1}", format=args.format, sheet=i + 1))
        repo.put_job(Job(id=f"j_{vid}", run_id=f"r_{vid}", state="queued", created_at=now,
                         run_after=now, kind="live"))
    sources = {vid: src for vid, src in videos}

    def make_recorder(job):
        vid = job["video_id"]
        if sources[vid] is None:
            from curling_score.live.recorder import YtDlpRecorder

            import os
            return YtDlpRecorder(vid, out / "live" / vid,
                                 pot_provider=os.environ.get("YTDLP_POT_PROVIDER"),
                                 require_first_segment=not args.any_window)
        return replay.ReplayRecording(sources[vid], out / "live" / vid / "rec.ts",
                                      speed=args.speed)

    def client():
        return ApiClient("http://testserver", "w", http=TestClient(app))

    mgr = manager_mod.LiveManager(client(), "e2e", model_id=settings.model_id, gpu=None,
                                  root=None, max_streams=args.max_streams,
                                  make_recorder=make_recorder).start(poll_s=5.0)
    lane = lane_mod.LiveLane(mgr, client(), "e2e", make_session=lane_mod.video_sessions(
        weights, skip_longview=args.no_longview, progress=lambda m: log.info("%s", m)))
    viewer = TestClient(app)

    publishes, busy_s, heads = [], 0.0, {}
    original = lane._publish

    def publish(stream, doc):
        original(stream, doc)
        wall = time.monotonic() - t0
        vid = stream.job["video_id"]
        stream_clock = wall * args.speed if sources[vid] is not None else stream.recorder.head_s()
        latest = doc["games"][-1]["ends"][-1] if doc["games"] and doc["games"][-1]["ends"] else None
        served = None
        src = next(iter(repo.sources_for_video(vid)), None)
        if src is not None:
            r = viewer.get(f"/g/{src.id}/timeline.json")
            served = (r.status_code, [len(g["ends"]) for g in r.json()["games"]]
                      if r.status_code == 200 else None)
        rec = {"video": vid, "wall_s": round(wall, 1), "stream_s": round(stream_clock, 1),
               "ends": [len(g["ends"]) for g in doc["games"]],
               "end_s": latest["end_s"] if latest else None,
               "lag_s": round(stream_clock - latest["end_s"], 1) if latest else None,
               "final": not doc["live"]["in_progress"], "served": served}
        publishes.append(rec)
        log.info("PUBLISH %s", json.dumps(rec))
        with open(out / "publishes.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    lane._publish = publish
    deadline = t0 + args.timeout
    next_report = t0
    try:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_report:
                next_report += 30.0
                for st in mgr.streams():
                    log.info("STREAM %s head %.1f s, session %s", st.job["video_id"],
                             st.recorder.head_s(), "none" if st.session is None else
                             ("calibrated" if st.session.calibration else "uncalibrated"))
            runs = [repo.get_run(f"r_{vid}") for vid, _ in videos]
            if all(r.status in ("ready", "failed") for r in runs) and not lane.busy():
                break
            if lane.busy():
                s = time.monotonic()
                did = lane.step()
                if did:
                    busy_s += time.monotonic() - s
                else:
                    time.sleep(2.0)
            else:
                time.sleep(2.0)
    finally:
        mgr.stop()
    wall = time.monotonic() - t0
    summary = {"wall_s": round(wall, 1), "lane_busy_s": round(busy_s, 1),
               "lane_busy_fraction": round(busy_s / wall, 3) if wall else None,
               "runs": {}}
    for vid, _ in videos:
        run = repo.get_run(f"r_{vid}")
        job = repo.job_for_run(run.id)
        summary["runs"][vid] = {"status": run.status, "error": run.error or job.error,
                                "games": run.games}
        data = store.get_bytes(timeline_key(vid, run.id))
        if data:
            (out / f"live-{vid}.json").write_bytes(data)
    lags = [p["lag_s"] for p in publishes if p["lag_s"] is not None and not p["final"]]
    if lags:
        lags.sort()
        summary["lag_s"] = {"median": lags[len(lags) // 2], "max": lags[-1], "n": len(lags)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
