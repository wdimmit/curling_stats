"""Watch a live YouTube stream as a practice session, with no API.

    python scripts/practice/live_practice.py VIDEO_ID --minutes 30 --out out/practice/live

The worker's own live side -- manager thread, lookback recorder, lane, practice
watch -- runs in this process against a stand-in API that serves one practice
job, writes every published document under --out, and says stop after
--minutes. It records from YouTube: ask before running it.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def practice_job(video_id, lookback_s):
    return {"id": "p_live", "run_id": "pr_live", "video_id": video_id, "kind": "practice",
            "sheet": None, "title": f"{video_id} practice", "lookback_s": lookback_s}


class FakeApi:
    """The worker endpoints a practice job uses, answered in-process."""

    def __init__(self, job, *, out, stop_after_s, clock=time.monotonic):
        self.job, self.out, self.clock = job, Path(out), clock
        self.video_id = job["video_id"]
        self.stop_at = clock() + stop_after_s
        self.pending, self.count, self.completed = None, 0, None
        (self.out / "publishes").mkdir(parents=True, exist_ok=True)

    def claim(self, worker_id, model_id, gpu, kinds=None):
        job, self.job = self.job, None
        return job

    def progress(self, job_id, worker_id, phase, fraction, message):
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)
        return {"stop": True} if self.clock() >= self.stop_at else None

    def artifacts(self, job_id, worker_id, files, detcache):
        return {"uploads": [{"name": f["name"], "url": f"memory://{f['name']}", "headers": {}}
                            for f in files]}

    def upload(self, url, headers, data):
        self.pending = data

    def publish(self, job_id, worker_id, payload):
        self.count += 1
        (self.out / "publishes" / f"{self.count:03d}.json").write_bytes(self.pending)
        (self.out / "practice.json").write_bytes(self.pending)
        print(f"[{time.strftime('%H:%M:%S')}] published {payload}", flush=True)

    def complete(self, job_id, worker_id, payload):
        self._keep_recording()
        self.completed = payload
        print(f"[{time.strftime('%H:%M:%S')}] complete {payload}", flush=True)

    def fail(self, job_id, worker_id, error, kind, retry_after_s=None):
        self._keep_recording()
        self.completed = {"failed": error}
        print(f"failed: {error}", flush=True)

    def _keep_recording(self):
        """The footage the throws are checked against: the worker deletes a
        practice recording once its job is done, and by then the stream's DVR
        window has moved on."""
        import shutil

        parts = sorted((self.out / "live" / self.video_id).glob("rec.*.ts"),
                       key=lambda p: p.stat().st_size)
        if parts:
            shutil.copyfile(parts[-1], self.out / "recording.ts")


def main(argv=None) -> int:
    from curling_score import weights as weights_mod
    from curling_score.live import lane as lane_mod, manager as manager_mod

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("video_id")
    p.add_argument("--minutes", type=float, default=30.0)
    p.add_argument("--lookback", type=float, default=1200.0)
    p.add_argument("--out", default="out/practice/live")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(a.out)
    api = FakeApi(practice_job(a.video_id, a.lookback), out=out, stop_after_s=60 * a.minutes)
    mgr = manager_mod.LiveManager(api, "practice-check", model_id="local", gpu=None,
                                  root=out, kinds=("practice",), heartbeat_s=30.0).start(poll_s=5.0)
    lane = lane_mod.LiveLane(mgr, api, "practice-check",
                             make_session=lane_mod.video_sessions(str(weights_mod.default_path())))
    try:
        while api.completed is None:
            if not lane.step():
                time.sleep(0.25)
    finally:
        mgr.stop()
    print(json.dumps(api.completed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
