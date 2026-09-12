"""The HTTP face of the service: submit a link, chart a game, feed the worker.

Three audiences, three surfaces. People get a submit form, a status page, the
charting viewer under ``/c/{slug}/`` and a read-only twin under ``/s/``. The
worker gets a claim/progress/complete protocol behind a bearer token. The
operator gets an admin token for approvals, retries, playlists and a backup.

The viewer is served under a directory-shaped URL on purpose: its page fetches
bare ``timeline.json`` and ``overrides.json``, so mounted at ``/c/{slug}/`` it
needs no changes to talk to us, and the local ``curling-score serve`` keeps
working against the same page.
"""

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from curling_score import timeline, version, viewer
from curling_score.ingest import source
from curling_score.service import dedupe, playlists, slug
from curling_score.service.records import Chart, Job, Run, Worker
from curling_score.service.repo import LEASE_S, worker_online
from curling_score.service.store import detcache_key, meta_key, timeline_key
from curling_score.service.youtube import SubmissionError, validate_submission

log = logging.getLogger(__name__)

VIEWER_DIR = Path(viewer.__file__).parent
STATIC_DIR = Path(__file__).parent / "static"
VIEWER_ASSETS = {"app.js": "application/javascript", "style.css": "text/css"}
STATIC_ASSETS = {"status.js": "application/javascript",
                 "games.js": "application/javascript",
                 "submit.js": "application/javascript",
                 "site.css": "text/css"}
MAX_OVERRIDES_BYTES = 10_000_000
# Minutes each stage usually takes on the home box, for the status page's ETA.
PHASE_BUDGET_MIN = {"download": 3, "proxy": 9, "calibrate": 0.5, "profile": 1,
                    "detect": 9, "rules": 1, "scoreboard": 0.5, "upload": 0.5}
PHASE_ORDER = list(PHASE_BUDGET_MIN)


@dataclass
class Settings:
    public_base_url: str = ""
    worker_token: str = ""
    admin_token: str = ""
    allowed_channels: set = field(default_factory=set)
    require_approval: bool = False
    model_id: str = "classical"
    # A hard sanity cap on what we will look at at all. Streams longer than
    # dedupe.MAX_WHOLE_S (5 h) are still accepted -- with a start time, and
    # analysed in a window around it -- so this only refuses the absurd.
    max_hours: float = 12.0
    max_queued: int = 10
    rate_hour: int = 5
    rate_day: int = 20
    ip_salt: str = "curling"
    lease_s: float = LEASE_S

    @property
    def processing_version(self) -> str:
        return f"{version.PIPELINE_VERSION}+{self.model_id}"

    @classmethod
    def from_env(cls):
        env = os.environ.get
        chans = {c.strip() for c in env("ALLOWED_CHANNEL_IDS", "").split(",") if c.strip()}
        return cls(
            public_base_url=env("PUBLIC_BASE_URL", "").rstrip("/"),
            worker_token=env("WORKER_TOKEN", ""),
            admin_token=env("ADMIN_TOKEN", ""),
            allowed_channels=chans,
            require_approval=env("REQUIRE_APPROVAL", "0") in ("1", "true", "yes"),
            model_id=env("MODEL_ID", "classical"),
            max_hours=float(env("MAX_HOURS", "12")),
            max_queued=int(env("MAX_QUEUED", "10")),
            rate_hour=int(env("RATE_HOUR", "5")),
            rate_day=int(env("RATE_DAY", "20")),
            ip_salt=env("IP_SALT", "curling"),
        )


def utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def create_app(repo, store, youtube, settings: Settings, now=utcnow) -> FastAPI:
    app = FastAPI(title="Curling Chart", docs_url=None, redoc_url=None)

    # ------------------------------------------------------------ helpers
    def bearer_ok(header, expected) -> bool:
        if not expected or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):], expected)

    def require_worker(authorization):
        if not settings.worker_token:
            raise HTTPException(503, "worker token is not configured")
        if not bearer_ok(authorization, settings.worker_token):
            raise HTTPException(401, "bad worker token")

    def require_admin(authorization):
        if not settings.admin_token:
            raise HTTPException(503, "admin token is not configured")
        if not bearer_ok(authorization, settings.admin_token):
            raise HTTPException(401, "bad admin token")

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def ip_hash(request: Request) -> str:
        return hashlib.sha256((settings.ip_salt + client_ip(request)).encode()).hexdigest()[:32]

    def url_for(path: str) -> str:
        return f"{settings.public_base_url}{path}"

    @lru_cache(maxsize=64)
    def load_doc(key: str) -> dict:
        data = store.get_bytes(key)
        if data is None:
            raise KeyError(key)
        return json.loads(data)

    def chart_doc(chart: Chart, run: Run, read_only: bool) -> dict:
        """The pristine document, cut down to the chart's game."""
        doc = json.loads(json.dumps(load_doc(run.timeline_key)))  # a private copy
        if chart.game_index is not None:
            doc["games"] = [g for g in doc["games"] if g["index"] == chart.game_index]
        game = doc["games"][0] if doc["games"] else None
        doc["source"]["start_s"] = game["start_s"] if game else None
        doc["chart"] = {
            "slug": chart.id,
            "share_url": None if read_only else url_for(f"/s/{chart.share_slug}/"),
            "read_only": read_only,
            "requested_start_s": chart.requested_start_s,
            "snap_distance_s": chart.snap_distance_s,
            "game_index": chart.game_index,
            "title": run.title,
            "league": run.league,
        }
        return doc

    def lookup(kind: str, key: str):
        chart = repo.get_chart(key) if kind == "c" else repo.chart_by_share(key)
        if chart is None:
            raise HTTPException(404, "no such chart")
        run = repo.get_run(chart.run_id)
        if run is None:
            raise HTTPException(404, "the chart's run is missing")
        return chart, run, kind == "s"

    def page(name: str, **subs) -> HTMLResponse:
        text = (STATIC_DIR / name).read_text()
        for k, v in subs.items():
            text = text.replace("{{" + k + "}}", v)
        return HTMLResponse(text)

    def status_payload(chart: Chart, run: Run, read_only: bool) -> dict:
        job = repo.job_for_run(run.id)
        t = now()
        online = worker_online(repo.workers(), t)
        out = {
            "status": run.status, "title": run.title, "league": run.league,
            "worker_online": online, "read_only": read_only,
            "phase": None, "fraction": None, "message": None, "eta_s": None,
            "position": None, "error": run.error, "game": None, "other_games": [],
        }
        if run.status in ("queued", "pending_approval") and job is not None:
            out["position"] = repo.queue_position(job.id)
        if run.status == "processing" and job is not None:
            out.update(phase=job.phase, fraction=job.fraction, message=job.message)
            if job.phase in PHASE_ORDER:
                i = PHASE_ORDER.index(job.phase)
                rest = sum(PHASE_BUDGET_MIN[p] for p in PHASE_ORDER[i + 1:])
                here = PHASE_BUDGET_MIN[job.phase] * (1.0 - (job.fraction or 0.0))
                out["eta_s"] = round((rest + here) * 60)
        if run.status == "ready" and chart.game_index is not None:
            for g in run.games:
                entry = {"index": g["index"], "start_s": g["start_s"],
                         "end_s": g["end_s"], "ends": g.get("ends")}
                if g["index"] == chart.game_index:
                    out["game"] = {**entry, "snap_distance_s": chart.snap_distance_s}
                else:
                    out["other_games"].append(entry)
        return out

    def new_chart(video_id, run, start_s, sheet, iph, t) -> Chart:
        chart = Chart(id=slug.new_slug(), share_slug=slug.new_slug(), video_id=video_id,
                      run_id=run.id, created_at=t, updated_at=t,
                      requested_start_s=start_s, requested_sheet=sheet,
                      submitter_ip_hash=iph)
        repo.put_chart(chart)
        if run.status == "ready":
            chart = dedupe.resolve_chart(repo, chart, run, t)
        return chart

    # ------------------------------------------------------------- public
    @app.get("/", response_class=HTMLResponse)
    def home():
        return page("submit.html")

    @app.get("/games", response_class=HTMLResponse)
    def games_page():
        return page("games.html")

    @app.get("/static/{name}")
    def static_asset(name: str):
        if name not in STATIC_ASSETS:
            raise HTTPException(404)
        return FileResponse(STATIC_DIR / name, media_type=STATIC_ASSETS[name])

    # Under /api/ deliberately: Google's frontend reserves /healthz and answers
    # it with its own 404 before the request reaches this container.
    @app.get("/api/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/healthz/worker")
    def healthz_worker():
        seen = repo.workers()
        latest = max((w.last_seen_at for w in seen), default=None)
        ok = latest is not None and (now() - latest) < timedelta(hours=1)
        return JSONResponse({"ok": ok, "last_seen_at": _iso(latest)}, status_code=200 if ok else 503)

    @app.get("/api/games")
    def api_games(league: str | None = None, video_id: str | None = None):
        sources = repo.list_sources(league=league)
        if video_id:
            sources = [s for s in sources if s.video_id == video_id]
        runs = {}
        out = []
        for s in sources:
            run = runs.get(s.current_run_id) or repo.get_run(s.current_run_id)
            runs[s.current_run_id] = run
            out.append({
                "source_id": s.id, "video_id": s.video_id, "title": s.title,
                "sheet": s.sheet, "league": s.league, "played_at": _iso(s.played_at),
                "game_index": s.game_index, "start_s": s.game_start_s,
                "end_s": s.game_end_s, "status": run.status if run else None,
                "ends": next((g.get("ends") for g in (run.games if run else [])
                              if g["index"] == s.game_index), None),
            })
        # Games queued or in flight have no sources yet; list their runs too so
        # a person can make a link now and have it fill in.
        for run in repo.list_runs():
            if run.status in ("ready", "failed"):
                continue
            out.append({
                "source_id": None, "video_id": run.video_id, "title": run.title,
                "sheet": run.sheet, "league": run.league,
                "played_at": _iso(run.published_at), "game_index": None,
                "start_s": run.window_start_s, "end_s": run.window_end_s,
                "status": run.status, "ends": None,
            })
        leagues = sorted({g["league"] for g in out if g["league"]})
        return {"games": out, "leagues": leagues}

    @app.post("/api/submissions", status_code=201)
    async def submit(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "expected a JSON body")
        if not isinstance(body, dict):
            raise HTTPException(400, "expected an object")
        raw_url = str(body.get("url") or body.get("video_id") or "").strip()
        if not raw_url:
            raise HTTPException(400, "give a YouTube link")
        try:
            link = source.parse_link(raw_url)
        except ValueError:
            raise HTTPException(400, "that is not a YouTube video link")
        start_s = body.get("start_s", link.start_s)
        start_s = None if start_s in (None, "") else float(start_s)
        sheet = body.get("sheet")
        sheet = None if sheet in (None, "") else int(sheet)
        t = now()
        iph = ip_hash(request)

        if not repo.bump_rate_limit(iph, t, settings.rate_hour, settings.rate_day):
            raise HTTPException(429, "too many submissions from this address; try later")

        try:
            meta = validate_submission(youtube.video(link.video_id),
                                       settings.allowed_channels or None,
                                       settings.max_hours)
        except SubmissionError as exc:
            raise HTTPException(exc.status, exc.message)

        run = dedupe.find_reusable_run(repo.runs_for_video(link.video_id),
                                       settings.processing_version, start_s)
        reused = run is not None
        if run is None:
            if repo.count_queued() >= settings.max_queued:
                raise HTTPException(503, "the processing queue is full; try again later")
            try:
                w0, w1 = dedupe.window_for(meta.duration_s, start_s)
            except dedupe.NeedsStartTime:
                raise HTTPException(422, "that stream is very long -- give the time "
                                         "the game starts (e.g. paste a link with ?t=)")
            status = "pending_approval" if settings.require_approval else "queued"
            run = Run(id=slug.new_run_id(), video_id=link.video_id,
                      processing_version=settings.processing_version, status=status,
                      created_at=t, window_start_s=w0, window_end_s=w1,
                      title=meta.title, channel_id=meta.channel_id,
                      duration_s=meta.duration_s, published_at=meta.published_at,
                      sheet=sheet if sheet is not None else source.sheet_from_title(meta.title))
            repo.put_run(run)
            if status == "queued":
                repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued",
                                 created_at=t, run_after=t))
        chart = new_chart(link.video_id, run, start_s, sheet, iph, t)
        job = repo.job_for_run(run.id)
        return {
            "slug": chart.id,
            "chart_url": url_for(f"/c/{chart.id}/"),
            "share_url": url_for(f"/s/{chart.share_slug}/"),
            "status": run.status,
            "position": repo.queue_position(job.id) if job and run.status == "queued" else None,
            "reused": reused,
        }

    # ------------------------------------------------------ chart routes
    def register_chart_routes(kind: str):
        prefix = f"/{kind}/{{key}}"

        @app.get(prefix, include_in_schema=False)
        def redirect(key: str):
            return RedirectResponse(f"{prefix.replace('{key}', key)}/", status_code=302)

        @app.get(prefix + "/", response_class=HTMLResponse)
        def chart_page(key: str):
            chart, run, read_only = lookup(kind, key)
            if run.status != "ready" or chart.game_index is None:
                return page("status.html", SLUG=chart.id, MODE="view" if read_only else "edit")
            text = (VIEWER_DIR / "index.html").read_text()
            boot = (f'<script>window.CHART={json.dumps({"slug": chart.id, "mode": "view" if read_only else "edit"})};'
                    f'</script>\n<script src="app.js"></script>')
            return HTMLResponse(text.replace('<script src="app.js"></script>', boot, 1))

        @app.get(prefix + "/status.json")
        def status_json(key: str):
            chart, run, read_only = lookup(kind, key)
            return status_payload(chart, run, read_only)

        @app.get(prefix + "/timeline.json")
        def timeline_json(key: str):
            chart, run, read_only = lookup(kind, key)
            if run.status != "ready" or chart.game_index is None:
                raise HTTPException(404, "not processed yet")
            return JSONResponse(chart_doc(chart, run, read_only),
                                headers={"Cache-Control": "private, max-age=3600"})

        @app.get(prefix + "/overrides.json")
        def overrides_get(key: str):
            chart, _run, _ro = lookup(kind, key)
            return JSONResponse(chart.overrides, headers={"ETag": f'"{chart.overrides_version}"'})

        @app.post(prefix + "/overrides.json")
        async def overrides_post(key: str, request: Request, v: int | None = Query(default=None)):
            chart, _run, read_only = lookup(kind, key)
            if read_only:
                raise HTTPException(403, "this is a view-only link")
            body = await request.body()
            if not body:
                return JSONResponse({"ok": False, "error": "empty body"}, status_code=400)
            if len(body) > MAX_OVERRIDES_BYTES:
                return JSONResponse({"ok": False, "error": "too large"}, status_code=400)
            try:
                data = json.loads(body)
            except ValueError as err:
                return JSONResponse({"ok": False, "error": f"invalid JSON: {err}"}, status_code=400)
            if not isinstance(data, dict) or not all(isinstance(x, dict) for x in data.values()):
                return JSONResponse({"ok": False, "error": "expected {key: patch}"}, status_code=400)
            ok, ver, current = repo.save_overrides(chart.id, data, v, now())
            if not ok:
                return JSONResponse({"ok": False, "error": "stale", "version": ver,
                                     "overrides": current}, status_code=409)
            return {"ok": True, "shots": len(data), "version": ver}

        @app.get(prefix + "/export.json")
        def export_json(key: str):
            chart, run, read_only = lookup(kind, key)
            if run.status != "ready":
                raise HTTPException(404, "not processed yet")
            return timeline.apply_overrides(chart_doc(chart, run, read_only), chart.overrides)

        @app.get(prefix + "/{asset}")
        def chart_asset(key: str, asset: str):
            lookup(kind, key)
            if asset in VIEWER_ASSETS:
                return FileResponse(VIEWER_DIR / asset, media_type=VIEWER_ASSETS[asset])
            if asset in STATIC_ASSETS:
                return FileResponse(STATIC_DIR / asset, media_type=STATIC_ASSETS[asset])
            raise HTTPException(404)

    register_chart_routes("c")
    register_chart_routes("s")

    # ------------------------------------------------------------- worker
    @app.post("/api/worker/claim")
    async def claim(request: Request, authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        t = now()
        repo.heartbeat(Worker(id=str(body.get("worker_id", "worker")), last_seen_at=t,
                              version=body.get("version"), model_id=body.get("model_id"),
                              gpu=body.get("gpu")))
        if body.get("model_id") and body["model_id"] != settings.model_id:
            raise HTTPException(409, f"worker has model {body['model_id']}, "
                                     f"the service expects {settings.model_id}")
        repo.requeue_expired(t)
        job = repo.claim_job(str(body.get("worker_id", "worker")), t, settings.lease_s)
        if job is None:
            return Response(status_code=204)
        run = repo.get_run(job.run_id)
        repo.heartbeat(Worker(id=str(body.get("worker_id", "worker")), last_seen_at=t,
                              version=body.get("version"), model_id=body.get("model_id"),
                              gpu=body.get("gpu"), current_job_id=job.id))
        return {"job": {
            "id": job.id, "run_id": run.id, "video_id": run.video_id,
            "window_start_s": run.window_start_s, "window_end_s": run.window_end_s,
            "sheet": run.sheet, "processing_version": run.processing_version,
            "attempt": job.attempts, "lease_s": settings.lease_s,
        }}

    def owned_job(job_id: str, worker_id: str | None) -> Job:
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        if job.state != "running" or (worker_id and job.worker_id != worker_id):
            raise HTTPException(409, "this job is not yours any more")
        return job

    @app.post("/api/worker/jobs/{job_id}/progress")
    async def progress(job_id: str, request: Request,
                       authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_job(job_id, body.get("worker_id"))
        t = now()
        lease = t + timedelta(seconds=settings.lease_s)
        repo.update_job(job.id, phase=body.get("phase"), fraction=body.get("fraction"),
                        message=body.get("message"), progress_at=t, lease_expires_at=lease)
        return {"ok": True, "lease_expires_at": _iso(lease)}

    @app.post("/api/worker/jobs/{job_id}/artifacts")
    async def artifacts(job_id: str, request: Request,
                        authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_job(job_id, body.get("worker_id"))
        run = repo.get_run(job.run_id)
        def signed(key, content_type):
            target = store.presign_put(key, content_type)
            if target["url"].startswith("memory://"):
                # No real bucket behind this store: take the bytes ourselves.
                target = {"url": url_for(f"/api/worker/jobs/{job.id}/upload?key={key}"),
                          "headers": {"Content-Type": content_type}}
            return target

        uploads = []
        for f in body.get("files", []):
            name = f["name"]
            if name not in ("timeline.json", "meta.json"):
                raise HTTPException(400, f"unknown artifact {name}")
            key = timeline_key(run.video_id, run.id) if name == "timeline.json" \
                else meta_key(run.video_id, run.id)
            uploads.append({"name": name, "key": key, **signed(key, "application/json")})
        det = []
        for d in body.get("detcache", []):
            key = detcache_key(run.video_id, d["digest"])
            if store.exists(key):
                continue
            det.append({"digest": d["digest"], "key": key,
                        **signed(key, "application/octet-stream")})
        return {"uploads": uploads, "detcache_uploads": det}

    @app.put("/api/worker/jobs/{job_id}/upload")
    async def upload_via_api(job_id: str, request: Request, key: str = Query(...),
                             authorization: str | None = Header(default=None)):
        """The fallback upload path, used only when the store cannot sign URLs."""
        require_worker(authorization)
        job = repo.get_job(job_id)
        if job is None or job.state != "running":
            raise HTTPException(409, "this job is not running")
        run = repo.get_run(job.run_id)
        allowed = (timeline_key(run.video_id, run.id), meta_key(run.video_id, run.id))
        if key not in allowed and not key.startswith(f"detcache/{run.video_id}/"):
            raise HTTPException(400, "that key does not belong to this job")
        data = await request.body()
        store.put_bytes(key, data, request.headers.get("content-type", "application/octet-stream"))
        return {"ok": True, "bytes": len(data)}

    @app.post("/api/worker/jobs/{job_id}/complete")
    async def complete(job_id: str, request: Request,
                       authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        run = repo.get_run(job.run_id)
        if job.state == "done":
            return {"ok": True, "charts_resolved": 0, "already": True}
        owned_job(job_id, body.get("worker_id"))
        tkey = timeline_key(run.video_id, run.id)
        if not store.exists(tkey):
            raise HTTPException(409, "timeline.json has not been uploaded")
        t = now()
        games = body.get("games") or []
        repo.update_run(run.id, status="ready", ready_at=t, games=games,
                        title=body.get("title", run.title),
                        channel_id=body.get("channel_id", run.channel_id),
                        duration_s=body.get("duration_s", run.duration_s),
                        sheet=body.get("sheet", run.sheet),
                        timeline_key=tkey, meta_key=meta_key(run.video_id, run.id),
                        error=None)
        repo.update_job(job.id, state="done", finished_at=t, phase="done", fraction=1.0)
        run = repo.get_run(run.id)
        resolved = dedupe.resolve_charts_for_run(repo, run, t)
        return {"ok": True, "charts_resolved": resolved}

    @app.post("/api/worker/jobs/{job_id}/fail")
    async def fail(job_id: str, request: Request,
                   authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_job(job_id, body.get("worker_id"))
        kind = body.get("kind", "transient")
        error = str(body.get("error", ""))[:500]
        t = now()
        if kind == "permanent" or job.attempts >= job.max_attempts:
            repo.update_job(job.id, state="failed", error=error, error_kind=kind, finished_at=t)
            repo.update_run(job.run_id, status="failed", error=error)
            return {"ok": True, "final": True}
        if kind == "blocked":
            ladder = (300, 600, 1200)
            delay = ladder[min(job.attempts - 1, len(ladder) - 1)]
        else:
            delay = float(body.get("retry_after_s") or 60)
        repo.update_job(job.id, state="queued", worker_id=None, lease_expires_at=None,
                        run_after=t + timedelta(seconds=delay), error=error, error_kind=kind)
        repo.update_run(job.run_id, status="queued", error=error)
        return {"ok": True, "final": False, "retry_after_s": delay}

    @app.get("/api/worker/detcache/{video_id}")
    def detcache_list(video_id: str, authorization: str | None = Header(default=None)):
        require_worker(authorization)
        keys = store.list_keys(f"detcache/{video_id}/")
        return [{"digest": Path(k).stem, "url": store.presign_get(k)} for k in keys]

    # -------------------------------------------------------------- admin
    @app.get("/api/admin/runs")
    def admin_runs(status: str | None = None,
                   authorization: str | None = Header(default=None)):
        require_admin(authorization)
        out = []
        for run in repo.list_runs(status=status):
            job = repo.job_for_run(run.id)
            out.append({**{k: (v.isoformat() if isinstance(v, datetime) else v)
                           for k, v in run.to_dict().items()},
                        "job": None if job is None else {
                            "id": job.id, "state": job.state, "attempts": job.attempts,
                            "phase": job.phase, "fraction": job.fraction,
                            "error": job.error, "error_kind": job.error_kind}})
        return {"runs": out, "workers": [
            {**w.to_dict(), "last_seen_at": _iso(w.last_seen_at)} for w in repo.workers()],
            "queued": repo.count_queued()}

    @app.post("/api/admin/runs/{run_id}/approve")
    def admin_approve(run_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(404)
        if run.status != "pending_approval":
            return {"ok": True, "status": run.status}
        t = now()
        repo.update_run(run.id, status="queued")
        repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued", created_at=t, run_after=t))
        return {"ok": True, "status": "queued"}

    @app.post("/api/admin/runs/{run_id}/retry")
    def admin_retry(run_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(404)
        t = now()
        repo.update_run(run.id, status="queued", error=None)
        repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued", created_at=t, run_after=t))
        return {"ok": True}

    @app.post("/api/admin/reprocess")
    async def admin_reprocess(request: Request, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        body = await request.json()
        vid = source.video_id(str(body["video_id"]))
        prior = repo.runs_for_video(vid)
        base = prior[0] if prior else None
        t = now()
        run = Run(id=slug.new_run_id(), video_id=vid,
                  processing_version=settings.processing_version, status="queued",
                  created_at=t, title=base.title if base else None,
                  channel_id=base.channel_id if base else None,
                  duration_s=base.duration_s if base else None,
                  sheet=base.sheet if base else None,
                  published_at=base.published_at if base else None,
                  league=base.league if base else None,
                  playlist_id=base.playlist_id if base else None)
        repo.put_run(run)
        repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued", created_at=t, run_after=t))
        return {"ok": True, "run_id": run.id}

    @app.get("/api/admin/export")
    def admin_export(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return JSONResponse(json.loads(json.dumps(repo.export_all(), default=_json_default)))

    @app.get("/api/admin/playlists")
    def admin_playlists(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"playlists": [
            {**p.to_dict(), "created_at": _iso(p.created_at), "last_polled_at": _iso(p.last_polled_at)}
            for p in repo.list_playlists()]}

    @app.post("/api/admin/playlists", status_code=201)
    async def admin_add_playlist(request: Request, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        body = await request.json()
        pl = playlists.new_watched_playlist(str(body["playlist_id"]).strip(),
                                            str(body.get("label") or body["playlist_id"]).strip(),
                                            now())
        repo.put_playlist(pl)
        return {"ok": True, "id": pl.id}

    @app.delete("/api/admin/playlists/{pid}")
    def admin_delete_playlist(pid: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        repo.delete_playlist(pid)
        return {"ok": True}

    @app.post("/api/admin/poll-playlists")
    def admin_poll(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        result = playlists.poll(
            repo, youtube, processing_version=settings.processing_version, now=now(),
            allowed_channels=settings.allowed_channels or None,
            max_hours=min(settings.max_hours, dedupe.MAX_WHOLE_S / 3600),
            initial_status="pending_approval" if settings.require_approval else "queued",
        )
        return result

    return app


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")
