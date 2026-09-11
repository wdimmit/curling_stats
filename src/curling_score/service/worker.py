"""The processing worker: the one machine that touches video bytes.

Runs at home, on a residential connection, behind no open ports. It asks the
API for work, does it with the ordinary pipeline, uploads the results straight
to the bucket, and tells the API it is done. Everything it needs to be
restarted safely is already true of the pipeline: caches make a repeated run
cheap, and the API's lease makes an abandoned one reappear.
"""

import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

from curling_score import analyze as analyze_mod, version
from curling_score.ingest import cache, prune, source

log = logging.getLogger("curling_score.worker")

POLL_BUSY_S = 10.0    # after a job, look again soon: a league night queues five
POLL_IDLE_S = 30.0    # otherwise stay well inside Firestore's free read quota
HEARTBEAT_S = 60.0    # how often an idle worker says it is alive
PROGRESS_MIN_GAP_S = 5.0
UPLOAD_TIMEOUT_S = 600.0


class Lost(Exception):
    """The API says this job is no longer ours; stop working on it."""


class ApiClient:
    def __init__(self, base_url: str, token: str, http=None):
        import httpx

        self.base = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=60.0)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _post(self, path, payload):
        r = self._http.post(f"{self.base}{path}", json=payload, headers=self._headers)
        if r.status_code == 409:
            raise Lost(r.text)
        r.raise_for_status()
        return r.json() if r.content else None

    def claim(self, worker_id, model_id, gpu):
        r = self._http.post(f"{self.base}/api/worker/claim", headers=self._headers,
                            json={"worker_id": worker_id, "version": version.PIPELINE_VERSION,
                                  "model_id": model_id, "gpu": gpu})
        if r.status_code == 204:
            return None
        if r.status_code == 409:
            raise RuntimeError(f"model mismatch: {r.text}")
        r.raise_for_status()
        return r.json()["job"]

    def progress(self, job_id, worker_id, phase, fraction, message):
        return self._post(f"/api/worker/jobs/{job_id}/progress",
                          {"worker_id": worker_id, "phase": phase,
                           "fraction": fraction, "message": message})

    def artifacts(self, job_id, worker_id, files, detcache):
        return self._post(f"/api/worker/jobs/{job_id}/artifacts",
                          {"worker_id": worker_id, "files": files, "detcache": detcache})

    def upload(self, url, headers, data: bytes):
        if url.startswith("memory://"):
            return  # a test store; the test writes the bytes itself
        # A signed bucket URL carries its own authorisation; an upload routed
        # back through our API needs the worker token.
        if url.startswith(self.base):
            headers = {**headers, **self._headers}
        r = self._http.put(url, content=data, headers=headers, timeout=UPLOAD_TIMEOUT_S)
        r.raise_for_status()

    def complete(self, job_id, worker_id, payload):
        return self._post(f"/api/worker/jobs/{job_id}/complete",
                          {"worker_id": worker_id, **payload})

    def fail(self, job_id, worker_id, error, kind, retry_after_s=None):
        return self._post(f"/api/worker/jobs/{job_id}/fail",
                          {"worker_id": worker_id, "error": error, "kind": kind,
                           "retry_after_s": retry_after_s})


def classify_error(exc: BaseException) -> str:
    """Which kind of failure this is, which decides whether and when to retry."""
    if isinstance(exc, cache.BlockedError):
        return "blocked"
    text = f"{type(exc).__name__}: {exc}".lower()
    permanent = ("private video", "video unavailable", "removed", "no panels",
                 "could not find two panels", "members-only", "no youtube video id",
                 "not a youtube")
    if any(p in text for p in permanent):
        return "permanent"
    return "transient"


def _snapshot(det_dir: Path) -> dict:
    if not det_dir.is_dir():
        return {}
    return {p.name: p.stat().st_mtime_ns for p in det_dir.glob("*.npz")}


def gpu_name() -> str | None:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        pass
    return None


