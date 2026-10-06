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
# With live streams in hand but nothing of theirs ready, look again this soon:
# the next end settles on the recording's clock, not the queue's.
LIVE_IDLE_S = 5.0
UPLOAD_TIMEOUT_S = 600.0
# Short, because a progress post that is not answered promptly has already
# failed at its job of being timely, and the next one is seconds away.
PROGRESS_TIMEOUT_S = 15.0


class Lost(Exception):
    """The API says this job is no longer ours; stop working on it."""


class Yield(Exception):
    """A live stream needs the GPU: hand this recording back and come back to it."""


def _yielded(exc: BaseException) -> bool:
    """Whether ``exc`` is, or was caused by, a Yield -- yt-dlp may wrap one
    raised from inside its progress hook."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, Yield):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


class ApiClient:
    def __init__(self, base_url: str, token: str, http=None):
        import httpx

        self.base = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=60.0)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _post(self, path, payload, timeout=None):
        r = self._http.post(f"{self.base}{path}", json=payload,
                            headers=self._headers, timeout=timeout)
        if r.status_code == 409:
            raise Lost(r.text)
        r.raise_for_status()
        return r.json() if r.content else None

    def claim(self, worker_id, model_id, gpu, kinds=None):
        body = {"worker_id": worker_id, "version": version.PIPELINE_VERSION,
                "model_id": model_id, "gpu": gpu}
        if kinds is not None:
            body["kinds"] = list(kinds)
        r = self._http.post(f"{self.base}/api/worker/claim", headers=self._headers,
                            json=body)
        if r.status_code == 204:
            return None
        if r.status_code == 409:
            raise RuntimeError(f"model mismatch: {r.text}")
        r.raise_for_status()
        return r.json()["job"]

    def progress(self, job_id, worker_id, phase, fraction, message):
        """Report where the job has got to. Best-effort by contract.

        A progress post is telemetry: it moves a bar on a page and pushes the
        lease out. Losing one is not a reason to abandon half an hour of work,
        so the caller is expected to swallow network failures -- and the lease
        is the backstop, since a worker that has genuinely stopped talking
        stops renewing it and the job is requeued.

        Only 409 propagates: that is the server saying the job is no longer
        ours, which is a real instruction to stop.
        """
        return self._post(f"/api/worker/jobs/{job_id}/progress",
                          {"worker_id": worker_id, "phase": phase,
                           "fraction": fraction, "message": message},
                          timeout=PROGRESS_TIMEOUT_S)

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

    def yield_job(self, job_id, worker_id):
        return self._post(f"/api/worker/jobs/{job_id}/yield", {"worker_id": worker_id})

    def publish(self, job_id, worker_id, payload):
        return self._post(f"/api/worker/jobs/{job_id}/publish",
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
                weights: str | None, out_dir: Path, skip_longview: bool = False,
                analyze_fn=None, fetch_info=source.fetch_info,
                clock=time.monotonic, should_yield=None) -> dict:
    """Run one job end to end. Raises on failure; the caller reports it.

    ``should_yield`` is asked at every progress report, and a yes raises
    Yield: a live stream has started and needs the GPU. The detection cache
    keeps every end already done, so coming back costs little.
    """
    analyze_fn = analyze_fn or analyze_mod.analyze
    url = source.canonical_url(job["video_id"])
    last = {"t": -1e9}
    timings = {}
    t_start = clock()

    def on_phase(name, fraction, message=None):
        # Not once the results are going up: they are made, and handing the
        # job back now would only mean making them again.
        if name != "upload" and should_yield is not None and should_yield():
            raise Yield(f"live work waiting, at {name}")
        t = clock()
        timings.setdefault(name, {"first_s": round(t - t_start, 1)})
        timings[name]["last_s"] = round(t - t_start, 1)
        if fraction < 1.0 and t - last["t"] < PROGRESS_MIN_GAP_S:
            return
        last["t"] = t
        try:
            api.progress(job["id"], worker_id, name, float(fraction), message)
        except Lost:
            raise                      # the server has given the job away
        except Exception as exc:       # noqa: BLE001 - any network trouble
            # Telemetry, not correctness. A timeout here once cost a job that
            # had just finished a three-minute download and was otherwise fine.
            log.warning("progress (%s %.2f) not delivered: %s", name, fraction, exc)

    on_phase("download", 0.0, "checking the video")
    info = fetch_info(url)
    det_dir = root / "detections"
    before = _snapshot(det_dir)

    # The API decides the format and checks it on complete. A claim without
    # one is an API from before formats, which holds every run as fours; the
    # title must not overrule that.
    doc = analyze_fn(
        url, root=root, weights=weights, info=info,
        start_s=job.get("window_start_s"), end_s=job.get("window_end_s"),
        sheet=job.get("sheet"), game_format=job.get("format") or "fours",
        skip_scoreboard=False,
        skip_longview=skip_longview, on_phase=on_phase,
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
    fmt_name = (doc.get("format") or {}).get("name", "fours")
    meta = {
        "video_id": info.video_id, "title": info.title, "channel_id": info.channel_id,
        "duration_s": info.duration_s, "sheet": doc["source"].get("sheet"),
        "games": games, "processing_version": doc.get("processing_version"),
        "worker_id": worker_id, "timings_s": timings,
        "total_s": round(clock() - t_start, 1),
        "format": fmt_name,
        # Whether the ends looked like the format they were analysed as: a
        # doubles document carries the check, a fours one only its warning.
        "format_check": (doc.get("format") or {}).get("check"),
        "format_warning": doc.get("format_warning"),
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
        "timings": timings, "format": fmt_name,
    })


def resolve_weights():
    """Which model this worker runs.

    ``WEIGHTS`` still wins, so a deployment can pin a model or ask for the
    classical detector with ``none``. Unset now means the project default
    (``weights.DEFAULT_NAME``) rather than the colour detector.

    A worker that cannot find the default falls back to classical and says so,
    rather than refusing to start. That is safe here only because
    ``version.model_id`` writes the model into every timeline's
    ``processing_version``: the fallback is recorded, not hidden.
    """
    from curling_score import weights as weights_mod

    chosen = os.environ.get("WEIGHTS")
    if chosen:
        return None if chosen.strip().lower() in ("none", "classical") else chosen
    try:
        path = weights_mod.default_path()
    except FileNotFoundError as exc:
        log.warning("no default weights (%s); falling back to the classical "
                    "detector, which will show in processing_version", exc)
        return None
    return str(path) if path else None


def resolve_skip_longview() -> bool:
    """Whether this worker should skip the side-view hog-crossing pass.

    Off by default, matching ``analyze()``'s own default -- setting
    ``SKIP_LONGVIEW=1`` is an escape hatch for a deployment that cannot pay
    for the per-shot ffmpeg decode the side views need, not a new default.
    """
    return os.environ.get("SKIP_LONGVIEW") == "1"


def run_forever(api: ApiClient, worker_id: str, *, root: Path, weights: str | None,
                out_dir: Path, cache_gb: float, min_free_gb: float = 0.0,
                recording_days: float | None = None, skip_longview: bool = False, sleep=time.sleep, once: bool = False,
                live=None, **process_kw):
    """Claim and process jobs until stopped.

    With a ``live`` lane, live streams come first: while the lane has any,
    the loop steps them and claims nothing else, and a recording already in
    hand is handed back (``/yield``) as soon as one appears -- the lane's
    manager thread claims live jobs by itself. Without one, as before.
    """
    model = version.model_id(weights)
    gpu = gpu_name()
    log.info("worker %s: model %s, gpu %s, cache %s", worker_id, model, gpu, root)
    idle_since = None
    served_live = False
    while True:
        if live is not None and live.busy():
            served_live = True
            try:
                did = live.step()
            except Exception:  # noqa: BLE001 - the lane contains its streams' errors;
                # this is the backstop, so a bug there costs a pause, not the worker.
                log.exception("live lane step failed")
                did = False
            if not did:
                sleep(LIVE_IDLE_S)
            continue
        if once and served_live:
            return
        try:
            job = (api.claim(worker_id, model, gpu) if live is None
                   else api.claim(worker_id, model, gpu, kinds=["vod"]))
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
            result = process_job(job, api, worker_id, root=root, weights=weights,
                                 out_dir=out_dir, skip_longview=skip_longview,
                                 should_yield=None if live is None else live.busy,
                                 **process_kw)
            log.info("job %s done: %s", job["id"], result)
        except Lost as exc:
            log.warning("job %s lost: %s", job["id"], exc)
        except Exception as exc:  # noqa: BLE001
            if _yielded(exc):
                log.info("job %s handed back for live work", job["id"])
                try:
                    api.yield_job(job["id"], worker_id)
                except Exception as exc2:  # noqa: BLE001 - the lease will return it
                    log.warning("could not hand job %s back: %s", job["id"], exc2)
                if once:
                    return
                continue
            kind = classify_error(exc)
            log.exception("job %s failed (%s)", job["id"], kind)
            try:
                api.fail(job["id"], worker_id, f"{type(exc).__name__}: {exc}"[:500], kind)
            except Exception as exc2:  # noqa: BLE001
                log.warning("could not report failure: %s", exc2)
        try:
            removed = prune.prune(root, cache_gb, min_free_gb=min_free_gb,
                                  recording_days=recording_days)
            if removed:
                log.info("pruned %d cached media files", len(removed))
        except Exception as exc:  # noqa: BLE001
            log.warning("prune failed: %s", exc)
        if once:
            return
        sleep(POLL_BUSY_S)


def _enable_stack_dumps():
    """See :func:`curling_score.diagnostics.enable_stack_dumps`."""
    from curling_score.diagnostics import enable_stack_dumps

    enable_stack_dumps()


def build_live(api_url, token, worker_id, *, root: Path, weights,
               skip_longview: bool = False, start: bool = True,
               cache_gb: float | None = None, min_free_gb: float = 0.0,
               recording_days: float | None = None):
    """The live lane, when ``WORKER_LIVE=1`` asks for one; None otherwise.

    Its manager gets an API client of its own, since it talks to the API from
    its own thread, and follows up to ``LIVE_MAX_STREAMS`` streams at once.
    A worker starts with nothing in hand, so any recordings left under
    ``live/`` by a worker that died are cleared: a live job it held comes back
    when its lease runs out, and is recorded again from the first segment.
    A recording is kept once its stream ends -- whole as the cached video,
    partial under ``kept/`` -- and the cache is then pruned as after any job,
    recordings older than ``recording_days`` first.
    """
    if os.environ.get("WORKER_LIVE") != "1":
        return None
    import shutil

    from curling_score.live import lane, manager

    shutil.rmtree(root / "live", ignore_errors=True)
    def prune_cache():
        removed = prune.prune(root, cache_gb, min_free_gb=min_free_gb,
                              recording_days=recording_days)
        if removed:
            log.info("pruned %d cached media files", len(removed))

    mgr = manager.LiveManager(
        ApiClient(api_url, token), worker_id, model_id=version.model_id(weights),
        gpu=gpu_name() if start else None, root=root,
        max_streams=int(os.environ.get("LIVE_MAX_STREAMS", manager.MAX_STREAMS)),
        prune=prune_cache if cache_gb is not None else None)
    if start:
        mgr.start()
    return lane.LiveLane(mgr, ApiClient(api_url, token), worker_id,
                         make_session=lane.video_sessions(weights,
                                                          skip_longview=skip_longview))


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    _enable_stack_dumps()
    api_url = os.environ["API_URL"]
    token = os.environ["WORKER_TOKEN"]
    root = Path(os.environ.get("CURLING_SCORE_CACHE") or cache.default_root())
    os.environ["CURLING_SCORE_CACHE"] = str(root)
    weights = resolve_weights()
    worker_id = os.environ.get("WORKER_ID") or socket.gethostname()
    cache_gb = float(os.environ.get("WORKER_CACHE_GB", "300"))
    min_free_gb = float(os.environ.get("WORKER_MIN_FREE_GB", "40"))
    recording_days = float(os.environ.get("WORKER_RECORDING_DAYS", "7"))
    run_forever(
        ApiClient(api_url, token), worker_id,
        root=root, weights=weights, out_dir=Path(os.environ.get("WORKER_OUT", root / "out")),
        cache_gb=cache_gb, min_free_gb=min_free_gb, recording_days=recording_days,
        skip_longview=resolve_skip_longview(),
        once="--once" in (argv or sys.argv[1:]),
        live=build_live(api_url, token, worker_id, root=root, weights=weights,
                        skip_longview=resolve_skip_longview(),
                        cache_gb=cache_gb, min_free_gb=min_free_gb,
                        recording_days=recording_days),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
