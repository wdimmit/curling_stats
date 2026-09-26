"""The HTTP face of the service: submit a link, chart a game, feed the worker.

People get a submit form, a status page, the charting viewer under
``/c/{slug}/``, a read-only twin under ``/s/{share}/``, and -- for anyone who
only wants the shot-to-shot navigation -- a public, chart-less ``/g/{source}/``.
The worker gets a claim/progress/complete protocol behind a bearer token. The
operator gets an admin token for approvals, retries, playlists and a backup.

The three human surfaces differ in what holding the URL means. ``/g/`` is
public and grants nothing; the other two are unguessable, and holding one *is*
the permission it carries. The kind of URL decides the mode, always.

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
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from curling_score import timeline, version, viewer
from curling_score.ingest import source
from curling_score.service import dedupe, playlists, slug
from curling_score.service.auth import NoAuth
from curling_score.service.records import (
    Chart, Flag, Invite, Job, Run, Source, Team, User, Worker,
)
from curling_score.service.repo import LEASE_S, worker_online
from curling_score.service.store import detcache_key, meta_key, timeline_key
from curling_score.service.youtube import SubmissionError, validate_submission

log = logging.getLogger(__name__)

VIEWER_DIR = Path(viewer.__file__).parent
STATIC_DIR = Path(__file__).parent / "static"
VIEWER_ASSETS = {"app.js": "application/javascript", "style.css": "text/css"}
# One bundle for every page. This was nine hand-kept names, one per script
# plus the modules they imported, and a page referencing a file missing from
# it 404s at runtime with nothing to catch it -- which is what the test named
# "every script a page asks for is actually served" exists for.
STATIC_ASSETS = {"site.js": "application/javascript",
                 "site.css": "text/css"}


@lru_cache(maxsize=16)
def _asset_bytes(path: Path, mtime_ns: int, size: int) -> tuple[bytes, str]:
    """A shipped file and a tag of its bytes. The stat is part of the key so
    a file rebuilt under a running server is read again, not served stale."""
    body = path.read_bytes()
    return body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def asset_revalidated(request: Request, path: Path, media_type: str) -> Response:
    """The viewer's code, which the browser may keep but has to ask about.

    A deploy changes app.js without changing its URL. FileResponse sent it
    with a Last-Modified and no Cache-Control, which leaves the browser to
    guess a lifetime -- a tenth of the file's age -- and a reload re-asks for
    the page but takes a still-fresh script from cache. So a deploy reached a
    new browser and not a reloaded one. no-cache makes every load ask.

    FileResponse also answers If-None-Match with the whole file, so the tag
    is checked here, as json_revalidated does for the timeline. It is a hash
    of the bytes rather than FileResponse's mtime tag: every image stamps a
    new mtime, which would cost a download on every deploy, changed or not.
    """
    st = path.stat()
    body, tag = _asset_bytes(path, st.st_mtime_ns, st.st_size)
    headers = {"ETag": tag, "Cache-Control": "public, no-cache"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)
# A whole-document save, and a merge patch. The first was 10 MB, which is ten
# times what Firestore will hold in one document -- the commit fails with an
# InvalidArgument the API turns into a bare 500, and MemoryRepo never
# reproduces it, so nothing caught it. Both are request caps; the size the
# stored document may reach is MAX_STORED_OVERRIDES_BYTES, checked on the
# merged result, because many small merges add up where one request does not.
MAX_OVERRIDES_BYTES = 700_000
MAX_MERGE_BYTES = 100_000
# A flag is a sentence or three about one rock. These bound what a stranger
# can make us store, and the budget is a flag's own, like the chart's, so
# flagging a game never costs anyone a submission.
MAX_FLAG_BYTES = 8_000
MAX_FLAG_NOTE = 2_000
FLAG_HOUR_LIMIT = 20
FLAG_DAY_LIMIT = 100
# The page a flag was sent from: an edit, view-only or review link.
FLAG_PATH = re.compile(r"^/(c|s|g)/([A-Za-z0-9_-]+)/?$")
_PLACE_INTS = ("game_index", "end", "rock")
_PLACE_TEXT = {"end_id": 40, "rock_id": 40, "key": 40, "label": 80}
PLACE_INT_MAX = 1_000_000
# Two days: past the longest video we accept (Settings.max_hours).
FLAG_VIDEO_S_MAX = 172_800
# Starting a chart on a game we already hold queues no work, so it gets a
# budget of its own rather than one of the five submissions an hour.
CHART_RATE_FACTOR = 6
# Minutes each stage usually takes on the home box, for the status page's ETA.
# Insertion order is the order the status page shows them, and it mirrors
# analyze.PHASES -- where the board is now read before the ends are built.
#
# "scoreboard" is an estimate, not a measurement: the worker now runs this
# pass (skip_scoreboard=False) instead of always skipping it, so a number was
# needed with no hosted run yet to time. read_game_board stops at the first
# usable board state, walking backwards from the game's end -- normally one
# read, occasionally a second when a card row is occluded or the board is
# briefly blank -- against the roughly twelve reads the old fixed-interval
# sweep took over a game. Each read is a keyframe_sweep of at most 40 frames
# over a bounded ~180 s window, comparable in frame count to "calibrate"'s 24
# frames (budgeted 0.5 min) but, like calibrate, against the full-resolution
# original rather than the cheap strip proxy every other stage reads from.
# Call one read about as costly as calibrate and budget two of them.
PHASE_BUDGET_MIN = {"download": 3, "proxy": 9, "calibrate": 0.5, "profile": 1,
                    "scoreboard": 1.0, "detect": 9, "rules": 1, "upload": 0.5}
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
    # How many proxies append to X-Forwarded-For before us: 1 for Cloud Run
    # (a domain mapping included), 2 behind an external HTTPS load balancer,
    # which appends its own address after the caller's. See client_ip.
    proxy_hops: int = 1
    ip_salt: str = "curling"
    lease_s: float = LEASE_S
    # Firebase's web config. None of it is secret -- the apiKey is a public
    # project identifier that ships in the source of every Firebase app -- so
    # it lives in plain env vars, and what actually limits abuse is the
    # Authorized domains list in the Auth console.
    firebase_project: str = ""
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    auth_emulator: str = ""

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
            proxy_hops=int(env("PROXY_HOPS", "1")),
            ip_salt=env("IP_SALT", "curling"),
            firebase_project=env("FIREBASE_PROJECT", ""),
            firebase_api_key=env("FIREBASE_API_KEY", ""),
            firebase_auth_domain=env("FIREBASE_AUTH_DOMAIN", ""),
            auth_emulator=env("FIREBASE_AUTH_EMULATOR_HOST", ""),
        )


def utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def clean_place(place) -> dict:
    """The browser's account of which rock, checked field by field.

    Every field may be null: a blank rock has no video time and an end with
    no rocks has no rock. Raises ValueError naming the first wrong field.
    """
    if not isinstance(place, dict):
        raise ValueError("place must be an object")
    out = {}
    for k in _PLACE_INTS:
        v = place.get(k)
        # Bounded: Firestore holds int64, and a 25-digit end fails the write.
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)
                              or abs(v) > PLACE_INT_MAX):
            raise ValueError(f"place.{k} must be a whole number")
        out[k] = v
    for k, most in _PLACE_TEXT.items():
        v = place.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            v = str(v)   # an identity is the detector's number unless renumbered
        if isinstance(v, str):
            v = printable(v, keep="")
        if v is not None and (not isinstance(v, str) or len(v) > most):
            raise ValueError(f"place.{k} must be text of at most {most} characters")
        out[k] = v
    t = place.get("t_video_s")
    # A range check rather than math.isfinite: it refuses inf (1e400 parses
    # to it), whose storage makes every later admin list and backup fail to
    # serialise, and NaN (every comparison with it is false), and a 400-digit
    # int, which isfinite cannot even convert. Python compares int and float
    # exactly, so none of these overflow here.
    if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float))
                          or not 0 <= t <= FLAG_VIDEO_S_MAX):
        raise ValueError("place.t_video_s must be a number of seconds")
    out["t_video_s"] = None if t is None else float(t)
    return out


# Right-to-left and isolate overrides: invisible, and they reorder what is
# printed after them, so a note could make the owner's terminal lie.
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def printable(text: str, keep: str = "\n\t") -> str:
    """`text` without control characters (but `keep`), bidi overrides or lone
    surrogates. A flag is read in the owner's terminal, where an escape code
    runs -- OSC 52 writes the clipboard -- and a lone surrogate cannot be
    encoded at all, so every later listing of it would fail."""
    return "".join(ch for ch in text
                   if ch in keep or not (unicodedata.category(ch) in ("Cc", "Cs")
                                         or ord(ch) in _BIDI))


def _no_constants(name):
    # NaN and Infinity are not JSON; Python's parser accepts them by default.
    raise ValueError(f"{name} is not JSON")


def _flag_json(flag) -> dict:
    return {**flag.to_dict(), "created_at": _iso(flag.created_at),
            "resolved_at": _iso(flag.resolved_at)}


def create_app(repo, store, youtube, settings: Settings, now=utcnow, auth=None) -> FastAPI:
    app = FastAPI(title="Curling Chart", docs_url=None, redoc_url=None)
    # Keyword with a default, so every caller that predates accounts keeps
    # working and gets a service with no accounts in it.
    auth = auth or NoAuth()

    # ------------------------------------------------------------ helpers
    def bearer_ok(header, expected) -> bool:
        """Whether the request carries this token.

        Both sides are stripped. A secret is very often created by piping
        something into `gcloud secrets create`, and `print`, `echo` and most
        editors add a trailing newline; the client's shell then drops it in
        command substitution, and the two no longer match. Comparing the
        meaningful characters costs nothing and removes a whole class of
        "401 and no idea why".
        """
        if not expected or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):].strip(), expected.strip())

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

    def user_for_claims(claims: dict) -> User:
        """The person behind a verified token, created on first sight."""
        uid = claims["sub"]                      # the Firebase UID, not Google's
        t = now()
        got = repo.get_user(uid)
        if got is None:
            got = User(id=uid, created_at=t, last_seen_at=t,
                       email=(claims.get("email") or "").lower() or None,
                       email_verified=bool(claims.get("email_verified")),
                       name=claims.get("name"), picture_url=claims.get("picture"))
            repo.put_user(got)
            return got
        # Throttled hard: without this every authenticated request is a write,
        # and writes are the quota that runs out.
        if got.last_seen_at is None or (t - got.last_seen_at).total_seconds() > 3600:
            got = repo.update_user(uid, last_seen_at=t) or got
        return got

    def current_user_or_none(authorization) -> User | None:
        """Who this request is, if it says so.

        Never raises. A missing, expired, malformed or foreign token all mean
        the same thing -- we do not know who this is -- and that is a state
        every route here already handles, because it is the only state that
        existed before accounts did.
        """
        if not auth.enabled or not authorization or not authorization.startswith("Bearer "):
            return None
        claims = auth.verify(authorization[len("Bearer "):].strip())
        if not claims or not claims.get("sub"):
            return None
        return user_for_claims(claims)

    def require_user(authorization) -> User:
        if not auth.enabled:
            raise HTTPException(503, "accounts are not configured")
        got = current_user_or_none(authorization)
        if got is None:
            raise HTTPException(401, "sign in to do that")
        return got

    def owner_key_for(chart: Chart) -> str | None:
        """What a chart dedupes against, or None to never dedupe.

        An anonymous chart has no owner and so keeps today's behaviour exactly:
        every submission gets its own link.
        """
        return chart.team_id or chart.owner_user_id

    def may_use_team(user: User | None, team_id: str | None) -> bool:
        return bool(user and team_id and user.id in repo.team_members(team_id))

    def client_ip(request: Request) -> str:
        """The caller's address, as our own proxies saw it.

        Cloud Run's front end appends the address it saw to whatever
        X-Forwarded-For the caller sent, so only the entries our proxies
        appended are facts -- the last `proxy_hops` of them -- and anything
        before is the caller's to invent. Taking the first entry, as this
        used to, gave anyone a fresh rate-limit budget per forged header.
        """
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            if hops:
                return hops[-min(max(settings.proxy_hops, 1), len(hops))]
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

    def game_doc(run: Run, game_index: int | None, src: Source | None = None,
                 start_s: float | None = None) -> dict:
        """The pristine document, cut down to one game.

        ``start_s`` is the time the person who made this chart said the game
        starts at, and it trims the practice off the front -- per chart, since
        the run's games are shared and only the chart knows what was asked
        for. The source-level review routes below pass nothing and so keep
        seeing the whole of what was detected.
        """
        doc = json.loads(json.dumps(load_doc(run.timeline_key)))  # a private copy
        if game_index is not None:
            doc["games"] = [g for g in doc["games"] if g["index"] == game_index]
        doc = timeline.trim_to_start(doc, start_s)
        game = doc["games"][0] if doc["games"] else None
        doc["source"]["start_s"] = game["start_s"] if game else None
        # Who played, if anybody has said. The names live on the source rather
        # than in the document because they are learned long after the run
        # that produced it, and every chart pinned to that run should get them
        # -- including the ones made before anyone typed them in.
        if game is not None and src is not None:
            for colour, name in (("red", src.team_red), ("yellow", src.team_yellow)):
                if name:
                    game.setdefault("teams", {}).setdefault(colour, {})["name"] = name
        return doc

    def learn_play_start(src: Source, run: Run) -> Source:
        """Teach a game where its play begins, from what its charters typed.

        The earliest start time anybody gave for this game, kept on the source
        so that every view of it agrees -- the review link, the catalogue, and
        charts made later by people who typed nothing. Earliest rather than
        latest because the trim's own guard is what makes a time safe: it only
        drops leading ends short of a full sixteen rocks, so a time that is
        too early simply trims less. Taking the latest would let one careless
        entry eat real ends out of everybody else's view of the game.

        The end count is stored alongside, because the catalogue lists many
        games and must not load a 300 KB document for each one to say "7".
        """
        # charts_for_run rather than a charts_for_source query: every chart of
        # this game is pinned to this run, both repos already answer it, and
        # it needs no new Firestore index.
        times = [c.requested_start_s for c in repo.charts_for_run(run.id)
                 if c.source_id == src.id and c.requested_start_s is not None]
        start = min(times) if times else None
        if start is None or run.timeline_key is None:
            return src
        try:
            doc = game_doc(run, src.game_index, None, start)
        except KeyError:
            return src
        ends = doc["games"][0]["ends"] if doc.get("games") else []
        if not ends or timeline.ends_trimmed(doc) == 0:
            return src
        if src.play_start_s == float(ends[0]["start_s"]) and src.play_ends == len(ends):
            return src
        return repo.update_source(src.id, play_start_s=float(ends[0]["start_s"]),
                                  play_ends=len(ends))

    def learn_play_starts_for_run(run: Run) -> None:
        for src in repo.sources_for_video(run.video_id):
            if src.current_run_id == run.id:
                learn_play_start(src, run)

    def trim_start_for(chart: Chart, src: Source | None) -> float | None:
        """Where this chart's game starts: the game's own answer, or its own.

        The source wins once the game has one, so two charts of one game never
        disagree about where it began. A chart whose source has not learned
        yet still honours the time its submitter gave.
        """
        if src is not None and src.play_start_s is not None:
            return src.play_start_s
        return chart.requested_start_s

    def chart_doc(chart: Chart, run: Run, read_only: bool) -> dict:
        """One game, plus what the people holding this link may do with it."""
        src = repo.get_source(chart.source_id) if chart.source_id else None
        doc = game_doc(run, chart.game_index, src, trim_start_for(chart, src))
        doc["chart"] = {
            "slug": chart.id,
            "share_url": None if read_only else url_for(f"/s/{chart.share_slug}/"),
            "read_only": read_only,
            "requested_start_s": chart.requested_start_s,
            # How many leading ends the start time took off, so the viewer can
            # say so rather than leave the charter wondering where end 1 went.
            "ends_trimmed": timeline.ends_trimmed(doc),
            "snap_distance_s": chart.snap_distance_s,
            "game_index": chart.game_index,
            "title": run.title,
            "league": run.league,
        }
        return doc

    def json_revalidated(request: Request, payload: dict, scope: str) -> Response:
        """JSON the browser may keep, but has to ask about before using.

        A chart's document changes without its URL changing: a start time
        trims the practice off it, a team name is typed in, a reprocess moves
        it to a newer run. The `max-age=3600` this replaces meant none of that
        reached anybody who had the page open in the last hour, and there was
        nothing on the page to tell them they were looking at a stale one.

        The tag is a hash of the bytes actually being sent rather than of
        anything upstream of them, so it cannot drift from what it names --
        two charts of one game with different start times get different tags
        because they *are* different documents. A 304 still keeps the 300 KB
        off the wire, which is all the max-age was ever buying.
        """
        body = json.dumps(payload, separators=(",", ":")).encode()
        tag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers = {"ETag": tag, "Cache-Control": f"{scope}, no-cache"}
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    def lookup(kind: str, key: str):
        chart = repo.get_chart(key) if kind == "c" else repo.chart_by_share(key)
        if chart is None:
            raise HTTPException(404, "no such chart")
        # A link that lost a race to a teammate keeps working: follow the
        # pointer here rather than redirecting, so the URL somebody wrote down
        # never breaks and app.js's relative POSTs land on the right chart
        # without knowing any of this happened. Chains are one deep by
        # construction -- a claim never changes hands -- but bound it anyway.
        for _ in range(4):
            if not chart.superseded_by:
                break
            nxt = repo.get_chart(chart.superseded_by)
            if nxt is None:
                break
            chart = nxt
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
        # Leases are reclaimed lazily, inside the next claim. With one worker
        # busy on a long job that can be half an hour away, so a job whose
        # worker died still reads as "processing" with a frozen fraction. Say
        # what is true: it is waiting to be picked up again.
        stalled = (job is not None and job.state == "running"
                   and job.lease_expires_at is not None
                   and job.lease_expires_at < t)
        status = "queued" if stalled and run.status == "processing" else run.status
        out = {
            "status": status,
            "stalled": stalled, "title": run.title, "league": run.league,
            "worker_online": online, "read_only": read_only,
            "phase": None, "fraction": None, "message": None, "eta_s": None,
            "position": None, "game": None, "other_games": [],
            "elapsed_s": None, "phase_elapsed_s": None, "phases": [], "attempt": None,
            # Only a run that actually failed should show an error. A job that
            # hit a hiccup and was retried is not a failure the reader can act
            # on, and showing it makes a healthy run look broken.
            "error": run.error if run.status == "failed" else None,
        }
        if status in ("queued", "pending_approval") and job is not None and not stalled:
            out["position"] = repo.queue_position(job.id)
        if status == "processing" and job is not None:
            out.update(phase=job.phase, fraction=job.fraction, message=job.message,
                       attempt=job.attempts)
            if job.started_at:
                out["elapsed_s"] = round((t - job.started_at).total_seconds())
            if job.phase_started_at:
                out["phase_elapsed_s"] = round((t - job.phase_started_at).total_seconds())
            # Every stage, with what it cost -- a finished phase reports its own
            # duration rather than an estimate, so the page stops guessing as
            # the job goes on.
            done = job.phase_timings or {}
            i = PHASE_ORDER.index(job.phase) if job.phase in PHASE_ORDER else -1
            out["phases"] = [
                {"name": p,
                 "state": ("done" if p in done else
                           "running" if p == job.phase else
                           "past" if i >= 0 and k < i else "todo"),
                 "took_s": done.get(p),
                 "budget_s": round(PHASE_BUDGET_MIN[p] * 60)}
                for k, p in enumerate(PHASE_ORDER)
            ]
            if i >= 0:
                rest = sum(PHASE_BUDGET_MIN[p] for p in PHASE_ORDER[i + 1:])
                here = PHASE_BUDGET_MIN[job.phase] * (1.0 - (job.fraction or 0.0))
                out["eta_s"] = round((rest + here) * 60)
        if status == "ready" and chart.game_index is not None:
            # The game this chart is on reports what the chart shows, warm-up
            # already taken off; the others report what was detected, since
            # nobody has said where their play starts.
            src = repo.get_source(chart.source_id) if chart.source_id else None
            for g in run.games:
                entry = {"index": g["index"], "start_s": g["start_s"],
                         "end_s": g["end_s"], "ends": g.get("ends")}
                if g["index"] == chart.game_index:
                    if src is not None and src.play_start_s is not None:
                        entry |= {"start_s": src.play_start_s, "ends": src.play_ends}
                    out["game"] = {**entry, "snap_distance_s": chart.snap_distance_s}
                else:
                    out["other_games"].append(entry)
        return out

    def existing_chart_for(owner_key, run, start_s) -> Chart | None:
        """The chart this owner already has for the game being asked for.

        Only answerable once the run is ready, because only then is it known
        which game a start time lands in. Before that a second chart is made
        and resolve_chart folds it away when the games arrive.
        """
        if not owner_key or run.status != "ready":
            return None
        index, _d = dedupe.snap_to_game(run.games, start_s)
        if index is None:
            return None
        game = next((g for g in run.games if g["index"] == index), None)
        if game is None:
            return None
        src = dedupe.find_or_create_source(repo, run, game, now())
        found = repo.chart_claim(owner_key, src.id)
        return repo.get_chart(found) if found else None

    def new_chart(video_id, run, start_s, sheet, iph, t, user=None,
                  team_id=None) -> tuple[Chart, bool]:
        """This owner's chart for this game, made if they have not got one.

        Anonymous submissions never dedupe -- no owner, no claim -- so the
        behaviour anyone relied on before accounts existed is unchanged.
        """
        owner_key = team_id or (user.id if user else None)
        found = existing_chart_for(owner_key, run, start_s)
        if found is not None:
            return found, True
        chart = Chart(id=slug.new_slug(), share_slug=slug.new_slug(), video_id=video_id,
                      run_id=run.id, created_at=t, updated_at=t,
                      requested_start_s=start_s, requested_sheet=sheet,
                      submitter_ip_hash=iph,
                      owner_user_id=user.id if user else None, team_id=team_id)
        repo.put_chart(chart)
        if run.status == "ready":
            chart = dedupe.resolve_chart(repo, chart, run, t)
            if chart.superseded_by:                    # lost a race just now
                return repo.get_chart(chart.superseded_by), True
            if chart.source_id and (src := repo.get_source(chart.source_id)):
                learn_play_start(src, run)
        return chart, False

    # ------------------------------------------------------------- public
    @app.get("/", response_class=HTMLResponse)
    def home():
        return page("submit.html")

    @app.get("/games", response_class=HTMLResponse)
    def games_page():
        return page("games.html")

    @app.get("/mine", response_class=HTMLResponse)
    def mine_page():
        return page("mine.html")

    @app.get("/join/{token}", response_class=HTMLResponse)
    def join_page(token: str):
        # The token is read back off the URL by the page itself; nothing is
        # rendered into the HTML, so there is nothing here to escape.
        return page("join.html")

    @app.get("/static/{name}")
    def static_asset(name: str, request: Request):
        if name not in STATIC_ASSETS:
            raise HTTPException(404)
        return asset_revalidated(request, STATIC_DIR / name, STATIC_ASSETS[name])

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
                "team_red": s.team_red, "team_yellow": s.team_yellow,
                "game_index": s.game_index,
                "start_s": s.play_start_s if s.play_start_s is not None else s.game_start_s,
                "end_s": s.game_end_s, "status": run.status if run else None,
                "ends": s.play_ends if s.play_ends is not None else
                        next((g.get("ends") for g in (run.games if run else [])
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

    @app.post("/api/games/{source_id}/league")
    def api_set_league(source_id: str, body: dict,
                       authorization: str | None = Header(default=None)):
        """Name the league a game belongs to, for the games nothing named.

        The playlist watcher stamps the league it found a recording in, so
        everything it queues arrives labelled. A link somebody pastes by hand
        arrives with nothing, and an unlabelled game cannot be filtered for or
        grouped -- it is only findable by scrolling to its date.

        One recording is one sheet for one night, so every game in it belongs
        to the same league and one edit names them all. It is written to the
        runs as well: a reprocess builds its new run from the last one, and a
        label that survived only on the source would be a label that a
        reprocess quietly dropped.
        """
        me = require_user(authorization)
        label = str(body.get("league") or "").strip()
        if len(label) > 60:
            raise HTTPException(422, "that name is too long for a league")
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        league = label or None
        games = repo.sources_for_video(src.video_id)
        for s in games:
            repo.update_source(s.id, league=league)
        for run in repo.runs_for_video(src.video_id):
            repo.update_run(run.id, league=league)
        log.info("league %r set on %s (%d games) by %s",
                 league, src.video_id, len(games), me.id)
        return {"ok": True, "league": league, "games": len(games)}

    @app.post("/api/admin/games/{source_id}/play-start")
    def admin_set_play_start(source_id: str, body: dict | None = None,
                             authorization: str | None = Header(default=None)):
        """Say where a game's play begins, for one charted before we asked.

        `learn_play_start` only runs when a chart resolves, so a game charted
        before any of this existed keeps its warm-up on every surface with no
        way to shift it. Passing `start_s` trims to that time; passing null
        clears the boundary and puts the whole detected game back.
        """
        require_admin(authorization)
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        run = repo.get_run(src.current_run_id)
        if run is None or run.timeline_key is None:
            raise HTTPException(409, "that game has no finished run")
        raw = (body or {}).get("start_s")
        if raw in (None, ""):
            src = repo.update_source(src.id, play_start_s=None, play_ends=None)
            return {"ok": True, "play_start_s": None, "play_ends": None}
        try:
            start = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "give start_s as a number of seconds")
        doc = game_doc(run, src.game_index, None, start)
        ends = doc["games"][0]["ends"] if doc.get("games") else []
        if not ends:
            raise HTTPException(409, "that game has no ends")
        src = repo.update_source(src.id, play_start_s=float(ends[0]["start_s"]),
                                 play_ends=len(ends))
        log.info("play starts at %s on %s, %d ends, %d trimmed",
                 src.play_start_s, src.id, src.play_ends, timeline.ends_trimmed(doc))
        return {"ok": True, "play_start_s": src.play_start_s,
                "play_ends": src.play_ends,
                "ends_trimmed": timeline.ends_trimmed(doc)}

    @app.post("/api/games/{source_id}/teams")
    def api_set_teams(source_id: str, body: dict,
                      authorization: str | None = Header(default=None)):
        """Who played this game, by the colour they threw.

        Per game, where the league is per recording: a night on one sheet is
        one league and two different pairs of teams. Nothing detects this --
        the club puts it in some titles and not others, and the cameras read
        stones rather than scoreboards -- so it is typed in or it is unknown.

        It is served into the timeline of every chart of this game, including
        charts made before anyone knew, which is why it lives on the source
        and not in the document the run produced.
        """
        me = require_user(authorization)
        names = {}
        for colour in ("red", "yellow"):
            if colour not in body:
                continue
            name = str(body.get(colour) or "").strip()
            if len(name) > 60:
                raise HTTPException(422, "that name is too long for a team")
            names["team_" + colour] = name or None
        if not names:
            raise HTTPException(400, "name red, yellow, or both")
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        repo.update_source(src.id, **names)
        log.info("teams %s set on %s by %s", names, src.id, me.id)
        return {"ok": True, **{c: names.get("team_" + c, getattr(src, "team_" + c))
                               for c in ("red", "yellow")}}

    @app.post("/api/admin/relabel")
    def admin_relabel(authorization: str | None = Header(default=None)):
        """Name the leagues of everything that predates reading them off titles.

        A one-shot for what is already stored. New runs get it at submission
        and again on completion; this is for the games that arrived before
        there was anything to derive it from. It only ever fills a blank.
        """
        require_admin(authorization)
        runs = games = 0
        for run in repo.list_runs():
            if run.league or not run.title:
                continue
            if league := source.league_from_title(run.title):
                repo.update_run(run.id, league=league)
                runs += 1
        for s in repo.list_sources():
            if s.league:
                continue
            run = repo.get_run(s.current_run_id)
            league = (run.league if run else None) or source.league_from_title(s.title or "")
            if league:
                repo.update_source(s.id, league=league)
                games += 1
        log.info("relabelled %d runs and %d games from their titles", runs, games)
        return {"ok": True, "runs": runs, "games": games}

    @app.post("/api/submissions", status_code=201)
    async def submit(request: Request, authorization: str | None = Header(default=None)):
        me = current_user_or_none(authorization)
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
        # How much of the video to read. Without it a stream under
        # dedupe.MAX_WHOLE_S is analysed whole, so submitting one game out of
        # a four-hour night costs the whole night.
        duration_s = body.get("duration_s")
        if duration_s in (None, ""):
            duration_s = None
        else:
            try:
                duration_s = float(duration_s)
            except (TypeError, ValueError):
                raise HTTPException(400, "give the length as a number of seconds")
            if duration_s <= 0:
                raise HTTPException(400, "the length must be more than zero")
        sheet = body.get("sheet")
        sheet = None if sheet in (None, "") else int(sheet)
        t = now()
        iph = ip_hash(request)
        team_id = body.get("team_id") or None
        if team_id and not may_use_team(me, team_id):
            raise HTTPException(403, "you are not on that team")

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
                w0, w1 = dedupe.window_for(meta.duration_s, start_s, duration_s)
            except dedupe.NeedsStartTime:
                raise HTTPException(422, "that stream is very long -- give the time "
                                         "the game starts (e.g. paste a link with ?t=)")
            status = "pending_approval" if settings.require_approval else "queued"
            run = Run(id=slug.new_run_id(), video_id=link.video_id,
                      processing_version=settings.processing_version, status=status,
                      created_at=t, window_start_s=w0, window_end_s=w1,
                      title=meta.title, channel_id=meta.channel_id,
                      duration_s=meta.duration_s, published_at=meta.published_at,
                      sheet=sheet if sheet is not None else source.sheet_from_title(meta.title),
                      # Nothing queued this from a playlist, so there is no
                      # label to take; the club's titles carry the league in
                      # the same breath as the sheet.
                      league=source.league_from_title(meta.title))
            repo.put_run(run)
            if status == "queued":
                repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued",
                                 created_at=t, run_after=t))
        chart, reused_chart = new_chart(link.video_id, run, start_s, sheet, iph, t,
                                        user=me, team_id=team_id)
        job = repo.job_for_run(run.id)
        return {
            "slug": chart.id,
            "chart_url": url_for(f"/c/{chart.id}/"),
            "share_url": url_for(f"/s/{chart.share_slug}/"),
            "status": run.status,
            "position": repo.queue_position(job.id) if job and run.status == "queued" else None,
            "reused": reused,
            "reused_chart": reused_chart,
        }

    @app.post("/api/charts", status_code=201)
    def api_start_charting(body: dict, request: Request,
                           authorization: str | None = Header(default=None)):
        """Chart a game the catalogue already knows about.

        Separate from /api/submissions, which ingests a *video*: this queues no
        work, so it must not spend one of the five submissions an hour. It is
        also what makes coming back work -- ask twice and you get the same
        chart, rather than the blank second one the catalogue used to mint.
        """
        me = current_user_or_none(authorization)
        source_id = str(body.get("source_id") or "")
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        run = repo.get_run(src.current_run_id)
        if run is None or run.status != "ready":
            raise HTTPException(409, "that game is still processing")
        team_id = body.get("team_id") or None
        if team_id and not may_use_team(me, team_id):
            raise HTTPException(403, "you are not on that team")
        iph = ip_hash(request)
        # Its own, looser budget. Charting queues no work, so counting it
        # against submissions would make coming back to your own games cost
        # the same as asking for a new video -- but leaving it uncounted lets
        # anyone create documents without end. Only a chart that is actually
        # made is counted; reopening one you already have is free.
        if not existing_chart_for(team_id or (me.id if me else None), run, src.game_start_s):
            if not repo.bump_rate_limit("chart:" + iph, now(),
                                        settings.rate_hour * CHART_RATE_FACTOR,
                                        settings.rate_day * CHART_RATE_FACTOR):
                raise HTTPException(429, "too many new charts from this address; try later")
        chart, reused = new_chart(src.video_id, run, src.game_start_s, src.sheet,
                                  iph, now(), user=me, team_id=team_id)
        return {"slug": chart.id, "chart_url": url_for(f"/c/{chart.id}/"),
                "share_url": url_for(f"/s/{chart.share_slug}/"),
                "status": run.status, "reused_chart": reused}

    # ---------------------------------------------------- account routes
    # Accounts exist so a person can find their links again, and so a team can
    # share one. They gate nothing: every route below is additive, and a chart
    # URL keeps working for whoever holds it whether or not anyone is signed in.
    def chart_summary(chart: Chart, cache: dict | None = None) -> dict:
        # A list is mostly the same handful of runs and teams over and over.
        cache = {} if cache is None else cache
        if (run := cache.get(("run", chart.run_id), ...)) is ...:
            run = cache[("run", chart.run_id)] = repo.get_run(chart.run_id)
        team = None
        if chart.team_id:
            if (team := cache.get(("team", chart.team_id), ...)) is ...:
                team = cache[("team", chart.team_id)] = repo.get_team(chart.team_id)
        return {
            "slug": chart.id,
            "chart_url": url_for(f"/c/{chart.id}/"),
            "share_url": url_for(f"/s/{chart.share_slug}/"),
            "source_id": chart.source_id,
            "review_url": url_for(f"/g/{chart.source_id}/") if chart.source_id else None,
            "title": run.title if run else None,
            "league": run.league if run else None,
            "sheet": run.sheet if run else None,
            "played_at": _iso(run.published_at) if run else None,
            "status": run.status if run else None,
            "game_index": chart.game_index,
            "updated_at": _iso(chart.updated_at),
            "shots_charted": len(chart.overrides),
            "team_id": chart.team_id,
            "team_name": team.name if team else None,
            "superseded_by": chart.superseded_by,
            "duplicate_of": chart.duplicate_of,
        }

    def team_summary(team: Team, members: bool = False) -> dict:
        out = {"id": team.id, "name": team.name, "owner_user_id": team.owner_user_id,
               "member_count": len(team.member_ids), "created_at": _iso(team.created_at)}
        if members:
            out["members"] = [
                {"id": uid, "name": (u.name if (u := repo.get_user(uid)) else None),
                 "email": u.email if u else None, "is_owner": uid == team.owner_user_id}
                for uid in team.member_ids
            ]
        return out

    @app.get("/api/auth/config")
    def auth_config():
        """What the browser needs to start a sign-in, or that it cannot.

        Served rather than templated into each page: there are four places that
        render HTML and one of them would eventually be forgotten. With no
        project configured this reports disabled and the whole feature simply
        is not there.
        """
        if not (auth.enabled and settings.firebase_api_key):
            return JSONResponse({"enabled": False},
                                headers={"Cache-Control": "public, max-age=300"})
        return JSONResponse({"enabled": True,
                             "apiKey": settings.firebase_api_key,
                             "authDomain": settings.firebase_auth_domain,
                             "projectId": settings.firebase_project,
                             "emulator": settings.auth_emulator or None},
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/api/me")
    def api_me(authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        teams = repo.teams_for_user(me.id)
        return {"user": {"id": me.id, "email": me.email, "name": me.name,
                         "picture_url": me.picture_url},
                "teams": [team_summary(t) for t in teams]}

    @app.get("/api/me/charts")
    def api_my_charts(authorization: str | None = Header(default=None)):
        """Every game this person can get back to: their own, and their teams'."""
        me = require_user(authorization)
        seen, out = set(), []
        for chart in repo.charts_for_owner(me.id):
            seen.add(chart.id)
            out.append(chart)
        for team in repo.teams_for_user(me.id):
            for chart in repo.charts_for_team(team.id):
                if chart.id not in seen:
                    seen.add(chart.id)
                    out.append(chart)
        # A superseded chart is the same game as the one it points at; showing
        # both would be showing a duplicate the person cannot act on.
        out = [c for c in out if not c.superseded_by]
        out.sort(key=lambda c: c.updated_at, reverse=True)
        cache = {}
        return {"charts": [chart_summary(c, cache) for c in out]}

    @app.post("/api/me/charts/{chart_id}/claim")
    def api_claim_chart(chart_id: str, body: dict | None = None,
                        authorization: str | None = Header(default=None)):
        """Put a link you already hold into your list.

        Holding the chart id is the whole permission -- it is the same thing
        that lets you edit -- so nothing else is asked for. What is refused is
        claiming a chart that already belongs to a team you are not on, which
        would otherwise let anyone you shared a link with take it.
        """
        me = require_user(authorization)
        chart = repo.get_chart(chart_id)
        if chart is None:
            raise HTTPException(404, "no such chart")
        # An old link may point at the chart its team kept. Claim the live one,
        # the way opening the link would have taken you there.
        for _ in range(4):
            if not chart.superseded_by:
                break
            nxt = repo.get_chart(chart.superseded_by)
            if nxt is None:
                break
            chart = nxt
        chart_id = chart.id
        team_id = (body or {}).get("team_id")
        if chart.team_id and not may_use_team(me, chart.team_id):
            raise HTTPException(403, "this chart belongs to another team")
        if team_id and not may_use_team(me, team_id):
            raise HTTPException(403, "you are not on that team")
        fields = {"owner_user_id": chart.owner_user_id or me.id, "team_id": team_id or chart.team_id}
        chart = repo.update_chart(chart_id, **fields)
        key = owner_key_for(chart)
        if key and chart.source_id:
            repo.claim_chart(key, chart.source_id, chart.id)
        return chart_summary(chart)

    @app.post("/api/teams", status_code=201)
    def api_create_team(body: dict, authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        name = (body.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise HTTPException(422, "a team needs a name")
        team = Team(id=slug.new_team_id(), name=name, owner_user_id=me.id,
                    created_at=now(), member_ids=[me.id])
        repo.put_team(team)
        return team_summary(team, members=True)

    @app.get("/api/teams/{team_id}")
    def api_get_team(team_id: str, authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        team = repo.get_team(team_id)
        if team is None or me.id not in team.member_ids:
            raise HTTPException(404, "no such team")
        charts = [c for c in repo.charts_for_team(team_id) if not c.superseded_by]
        cache = {}
        return {**team_summary(team, members=True),
                "charts": [chart_summary(c, cache) for c in charts]}

    @app.post("/api/teams/{team_id}/invites", status_code=201)
    def api_create_invite(team_id: str, body: dict | None = None,
                          authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        team = repo.get_team(team_id)
        if team is None or me.id not in team.member_ids:
            raise HTTPException(404, "no such team")
        days = (body or {}).get("days", 14)
        invite = Invite(id=slug.new_invite_token(), team_id=team_id,
                        created_by_user_id=me.id, created_at=now(),
                        expires_at=now() + timedelta(days=float(days)))
        repo.put_invite(invite)
        return {"token": invite.id, "url": url_for(f"/join/{invite.id}"),
                "team": team.name, "expires_at": _iso(invite.expires_at)}

    @app.get("/api/invites/{token}")
    def api_preview_invite(token: str):
        """What the join page says before anyone signs in.

        Unauthenticated on purpose: telling the holder of an invite which team
        it is for is exactly what the invite is.
        """
        invite = repo.get_invite(token)
        if invite is None:
            raise HTTPException(404, "no such invitation")
        team = repo.get_team(invite.team_id)
        return {"team": team.name if team else None, "team_id": invite.team_id,
                "usable": invite.usable(now()) and team is not None}

    @app.post("/api/invites/{token}/accept")
    def api_accept_invite(token: str, authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        invite = repo.get_invite(token)
        if invite is None:
            raise HTTPException(404, "no such invitation")
        team = repo.get_team(invite.team_id)
        if team is None:
            raise HTTPException(404, "that team is gone")
        if me.id in team.member_ids:
            return team_summary(team, members=True)   # accepting twice is fine
        if not invite.usable(now()):
            raise HTTPException(410, "that invitation has expired")
        team = repo.add_member(invite.team_id, me.id)
        repo.update_invite(token, uses=invite.uses + 1)
        return team_summary(team, members=True)

    @app.delete("/api/teams/{team_id}/members/{user_id}")
    def api_remove_member(team_id: str, user_id: str,
                          authorization: str | None = Header(default=None)):
        """Leave a team, or -- if you own it -- show somebody out."""
        me = require_user(authorization)
        team = repo.get_team(team_id)
        if team is None or me.id not in team.member_ids:
            raise HTTPException(404, "no such team")
        if user_id != me.id and team.owner_user_id != me.id:
            raise HTTPException(403, "only the team's owner can remove someone else")
        if user_id == team.owner_user_id:
            raise HTTPException(409, "the owner cannot leave; hand the team over first")
        return team_summary(repo.remove_member(team_id, user_id), members=True)

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
                return page("status.html")
            # `merge` says this server takes per-key patches; the local
            # `curling-score serve` sets no window.CHART at all and so keeps
            # getting whole documents. `shared` is what turns on polling for a
            # teammate's edits -- pointless, and a read every 15s, on a chart
            # only one person can reach.
            return HTMLResponse(viewer.boot_page({
                "slug": chart.id,
                "mode": "view" if read_only else "edit",
                "merge": True,
                "shared": chart.team_id is not None,
            }))

        @app.get(prefix + "/status.json")
        def status_json(key: str):
            chart, run, read_only = lookup(kind, key)
            return status_payload(chart, run, read_only)

        @app.get(prefix + "/timeline.json")
        def timeline_json(key: str, request: Request):
            chart, run, read_only = lookup(kind, key)
            if run.status != "ready" or chart.game_index is None:
                raise HTTPException(404, "not processed yet")
            return json_revalidated(request, chart_doc(chart, run, read_only),
                                    "private")

        @app.get(prefix + "/overrides.json")
        def overrides_get(key: str, request: Request):
            chart, _run, _ro = lookup(kind, key)
            # A shared chart is polled for a teammate's edits; almost every one
            # of those asks finds nothing, so answer them without the body.
            tag = f'"{chart.overrides_version}"'
            if request.headers.get("if-none-match") == tag:
                return Response(status_code=304, headers={"ETag": tag})
            return JSONResponse(chart.overrides, headers={"ETag": tag})

        @app.get(prefix + "/overrides_meta.json")
        def overrides_meta_get(key: str):
            """Who last touched each shot, and when.

            Served apart from overrides.json on purpose: the viewer feeds that
            response straight into its override map, and apply_overrides copies
            a patch onto the shot wholesale, so a `by` mixed in there would ride
            into export.json and on into the training labels.
            """
            chart, _run, _ro = lookup(kind, key)
            return JSONResponse(chart.overrides_meta,
                                headers={"ETag": f'"{chart.overrides_version}"'})

        @app.post(prefix + "/overrides.json")
        async def overrides_post(key: str, request: Request,
                                 v: int | None = Query(default=None),
                                 merge: bool = Query(default=False)):
            """Save grading, whole-document or shot by shot.

            The whole-document form is what the local `curling-score serve`
            speaks and what every page sent before merging existed; it keeps its
            version check and its 409. A merge carries only the shots that
            changed and never refuses on a version -- two people editing
            different shots have nothing to conflict about, and the same shot is
            simply the later of the two.
            """
            chart, _run, read_only = lookup(kind, key)
            if read_only:
                raise HTTPException(403, "this is a view-only link")
            body = await request.body()
            if not body:
                return JSONResponse({"ok": False, "error": "empty body"}, status_code=400)
            if len(body) > (MAX_MERGE_BYTES if merge else MAX_OVERRIDES_BYTES):
                return JSONResponse({"ok": False, "error": "too large"},
                                    status_code=413 if merge else 400)
            try:
                data = json.loads(body)
            except ValueError as err:
                return JSONResponse({"ok": False, "error": f"invalid JSON: {err}"}, status_code=400)
            if not isinstance(data, dict):
                return JSONResponse({"ok": False, "error": "expected {key: patch}"}, status_code=400)

            if not merge:
                if not all(isinstance(x, dict) for x in data.values()):
                    return JSONResponse({"ok": False, "error": "expected {key: patch}"},
                                        status_code=400)
                ok, ver, current = repo.save_overrides(chart.id, data, v, now())
                if not ok:
                    return JSONResponse({"ok": False, "error": "stale", "version": ver,
                                         "overrides": current}, status_code=409)
                return {"ok": True, "shots": len(data), "version": ver}

            # A merge value is a patch, or null to delete that shot.
            if not all(x is None or isinstance(x, dict) for x in data.values()):
                return JSONResponse({"ok": False, "error": "expected {key: patch|null}"},
                                    status_code=400)
            patch = {k: x for k, x in data.items() if x is not None}
            remove = [k for k, x in data.items() if x is None]
            stamp = _iso(now())
            meta = {k: {"at": stamp} for k in patch}
            ver, merged, ok = repo.merge_overrides(chart.id, patch, remove, meta, now())
            if not ok:
                return JSONResponse({"ok": False, "error": "chart is full", "version": ver},
                                    status_code=413)
            out = {"ok": True, "shots": len(data), "version": ver}
            # Only somebody who was actually behind needs the whole map back. A
            # lone charter is always exactly one behind their own last save, and
            # a beacon sends no version because it cannot read the reply.
            if v is not None and v != ver - 1:
                out["overrides"] = merged
            return out

        @app.get(prefix + "/export.json")
        def export_json(key: str):
            chart, run, read_only = lookup(kind, key)
            if run.status != "ready":
                raise HTTPException(404, "not processed yet")
            return timeline.apply_overrides(chart_doc(chart, run, read_only), chart.overrides)

        @app.get(prefix + "/{asset}")
        def chart_asset(key: str, asset: str, request: Request):
            lookup(kind, key)
            if asset in VIEWER_ASSETS:
                return asset_revalidated(request, VIEWER_DIR / asset, VIEWER_ASSETS[asset])
            if asset in STATIC_ASSETS:
                return asset_revalidated(request, STATIC_DIR / asset, STATIC_ASSETS[asset])
            raise HTTPException(404)

    register_chart_routes("c")
    register_chart_routes("s")

    # ------------------------------------------------------ review routes
    # Watching a game without charting it. A source is already the thing a
    # person means by "this game", and the catalogue already hands out its id,
    # so this surface needs no record of its own -- which is the point: no
    # chart is created by browsing, and nothing here can write. Read-only is a
    # property of the surface, not a check inside it; there is no POST route to
    # forget to guard.
    def lookup_source(sid: str) -> tuple[Source, Run]:
        src = repo.get_source(sid)
        if src is None:
            raise HTTPException(404, "no such game")
        run = repo.get_run(src.current_run_id)
        if run is None or run.status != "ready":
            raise HTTPException(404, "not processed yet")
        return src, run

    @app.get("/g/{sid}", include_in_schema=False)
    def review_redirect(sid: str):
        return RedirectResponse(f"/g/{sid}/", status_code=302)

    @app.get("/g/{sid}/", response_class=HTMLResponse)
    def review_page(sid: str):
        lookup_source(sid)
        return HTMLResponse(viewer.boot_page({"mode": "review", "source": sid}))

    @app.get("/g/{sid}/timeline.json")
    def review_timeline(sid: str, request: Request):
        src, run = lookup_source(sid)
        doc = game_doc(run, src.game_index, src, src.play_start_s)
        # No slug and no share_url: this link is already the public one.
        doc["chart"] = {"read_only": True, "review": True,
                        "title": run.title, "league": run.league,
                        "ends_trimmed": timeline.ends_trimmed(doc)}
        return json_revalidated(request, doc, "public")

    @app.get("/g/{sid}/export.json")
    def review_export(sid: str):
        src, run = lookup_source(sid)
        return game_doc(run, src.game_index, src, src.play_start_s)

    @app.get("/g/{sid}/{asset}")
    def review_asset(sid: str, asset: str, request: Request):
        lookup_source(sid)
        if asset in VIEWER_ASSETS:
            return asset_revalidated(request, VIEWER_DIR / asset, VIEWER_ASSETS[asset])
        if asset in STATIC_ASSETS:
            return asset_revalidated(request, STATIC_DIR / asset, STATIC_ASSETS[asset])
        raise HTTPException(404)

    # ------------------------------------------------------------- flags
    # Anyone looking at a chart may say what is wrong with it. One route for
    # all three kinds of link rather than one under each: /g/ carries no POST
    # route at all (see lookup_source), and one route is one budget to count.
    # The link arrives as the page's own path and is resolved here, so where a
    # flag says it was is the server's word, never the browser's.
    @app.post("/api/flags", status_code=201)
    async def create_flag(request: Request, authorization: str | None = Header(default=None)):
        raw = await request.body()
        if len(raw) > MAX_FLAG_BYTES:
            raise HTTPException(413, "that flag is too long")
        try:
            body = json.loads(raw, parse_constant=_no_constants)
        except ValueError:
            raise HTTPException(400, "send JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "send a JSON object")
        m = FLAG_PATH.match(str(body.get("path") or ""))
        if not m:
            raise HTTPException(400, "path must be a chart or game link")
        kind, key = m.groups()
        note = body.get("note")
        note = printable(note).strip() if isinstance(note, str) else ""
        if not note:
            raise HTTPException(422, "say what is wrong")
        if len(note) > MAX_FLAG_NOTE:
            raise HTTPException(422, f"keep it under {MAX_FLAG_NOTE} characters")
        try:
            place = clean_place(body.get("place"))
        except ValueError as err:
            raise HTTPException(422, str(err)) from None
        if kind == "g":
            src, run = lookup_source(key)
            chart = None
        else:
            chart, run, _ = lookup(kind, key)
            src = repo.get_source(chart.source_id) if chart.source_id else None
        iph = ip_hash(request)
        if not repo.bump_rate_limit("flag:" + iph, now(), FLAG_HOUR_LIMIT, FLAG_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of flags; try again later")
        user = current_user_or_none(authorization)
        flag = Flag(
            id=slug.new_flag_id(), created_at=now(), note=note, place=place,
            where={"link": kind,
                   "chart_id": chart.id if chart else None,
                   "share_slug": chart.share_slug if chart else None,
                   "source_id": src.id if src else None,
                   "run_id": run.id, "video_id": run.video_id,
                   "processing_version": run.processing_version,
                   "title": run.title},
            overrides_version=chart.overrides_version if chart else None,
            user={"uid": user.id, "email": user.email} if user else None,
            ip_hash=iph)
        repo.put_flag(flag)
        return {"id": flag.id}

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
        # Whatever went wrong last time is history now; leaving it set means the
        # status page shows a stale failure through an entirely healthy run.
        repo.update_job(job.id, error=None, error_kind=None, phase=None,
                        fraction=None, message=None, phase_started_at=t,
                        phase_timings={})
        repo.update_run(job.run_id, error=None)
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
        phase = body.get("phase")
        fields = {"phase": phase, "fraction": body.get("fraction"),
                  "message": body.get("message"), "progress_at": t,
                  "lease_expires_at": lease}
        if phase != job.phase:
            # A phase boundary: bank how long the last one took, and start the
            # clock on this one.
            if job.phase and job.phase_started_at:
                timings = dict(job.phase_timings or {})
                timings[job.phase] = round((t - job.phase_started_at).total_seconds(), 1)
                fields["phase_timings"] = timings
            fields["phase_started_at"] = t
        repo.update_job(job.id, **fields)
        # A progress post proves the worker is alive. Without this the only
        # heartbeat is the claim, so a worker goes "offline" 90 seconds into
        # every job and the page tells the user their game is not being worked
        # on while it plainly is.
        if job.worker_id:
            known = {w.id: w for w in repo.workers()}.get(job.worker_id)
            repo.heartbeat(Worker(
                id=job.worker_id, last_seen_at=t, current_job_id=job.id,
                version=getattr(known, "version", None),
                model_id=getattr(known, "model_id", None),
                gpu=getattr(known, "gpu", None)))
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
        title = body.get("title", run.title)
        repo.update_run(run.id, status="ready", ready_at=t, games=games,
                        title=title,
                        # A label somebody set, or a playlist gave, outranks a
                        # title read by pattern -- this only fills a blank.
                        league=run.league or source.league_from_title(title),
                        channel_id=body.get("channel_id", run.channel_id),
                        duration_s=body.get("duration_s", run.duration_s),
                        sheet=body.get("sheet", run.sheet),
                        timeline_key=tkey, meta_key=meta_key(run.video_id, run.id),
                        error=None)
        repo.update_job(job.id, state="done", finished_at=t, phase="done", fraction=1.0)
        run = repo.get_run(run.id)
        resolved = dedupe.resolve_charts_for_run(repo, run, t)
        learn_play_starts_for_run(run)
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
            # `or` would turn an explicit 0 -- "retry now" -- into 60.
            asked = body.get("retry_after_s")
            delay = float(60 if asked is None else asked)
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

    @app.get("/api/admin/flags")
    def admin_flags(status: str = "open", limit: int = Query(200, ge=1, le=1000),
                    authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if status not in ("open", "resolved", "all"):
            raise HTTPException(422, "status is open, resolved or all")
        flags = repo.list_flags(None if status == "all" else status, limit)
        return {"flags": [_flag_json(f) for f in flags]}

    @app.post("/api/admin/flags/{flag_id}/resolve")
    def admin_resolve_flag(flag_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        got = repo.resolve_flag(flag_id, now())
        if got is None:
            raise HTTPException(404, "no such flag")
        return _flag_json(got)

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