def process_job(job: dict, api: ApiClient, worker_id: str, *, root: Path,
                weights: str | None, out_dir: Path, analyze_fn=None,
                fetch_info=source.fetch_info, clock=time.monotonic) -> dict:
    """Run one job end to end. Raises on failure; the caller reports it."""
    analyze_fn = analyze_fn or analyze_mod.analyze
    url = source.canonical_url(job["video_id"])
    last = {"t": -1e9}
    timings = {}
    t_start = clock()

    def on_phase(name, fraction, message=None):
        t = clock()
        timings.setdefault(name, {"first_s": round(t - t_start, 1)})
        timings[name]["last_s"] = round(t - t_start, 1)
        if fraction >= 1.0 or t - last["t"] >= PROGRESS_MIN_GAP_S:
            last["t"] = t
            api.progress(job["id"], worker_id, name, float(fraction), message)

    on_phase("download", 0.0, "checking the video")
    info = fetch_info(url)
    det_dir = root / "detections"
    before = _snapshot(det_dir)

    doc = analyze_fn(
        url, root=root, weights=weights, info=info,
        start_s=job.get("window_start_s"), end_s=job.get("window_end_s"),
        sheet=job.get("sheet"), skip_scoreboard=True, on_phase=on_phase,
        download_attempts=1,
    )
    run_dir = out_dir / job["run_id"]
    analyze_mod.write(doc, run_dir)
    timeline_bytes = (run_dir / "timeline.json").read_bytes()

    after = _snapshot(det_dir)
    touched = [name for name, m in after.items() if before.get(name) != m]
    detcache = [{"digest": Path(n).stem, "bytes": (det_dir / n).stat().st_size} for n in touched]

    games = [{"index": g["index"], "start_s": g["start_s"], "end_s": g["end_s"],
              "ends": len(g["ends"])} for g in doc["games"]]
    meta = {
        "video_id": info.video_id, "title": info.title, "channel_id": info.channel_id,
        "duration_s": info.duration_s, "sheet": doc["source"].get("sheet"),
        "games": games, "processing_version": doc.get("processing_version"),
        "worker_id": worker_id, "timings_s": timings,
        "total_s": round(clock() - t_start, 1),
    }
    meta_bytes = json.dumps(meta, indent=1).encode()

    on_phase("upload", 0.0, "uploading results")
    plan = api.artifacts(job["id"], worker_id,
                         [{"name": "timeline.json", "bytes": len(timeline_bytes)},
                          {"name": "meta.json", "bytes": len(meta_bytes)}], detcache)
    payload = {"timeline.json": timeline_bytes, "meta.json": meta_bytes}
    for up in plan["uploads"]:
        api.upload(up["url"], up["headers"], payload[up["name"]])
    for up in plan.get("detcache_uploads", []):
        api.upload(up["url"], up["headers"], (det_dir / f"{up['digest']}.npz").read_bytes())
    on_phase("upload", 1.0, "uploaded")

    return api.complete(job["id"], worker_id, {
        "title": info.title, "channel_id": info.channel_id,
        "duration_s": info.duration_s, "sheet": doc["source"].get("sheet"),
        "games": games, "detcache_digests": [d["digest"] for d in detcache],
        "timings": timings,
    })


def run_forever(api: ApiClient, worker_id: str, *, root: Path, weights: str | None,
                out_dir: Path, cache_gb: float, sleep=time.sleep, once: bool = False):
    model = version.model_id(weights)
    gpu = gpu_name()
    log.info("worker %s: model %s, gpu %s, cache %s", worker_id, model, gpu, root)
    idle_since = None
    while True:
        try:
            job = api.claim(worker_id, model, gpu)
        except Exception as exc:  # noqa: BLE001 - the API may be down; keep trying
            log.warning("claim failed: %s", exc)
            job = None
            if once:
                return
            sleep(POLL_IDLE_S)
            continue
        if job is None:
            idle_since = idle_since or time.monotonic()
            if once:
                return
            sleep(POLL_IDLE_S if time.monotonic() - idle_since > HEARTBEAT_S else POLL_BUSY_S)
            continue
        idle_since = None
        log.info("job %s: video %s window %s-%s", job["id"], job["video_id"],
                 job.get("window_start_s"), job.get("window_end_s"))
        try:
            result = process_job(job, api, worker_id, root=root, weights=weights, out_dir=out_dir)
            log.info("job %s done: %s", job["id"], result)
        except Lost as exc:
            log.warning("job %s lost: %s", job["id"], exc)
        except Exception as exc:  # noqa: BLE001
            kind = classify_error(exc)
            log.exception("job %s failed (%s)", job["id"], kind)
            try:
                api.fail(job["id"], worker_id, f"{type(exc).__name__}: {exc}"[:500], kind)
            except Exception as exc2:  # noqa: BLE001
                log.warning("could not report failure: %s", exc2)
        try:
            removed = prune.prune(root, cache_gb)
            if removed:
                log.info("pruned %d cached media files", len(removed))
        except Exception as exc:  # noqa: BLE001
            log.warning("prune failed: %s", exc)
        if once:
            return
        sleep(POLL_BUSY_S)


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    api_url = os.environ["API_URL"]
    token = os.environ["WORKER_TOKEN"]
    root = Path(os.environ.get("CURLING_SCORE_CACHE") or cache.default_root())
    os.environ["CURLING_SCORE_CACHE"] = str(root)
    weights = os.environ.get("WEIGHTS") or None
    run_forever(
        ApiClient(api_url, token), os.environ.get("WORKER_ID") or socket.gethostname(),
        root=root, weights=weights, out_dir=Path(os.environ.get("WORKER_OUT", root / "out")),
        cache_gb=float(os.environ.get("WORKER_CACHE_GB", "300")),
        once="--once" in (argv or sys.argv[1:]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
