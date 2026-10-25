"""The HTTP face of the service: submit a link, chart a game, feed the worker.

People get a submit form, a status page, the charting viewer under
``/c/{slug}/``, a read-only twin under ``/s/{share}/``, and -- for anyone who
only wants the shot-to-shot navigation -- a public, chart-less ``/g/{source}/``.
The worker gets a claim/progress/complete protocol behind a bearer token. The
operator gets an admin token for approvals, retries, playlists and a backup.

The three human surfaces differ in what holding the URL means. ``/g/`` is
public and grants nothing; ``/s/`` is unguessable and shows a chart read-only.
``/c/`` is unguessable too, but with accounts on, holding it lets you change
only a chart that is yours or your team's (``may_edit_chart``); anyone else
watches. The kind of URL still decides the most a link can do.

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
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.gzip import GZipMiddleware

from curling_score import autoreview, timeline, version, viewer
from curling_score.game import format as format_mod
from curling_score.ingest import source
from curling_score.service import (dedupe, gameclips, labelling, playlist_index, playlists,
                                   practice, signin, slug, team_names, thinking_report)
from curling_score.service.auth import NoAuth
from curling_score.service.records import (
    JOB_KINDS, LABEL_KINDS, ROLES, ClipJob, Chart, Flag, Invite, Job, LabelAnswer, LabelItem, Play,
    Review, Run, SigninRelay, Source, Team, TeamNameEdit, User, Worker, label_answer_id, play_id,
    review_id,
)
from curling_score.service.repo import LEASE_S, worker_online
from curling_score.service.store import (clip_key, detcache_key, meta_key, practice_key,
                                         timeline_key)
from curling_score.service.youtube import SubmissionError, validate_submission

log = logging.getLogger(__name__)

VIEWER_DIR = Path(viewer.__file__).parent
STATIC_DIR = Path(__file__).parent / "static"
# What "every game" means to the report, its backfill and the league stamp.
# list_sources defaults to the newest 500; the report must see a league's old
# games too.
EVERY_SOURCE = 5000
# The catalogue opens on this many of the club's days of play, ending with the
# newest game's, and goes back OLDER_DAYS at a time.
LANDING_DAYS, OLDER_DAYS = 14, 7
# Every league there is, for the filter: a read of the whole catalogue, so kept
# this long per instance. A league edit here drops it at once.
LEAGUES_TTL_S = 600.0
VIEWER_ASSETS = {"app.js": "application/javascript", "style.css": "text/css",
                 "source-sans-3.woff2": "font/woff2"}
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
# How long a job YouTube has refused ("blocked") waits before each try. A block
# is the worker's address, not the job, so it spends none of the job's
# attempts; the ladder is what bounds it, about six and a half hours in all.
# 10/16 00:03-02:07 a bot check held for two hours, and the old 5/10/20-minute
# ladder, drawn from the four attempts, could not have outlasted it.
BLOCKED_LADDER_S = (600, 1800, 3600, 7200, 7200, 7200)
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
# A labelling answer is at most MAX_BOXES boxes; this is generous for that.
MAX_LABEL_BYTES = 64_000
# Items one admin call may create, and the longest an item id may be.
MAX_LABEL_ITEMS_PER_CALL = 500
LABEL_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
LABEL_STATES = ("done", "retired", "open", "pending")
MAX_FLAG_NOTE = 2_000
FLAG_HOUR_LIMIT = 20
FLAG_DAY_LIMIT = 100
# Saying which rocks of a game you threw, or changing your mind. Generous: a
# season's worth of games tagged in one sitting is the expected use.
PLAY_HOUR_LIMIT = 60
PLAY_DAY_LIMIT = 300
# Starting practice sessions: a few an hour is a busy morning on the ice.
PRACTICE_HOUR_LIMIT = 20
PRACTICE_DAY_LIMIT = 60
# What Start says when it is refused, on the club's cameras (the page shows it).
CAMERAS_NOT_ALLOWED = ("Practice on the club's cameras needs a validated account, "
                       "or the club's Wi-Fi.")
LEAGUE_ON = "A league game is on; practice opens when it ends."


def cameras_full(n: int) -> str:
    """What Start says when the cameras already carry as many sheets as they may."""
    sheets = "1 sheet is" if n == 1 else f"{n} sheets are"
    return (f"Practice on the cameras is full: {sheets} already practising. "
            "Try again when one ends.")


# A practice throw's id, as the worker writes it (practice/throws.py).
PRACTICE_THROW_ID = re.compile(r"^t_[0-9]{1,8}(\.[0-9])?$")
# A practice throw's clip, as the worker names the artifact.
PRACTICE_CLIP = re.compile(r"^clips/(t_[0-9]{1,8}(\.[0-9])?)\.mp4$")
CLIP_MAX_BYTES = 20_000_000
# A claimed game rock's clip, as the clips worker names the artifact
# (service/gameclips.py names the rock).
GAME_CLIP = re.compile(r"^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})\.mp4$")
# How long a signed link to a clip lasts; the page asks again after 50 minutes.
CLIP_URL_S = 3600
# Asking for clips of shots from their pages: a long evening's review of one's
# own games, not a stranger keeping the clips worker busy.
SHOT_CLIP_HOUR_LIMIT = 40
SHOT_CLIP_DAY_LIMIT = 200
# The clips worker asks for work every few seconds (clipworker.POLL_IDLE_S),
# so somebody waiting on a clip is not left waiting on its poll. An instance
# that found the queue empty answers so from memory for this long, unless it
# queued a clip job itself since: Firestore is asked twice a minute, not
# twenty times, and a job queued on the other instance waits at most this.
CLIP_QUIET_S = 30.0
# The page a flag was sent from: an edit, view-only or review link.
FLAG_PATH = re.compile(r"^/(c|s|g)/([A-Za-z0-9_-]+)/?$")
# A YouTube playlist's id: PL..., UU..., OL... and the rest, all URL-safe.
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
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
    # Whether a submission, the playlist poller or a reprocess may make a
    # mixed doubles run. Off until the viewer can chart one: while off, asking
    # for doubles is refused and a doubles title is read as fours, so the
    # service behaves exactly as it did before formats. The worker is not
    # gated -- it builds whatever format the job it claimed says.
    doubles_enabled: bool = False
    # Whether the playlist poller queues a stream that has gone live as a live
    # job. Off until a worker that can follow one is running: a live run no
    # worker claims would stand in for the recording and keep it from ever
    # being queued the ordinary way.
    live_enabled: bool = False
    # Practice sessions: /practice/sheet/N and its API, on the club's cameras.
    # Off until a worker that can see them (WORKER_PRACTICE=1 and
    # WORKER_CAMERAS=1) is running; turning it off is how practice is closed.
    practice_enabled: bool = False
    # Start needs a validated account, or a request from one of these: the
    # club's public address, as client_ip sees it.
    club_ips: frozenset = frozenset()
    # How many sheets may be read from the cameras at once. The Ryzen's link
    # to them carries about two sheets until it runs at gigabit; past that the
    # cameras fall into keyframe storms, which the club's own receivers see
    # too. A Start that joins a sheet's running watch is never counted.
    practice_max_camera_watches: int = 2
    # Clips of the rocks a person says they threw, on My shots: asked for when
    # a play is saved or its report read, cut by the clips worker. Off until a
    # worker running WORKER_ROLE=clips is up.
    game_clips_enabled: bool = False
    # Whether the sign-in chooser offers an emailed link beside Google. Off
    # until Email/Password and its Email link option are on in the Firebase
    # console, or every "Email me a link" fails. /signin and its relay work
    # either way, so a link already sent still finishes (deploy/README.md).
    email_link_signin: bool = False
    # The club's own clock, which playlists' schedules are written in.
    club_tz: str = "America/Los_Angeles"
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
            doubles_enabled=env("DOUBLES_ENABLED", "0") in ("1", "true", "yes"),
            live_enabled=env("LIVE_ENABLED", "0") in ("1", "true", "yes"),
            practice_enabled=env("PRACTICE_ENABLED", "0") in ("1", "true", "yes"),
            club_ips=frozenset(ip.strip() for ip in env("PRACTICE_CLUB_IPS", "").split(",")
                               if ip.strip()),
            practice_max_camera_watches=int(env("PRACTICE_MAX_CAMERA_WATCHES", "2")),
            game_clips_enabled=env("GAME_CLIPS_ENABLED", "0") in ("1", "true", "yes"),
            email_link_signin=env("EMAIL_LINK_SIGNIN", "0") in ("1", "true", "yes"),
            club_tz=env("CLUB_TZ", "America/Los_Angeles"),
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
    app = FastAPI(title="curling.dimmit.net", docs_url=None, redoc_url=None)
    # Cloud Run sends what it is given, and a game's timeline is 350-500 KB of
    # JSON that gzip takes to about a quarter. Level 6 is gzip's own default:
    # 9 saves 2% more on a timeline for over twice the time (41 ms, not 17).
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
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
        # and writes are the quota that runs out. A name the person never gave
        # (they came by emailed link, which carries none) is filled from a
        # later Google sign-in; it rides on the hourly write when that is due,
        # and a name already here is never touched.
        fields = {}
        if got.last_seen_at is None or (t - got.last_seen_at).total_seconds() > 3600:
            fields["last_seen_at"] = t
        if not got.name and claims.get("name"):
            fields["name"] = claims["name"]
        if fields:
            got = repo.update_user(uid, **fields) or got
        return got

    def claims_of(authorization) -> dict | None:
        """The verified claims behind a bearer token, or None. Never raises."""
        if not auth.enabled or not authorization or not authorization.startswith("Bearer "):
            return None
        claims = auth.verify(authorization[len("Bearer "):].strip())
        if not claims or not claims.get("sub"):
            return None
        # Emailed links need Firebase's Email/Password provider switched on, and
        # with it on anybody can make a password account under an address that
        # is not theirs. Firebase removes that unverified sign-in method itself
        # once the real owner finishes an emailed-link sign-in, but until then
        # such an account must never be a user here. A real emailed-link token
        # has email_verified true, so only the squatter is turned away.
        if ((claims.get("firebase") or {}).get("sign_in_provider") == "password"
                and not claims.get("email_verified")):
            return None
        return claims

    def uid_of(authorization) -> str | None:
        """Who is asking, by id alone. Reads no record: a save asks this every
        800 ms while somebody charts, and may_edit_chart needs only the id."""
        claims = claims_of(authorization)
        return claims["sub"] if claims else None

    def current_user_or_none(authorization) -> User | None:
        """Who this request is, if it says so.

        Never raises. A missing, expired, malformed or foreign token all mean
        the same thing -- we do not know who this is -- and that is a state
        every route here already handles, because it is the only state that
        existed before accounts did.
        """
        claims = claims_of(authorization)
        return user_for_claims(claims) if claims else None

    def require_user(authorization) -> User:
        if not auth.enabled:
            raise HTTPException(503, "accounts are not configured")
        got = current_user_or_none(authorization)
        if got is None:
            raise HTTPException(401, "sign in to do that")
        return got

    def require_any_admin(authorization) -> User | None:
        """The owner's admin token (None back: a script, so no account) or a
        signed-in admin (their account back). Anyone else is refused."""
        if bearer_ok(authorization, settings.admin_token):
            return None
        me = require_user(authorization)
        if me.role != "admin":
            raise HTTPException(403, "that is for admins")
        return me

    def owner_key_for(chart: Chart) -> str | None:
        """What a chart dedupes against, or None to never dedupe.

        An anonymous chart has no owner and so keeps today's behaviour exactly:
        every submission gets its own link.
        """
        return chart.team_id or chart.owner_user_id

    def may_use_team(user: User | None, team_id: str | None) -> bool:
        return bool(user and team_id and user.id in repo.team_members(team_id))

    def may_edit_chart(chart: Chart, uid: str | None) -> bool:
        """Whether `uid` may change this chart's grading.

        A team's chart is its current members' -- whoever made it included,
        and only while they stay on the team. A personal chart is its owner's.
        A chart nobody owns (made before charting needed an account) is still
        its link's, until somebody claims it. With accounts off every link
        edits, as before accounts existed.
        """
        if not auth.enabled:
            return True
        if chart.team_id:
            return uid is not None and uid in repo.team_members(chart.team_id)
        if chart.owner_user_id:
            return uid == chart.owner_user_id
        return True

    def chart_access(chart: Chart, uid: str | None, read_only: bool) -> dict:
        """What access.json answers: whether this person may edit the chart,
        and why -- the reason picks the page's view-only line. A team's chart
        carries the team's name; a personal chart never names its owner."""
        team = repo.get_team(chart.team_id) if chart.team_id else None
        named = {"name": team.name} if team else None
        if read_only:
            reason = "view-only-link"
        elif not auth.enabled or not (chart.team_id or chart.owner_user_id):
            reason = "open"
        elif may_edit_chart(chart, uid):
            reason = "team" if chart.team_id else "owner"
        else:
            reason = "signin" if uid is None else "not-yours"
        return {"edit": reason in ("owner", "team", "open"), "reason": reason, "team": named}

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
    def load_doc(key: str, version: str | None = None) -> dict:
        """The parsed timeline at `key`, as it stood at `version`.

        A retry runs a run again in place, and the worker uploads the new
        timeline over the old one at the same key. Cached by key alone, every
        instance that had served the game went on serving the old document --
        to the review link, every chart, and the thinking summary computed
        from it. The run's `ready_at` changes each time it completes, so it
        goes in the key the way the stat does in `_asset_bytes`.
        """
        data = store.get_bytes(key)
        if data is None:
            raise KeyError(key)
        return json.loads(data)

    def run_doc(run: Run) -> dict:
        # A live run's timeline is rewritten at the same key after every end;
        # whichever of the two stamps is newer names the version to serve.
        stamps = [t for t in (run.ready_at, run.revised_at) if t is not None]
        return load_doc(run.timeline_key,
                        max(stamps).isoformat() if stamps else None)

    def requested_format(value) -> str | None:
        """The format a request asked for, or None to let the title decide.

        Anything but a known name is a 400, and so is doubles while the
        service has it switched off (``Settings.doubles_enabled``).
        """
        if value in (None, "", "auto"):
            return None
        # A list or an object is not a name, and is unhashable besides: the
        # dict lookup would raise and answer a malformed body with a 500.
        if not isinstance(value, str) or value not in format_mod.FORMATS:
            raise HTTPException(400, f"format must be one of {sorted(format_mod.FORMATS)}")
        if value != "fours" and not settings.doubles_enabled:
            raise HTTPException(400, "doubles games are not supported yet")
        return value

    def allowed_format(name: str | None) -> str | None:
        """``name``, unless it is a format the service has switched off, which
        is read as fours -- what every run was before formats existed."""
        if name not in (None, "fours") and not settings.doubles_enabled:
            return "fours"
        return name

    def game_doc(run: Run, game_index: int | None, src: Source | None = None,
                 start_s: float | None = None) -> dict:
        """The pristine document, cut down to one game.

        ``start_s`` is the time the person who made this chart said the game
        starts at, and it trims the practice off the front -- per chart, since
        the run's games are shared and only the chart knows what was asked
        for. The source-level review routes below pass nothing and so keep
        seeing the whole of what was detected.
        """
        doc = json.loads(json.dumps(run_doc(run)))  # a private copy
        if game_index is not None:
            doc["games"] = [g for g in doc["games"] if g["index"] == game_index]
        doc = timeline.trim_to_start(doc, start_s)
        # Scores people entered for the ends the board never gave one. After
        # the trim, because the key is the end number the trimmed game shows;
        # before the team names, because rebuilding a game resets those.
        if src is not None:
            doc = timeline.apply_entered_scores(doc, src.entered_scores)
        # A team that played with three, if anybody has said: its rocks are
        # credited 3/3/2. On the source for the same reason as the names
        # below, and written into the format block so a renumbering -- the
        # viewer's, or apply_overrides' -- keeps crediting it that way.
        if (src is not None and src.short_handed
                and format_mod.of_document(doc) is format_mod.FOURS):
            doc["format"] = {**format_mod.FOURS.to_json(), **(doc.get("format") or {}),
                             "short_handed": [c for c in ("red", "yellow")
                                              if c in src.short_handed]}
            timeline.assign_short_handed(doc)
        # The ends a doubles pair threw the other way round, if either of
        # them (or a charter) has said: both partners' rocks change hands
        # there. On the source for the same reason again, and written into
        # each end as roles_swapped so a renumbering keeps it.
        if src is not None:
            doc = timeline.apply_switched_ends(doc, src.switched_ends)
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

    def refresh_thinking(src: Source, run: Run | None = None) -> Source:
        """Keep the game's thinking summary in step with what /g/ serves.

        The thinking report ranks a whole league, and loading a timeline per
        game to do it would be a megabyte a row, so the numbers are read once
        here and kept on the source -- from the same trimmed document the
        review link shows, keyed by the run and play start they came from.
        Whatever moves either of those calls this again.
        """
        run = run or repo.get_run(src.current_run_id)
        if run is not None and run.id != src.current_run_id:
            return src                  # not this game's run
        if run is not None and run.status in ("pending_approval", "queued", "processing"):
            return src                  # being run again in place; /complete will do it
        game = None
        if run is not None and run.status == "ready" and run.timeline_key:
            try:
                doc = game_doc(run, src.game_index, None, src.play_start_s)
                game = doc["games"][0] if doc.get("games") else None
            except KeyError:
                pass
        # Nothing to read -- no run, a failed one, no timeline, no such game in
        # it -- is summarised as nothing measured, so the report leaves the
        # game out rather than calling it pending for ever.
        summary = thinking_report.summarize(game or {}, src.current_run_id, src.play_start_s)
        if summary == src.thinking:
            return src
        return repo.update_source(src.id, thinking=summary) or src

    def keep_thinking_current(src: Source | None, run: Run) -> None:
        """Refresh the summary if the game has moved under it -- a new chart
        can move a game to another run of the same video. Bookkeeping for the
        report, so it never fails the request it rides on."""
        if src is None or thinking_report.current(src) is not None:
            return
        try:
            refresh_thinking(src, run)
        except Exception:  # noqa: BLE001 - see above
            log.exception("could not summarise %s for the thinking report", src.id)

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
        src = repo.update_source(src.id, play_start_s=float(ends[0]["start_s"]),
                                 play_ends=len(ends))
        return refresh_thinking(src, run)

    def learn_play_starts_for_run(run: Run) -> None:
        for src in repo.sources_for_video(run.video_id):
            if src.current_run_id == run.id:
                learn_play_start(src, run)

    def settle_sources_for_run(run: Run) -> None:
        """The rest of what a game's catalogue entry needs once its run is
        ready: its league playlist, from the index as it stands (no YouTube
        call -- the hourly poll catches a stream filed later), and its
        thinking summary. Bookkeeping for the report, so a failure here is
        logged rather than failing the worker's completion."""
        mine = [s for s in repo.sources_for_video(run.video_id) if s.current_run_id == run.id]
        try:
            playlist_index.stamp(repo, mine)
            for s in mine:
                refresh_thinking(repo.get_source(s.id) or s, run)
        except Exception:  # noqa: BLE001 - see above
            log.exception("could not summarise run %s for the thinking report", run.id)

    def index_playlists(max_scans: int = playlist_index.MAX_SCANS,
                        always_stamp: bool = True) -> dict:
        """Refresh the index, then re-stamp every game if it moved. A game
        that finishes is stamped on completion, so the hourly poll need not
        read every source when YouTube said nothing new."""
        channels = playlist_index.channels_to_index(repo, settings.allowed_channels)
        result = playlist_index.refresh(repo, youtube, channels, now(), max_scans=max_scans)
        moved = result["scanned"] or result["retitled"] or result["emptied"]
        result["stamped"] = (playlist_index.stamp(repo, repo.list_sources(limit=EVERY_SOURCE))
                             if always_stamp or moved else 0)
        result["channels"] = len(channels)
        return result

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
            # The chart id IS the edit link, so a view-only document must not
            # carry it: anyone could read it out and open /c/<id>/.
            "slug": None if read_only else chart.id,
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
            # The day it was played, for the report's title, and the game it
            # is, which an entered score is sent to. Neither is secret: the
            # catalogue lists both.
            "played_at": _iso(src.played_at if src else run.published_at),
            "source_id": chart.source_id,
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

    def live_chart(chart: Chart | None) -> Chart | None:
        """`chart`, or the chart that replaced it.

        A link that lost a race to a teammate keeps working: follow the
        pointer rather than redirecting, so the URL somebody wrote down never
        breaks and app.js's relative POSTs land on the right chart without
        knowing any of this happened. Chains are one deep by construction --
        a claim never changes hands -- but bound it anyway.
        """
        for _ in range(4):
            if chart is None or not chart.superseded_by:
                break
            nxt = repo.get_chart(chart.superseded_by)
            if nxt is None:
                break
            chart = nxt
        return chart

    def lookup(kind: str, key: str):
        chart = live_chart(repo.get_chart(key) if kind == "c" else repo.chart_by_share(key))
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
        # A recording that stepped aside for live games, and resumes after them.
        out["paused"] = bool(status == "queued" and job is not None
                             and job.state == "queued" and job.error_kind == "yielded")
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
        keep_thinking_current(repo.get_source(src.id), run)
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
                keep_thinking_current(learn_play_start(src, run), run)
        return chart, False

    # ------------------------------------------------------------- public
    # The catalogue is the front page: most people arrive to find a game that
    # was played, not to paste a link. /games stays, since bookmarks and every
    # chart's header point there.
    @app.get("/", response_class=HTMLResponse)
    @app.get("/games", response_class=HTMLResponse)
    def games_page():
        return page("games.html")

    @app.get("/submit", response_class=HTMLResponse)
    def submit_page():
        return page("submit.html")

    @app.get("/mine", response_class=HTMLResponse)
    def mine_page():
        return page("mine.html")

    @app.get("/thinking", response_class=HTMLResponse)
    def thinking_page():
        return page("thinking.html")

    # The shell for members' labelling; what it shows comes from /api/label.
    @app.get("/label", response_class=HTMLResponse)
    def label_page():
        return page("label.html")

    @app.get("/shots", response_class=HTMLResponse)
    def shots_page():
        return page("shots.html")

    # An admin's list of accounts. The shell is public like every other; the
    # list behind it is not (/api/admin/users).
    @app.get("/users", response_class=HTMLResponse)
    def users_page():
        return page("users.html")

    # The league playlists the poller watches, for an admin to change. Public
    # shell, admin-only list (/api/admin/playlists), as /users.
    @app.get("/playlists", response_class=HTMLResponse)
    def playlists_page():
        return page("playlists.html")

    # One page for all three: every session of mine, a sheet's Start, and one
    # session's throws. The page reads which off the path.
    @app.get("/practice", response_class=HTMLResponse)
    @app.get("/practice/sheet/{n}", response_class=HTMLResponse)
    @app.get("/practice/session/{session_id}", response_class=HTMLResponse)
    def practice_page():
        if not settings.practice_enabled:
            raise HTTPException(404, "no such page")
        return page("practice.html")

    # What the site promises and what it keeps. Plain pages, no script: they
    # have to read the same whatever else is broken.
    @app.get("/terms", response_class=HTMLResponse)
    def terms_page():
        return page("terms.html")

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy_page():
        return page("privacy.html")

    # Nothing here is for search engines: the games carry people's names,
    # and every page is reached from the club's own links or by being sent one.
    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots_txt():
        return "User-agent: *\nDisallow: /\n"

    @app.get("/join/{token}", response_class=HTMLResponse)
    def join_page(token: str):
        # The token is read back off the URL by the page itself; nothing is
        # rendered into the HTML, so there is nothing here to escape.
        return page("join.html")

    # Where an emailed sign-in link lands. Like the invite, everything the
    # link carries is read by the page itself (site/SignIn.jsx), and served
    # whether accounts are on or not: the page says which.
    @app.get("/signin", response_class=HTMLResponse)
    def signin_page():
        return page("signin.html")

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

    def games_listed(sources):
        """The sources that are games of their own: not a page left over from
        a game a newer run joined onto another (Source.merged_into), and not
        practice (Source.is_practice)."""
        return [s for s in sources if s.merged_into is None and not s.is_practice]

    leagues_kept = {"at": None, "names": []}

    def every_league() -> list[str]:
        """Every league a listed game is filed under, for the filter -- kept
        LEAGUES_TTL_S, since it reads the whole catalogue."""
        t = now()
        if (leagues_kept["at"] is None
                or (t - leagues_kept["at"]).total_seconds() >= LEAGUES_TTL_S):
            leagues_kept["names"] = sorted({s.league for s in games_listed(
                repo.list_sources(limit=EVERY_SOURCE)) if s.league})
            leagues_kept["at"] = t
        return leagues_kept["names"]

    def forget_leagues() -> None:
        leagues_kept["at"] = None

    def catalogue_page(before: datetime | None) -> tuple[list, datetime | None]:
        """(games, older): the club's days of play ending with the day of the
        newest game before `before` -- LANDING_DAYS of them for the first page,
        OLDER_DAYS after -- and where the next page starts, or None.

        Whole days in the club's time, so an evening's draws are never split
        across two pages, and anchored on a game rather than on today, so the
        summer is one step back rather than months of empty pages. A stretch
        holding nothing listable (practice, say) is passed over the same way.
        """
        tz = ZoneInfo(settings.club_tz)
        days = OLDER_DAYS if before else LANDING_DAYS
        while True:
            newest = repo.sources_played(before=before, limit=1)
            if not newest:
                return [], None
            day = (newest[0].played_at or newest[0].created_at).astimezone(tz).date()
            day = day - timedelta(days=days - 1)
            since = datetime(day.year, day.month, day.day, tzinfo=tz)
            games = games_listed(repo.sources_played(since=since, before=before))
            older = since if repo.sources_played(before=since, limit=1) else None
            if games or older is None:
                return games, older
            before, days = since, OLDER_DAYS

    @app.get("/api/games")
    def api_games(league: str | None = None, video_id: str | None = None,
                  before: str | None = None):
        """The catalogue: one video's games, one whole league, or else a page
        of the club's days of play (catalogue_page) -- the newest, or the page
        before `before`, which is the `older` the last page handed back."""
        cursor = None
        if before is not None:
            try:
                cursor = datetime.fromisoformat(before)
            except ValueError:
                raise HTTPException(422, "before is a time, as the last page's older gave it")
            if cursor.tzinfo is None:
                cursor = cursor.replace(tzinfo=timezone.utc)
        older = None
        if video_id:
            sources = [s for s in games_listed(repo.sources_for_video(video_id))
                       if league is None or s.league == league]
        elif league is not None:
            sources = games_listed(repo.list_sources(league=league, limit=EVERY_SOURCE))
        else:
            sources, older = catalogue_page(cursor)
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
                # A live game's end count grows with every publish; the
                # count learned at completion would be a snapshot of none.
                "ends": s.play_ends if (s.play_ends is not None and not (
                            run and run.status == "live")) else
                        next((g.get("ends") for g in (run.games if run else [])
                              if g["index"] == s.game_index), None),
                "format": s.format or "fours",
            })
        # Games queued or in flight have no sources yet; list their runs too so
        # a person can make a link now and have it fill in. They are the
        # newest there are, so the first page has them and no other.
        for run in repo.list_runs() if cursor is None else []:
            # A practice watch is not a game, at any stage.
            if run.status in ("ready", "failed", "live") or run.kind == "practice":
                continue
            if (video_id and run.video_id != video_id) or (league and run.league != league):
                continue
            out.append({
                "source_id": None, "video_id": run.video_id, "title": run.title,
                "sheet": run.sheet, "league": run.league,
                "played_at": _iso(run.published_at), "game_index": None,
                "start_s": run.window_start_s, "end_s": run.window_end_s,
                "status": run.status, "ends": None,
                "format": run.format or "fours",
            })
        # The page's own as well as the kept list: a league that arrived since
        # it was read is at least offered where its games are showing.
        leagues = sorted(set(every_league()) | {g["league"] for g in out if g["league"]})
        return {"games": out, "leagues": leagues,
                "older": _iso(older.astimezone(timezone.utc)) if older else None}

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
        forget_leagues()
        log.info("league %r set on %s (%d games) by %s",
                 league, src.video_id, len(games), me.id)
        return {"ok": True, "league": league, "games": len(games)}

    @app.get("/api/reports/thinking")
    def api_thinking_report(request: Request,
                            top: int = Query(thinking_report.TOP, ge=1, le=100),
                            authorization: str | None = Header(default=None)):
        """Per league, the teams with the most and the least thinking per
        end, one row per team per game, read from the summaries on the
        sources alone -- never a timeline.

        With accounts on it is for people signed in: it ranks teams by name,
        slowest first, which a game's own page never does. Any account will
        do. Kept privately for the same reason, so no shared cache hands it
        to somebody signed out.

        Asked about on every load rather than kept for five minutes: a team
        named on a game's page has to be on this one when the person who
        named it comes back to look.

        Only leagues with a game in the last two weeks are listed."""
        if auth.enabled and uid_of(authorization) is None:
            raise HTTPException(401, "sign in to see thinking time")
        report = thinking_report.build(games_listed(repo.list_sources(limit=EVERY_SOURCE)),
                                       top=top, since=now() - thinking_report.RECENT)
        return json_revalidated(request, report, "private")

    @app.post("/api/admin/backfill-thinking")
    def admin_backfill_thinking(limit: int = Query(50, ge=1, le=500),
                                authorization: str | None = Header(default=None)):
        """Give games that predate the thinking report the summary it reads.

        New runs get one on completion; this is for everything before that,
        and for any summary a reprocess or a play-start change left stale.
        Idempotent, and bounded by ``limit`` so it fits in one request: call
        it again until ``remaining`` is 0. A run's games are taken together so
        its timeline is loaded once.
        """
        require_admin(authorization)
        stale = sorted((s for s in games_listed(repo.list_sources(limit=EVERY_SOURCE))
                        if thinking_report.current(s) is None),
                       key=lambda s: (s.current_run_id, s.game_index))
        refreshed = skipped = 0
        for s in stale:
            if refreshed >= limit:
                break
            if thinking_report.current(refresh_thinking(s)) is None:
                skipped += 1        # its run is being processed again; /complete will do it
            else:
                refreshed += 1
        log.info("thinking summaries: %d refreshed, %d skipped", refreshed, skipped)
        return {"ok": True, "refreshed": refreshed, "skipped": skipped,
                "remaining": len(stale) - refreshed - skipped}

    @app.post("/api/admin/switched-ends/migrate")
    def admin_migrate_switched_ends(apply: bool = Query(False),
                                    authorization: str | None = Header(default=None)):
        """Move chart-level doubles swaps -- end-level override keys, which
        the End box wrote before switched ends lived on the game -- onto
        their games (Source.switched_ends), so each end has one answer.

        A dry run unless ``apply``. Every doubles game's charts are read; a
        chart is named by its view-only link, since its id is its edit link.
        - ``moved``: the swaps a colour holds on a chart of the game's current
          run, copied to the game; with ``apply``, taken off the chart.
        - ``normalised``: as moved, for a chart that swapped the first end.
          The first end is what Player A means, so the game is given every
          other end the chart did not swap; ``plays`` names the people who
          saved their rocks from that chart for that colour, whose slot now
          names the other player.
        - ``conflicts``: the game already holds other ends for that colour;
          nothing changes.
        - ``other_runs``: a chart pinned to a run that is not the game's
          current one (or not ready), whose end numbers may not be the
          game's; left in place.
        """
        require_admin(authorization)
        out = {"moved": [], "conflicts": [], "other_runs": [], "normalised": []}
        plays = None
        for src in repo.list_sources(limit=EVERY_SOURCE):
            if (src.format or "fours") != "doubles":
                continue
            charts = [c for r in repo.runs_for_video(src.video_id)
                      for c in repo.charts_for_run(r.id) if c.source_id == src.id]
            for chart in charts:
                # colour -> {end identity: override key}
                keys: dict[str, dict[int, str]] = {}
                for key, patch in (chart.overrides or {}).items():
                    parts = key.split(".")
                    if (len(parts) != 2 or not all(p.isdigit() for p in parts)
                            or int(parts[0]) != chart.game_index or not isinstance(patch, dict)):
                        continue
                    wanted = patch.get("roles_swapped")
                    for c in ("red", "yellow"):
                        if isinstance(wanted, dict) and wanted.get(c) is True:
                            keys.setdefault(c, {})[int(parts[1])] = key
                if not keys:
                    continue

                def item(c, ends):
                    return {"source_id": src.id, "chart": chart.share_slug, "color": c, "ends": ends}
                run = repo.get_run(chart.run_id)
                if (src.merged_into is not None or chart.run_id != src.current_run_id
                        or run is None or run.status != "ready" or chart.game_index is None):
                    out["other_runs"].extend(item(c, sorted(ids)) for c, ids in keys.items())
                    continue
                doc = chart_doc(chart, run, read_only=True)
                served = ([timeline.end_identity(e) for e in doc["games"][0]["ends"]]
                          if doc.get("games") else [])
                if not served:
                    continue
                moved = set()
                for c, by_end in keys.items():
                    swapped = {i for i in by_end if i in served}
                    first = served[0] in swapped
                    target = sorted(i for i in served[1:] if (i in swapped) != first)
                    entry = item(c, target)
                    held = ((src.switched_ends or {}).get(c) or {}).get("ends")
                    if held is not None and sorted(held) != target:
                        out["conflicts"].append(item(c, sorted(swapped)))
                        continue
                    if first:
                        plays = repo.all_plays() if plays is None else plays
                        entry["plays"] = sorted(p.user_id for p in plays
                                                if p.chart_id == chart.id and p.color == c)
                    out["normalised" if first else "moved"].append(entry)
                    moved.add(c)
                    if apply:
                        src = repo.set_switched_ends(src.id, c, {
                            "ends": target, "by": "migration", "at": _iso(now())} if target else None) or src
                if apply and moved:
                    patch, remove = {}, []
                    for key in {k for c in moved for k in keys[c].values()}:
                        old = chart.overrides[key]
                        left = {c: v for c, v in old["roles_swapped"].items() if c not in moved}
                        rest = {f: v for f, v in old.items() if f != "roles_swapped"}
                        kept = {**rest, "roles_swapped": left} if left else rest
                        if kept:
                            patch[key] = kept
                        else:
                            remove.append(key)
                    repo.merge_overrides(chart.id, patch, remove, {}, now())
        log.info("switched ends migration (%s): %s", "applied" if apply else "dry run",
                 {k: len(v) for k, v in out.items()})
        return {"ok": True, "applied": apply, **out}

    @app.post("/api/admin/playlist-index")
    def admin_playlist_index(max_scans: int = Query(playlist_index.MAX_SCANS, ge=0, le=200),
                             authorization: str | None = Header(default=None)):
        """Index the channel's playlists and stamp each game with its own.

        The hourly poll does this too; this is for the first pass, which is
        bigger than one poll reads. Call it again until ``pending`` is 0.
        """
        require_admin(authorization)
        try:
            return {"ok": True, **index_playlists(max_scans)}
        except Exception as e:  # noqa: BLE001 - YouTube's error, passed on
            log.warning("playlist index failed: %s", e)
            raise HTTPException(502, f"YouTube did not answer: {e}")

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
            refresh_thinking(src, run)
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
        src = refresh_thinking(src, run)
        log.info("play starts at %s on %s, %d ends, %d trimmed",
                 src.play_start_s, src.id, src.play_ends, timeline.ends_trimmed(doc))
        return {"ok": True, "play_start_s": src.play_start_s,
                "play_ends": src.play_ends,
                "ends_trimmed": timeline.ends_trimmed(doc)}

    @app.post("/api/games/{source_id}/teams")
    def api_set_teams(source_id: str, body: dict,
                      authorization: str | None = Header(default=None)):
        """Who played this game, by the colour they threw.
        ``short_handed`` says which teams played with three.

        Per game, where the league is per recording: a night on one sheet is
        one league and two different pairs of teams. Nothing detects this --
        the club puts it in some titles and not others, and the cameras read
        stones rather than scoreboards -- so it is typed in or it is unknown.

        It is served into the timeline of every chart of this game, including
        charts made before anyone knew, which is why it lives on the source
        and not in the document the run produced.

        The names are public and any validated account (User.role) may
        change them, so each change is kept with who made it (TeamNameEdit)
        for the admin to read.
        """
        me = require_user(authorization)
        if me.role not in ROLES:
            raise HTTPException(403, "naming teams is open to validated accounts")
        names = {}
        for colour in ("red", "yellow"):
            if colour not in body:
                continue
            # Printable, as a flag's note is: the history of these is read in
            # the owner's terminal.
            name = printable(str(body.get(colour) or ""), keep="").strip()
            if len(name) > 60:
                raise HTTPException(422, "that name is too long for a team")
            names["team_" + colour] = name or None
        # Which teams played with three: a list of colours, empty to clear.
        # Left out, it is left as it was, so naming a team never unmarks it.
        if "short_handed" in body:
            short = body["short_handed"]
            if (not isinstance(short, list)
                    or not all(isinstance(c, str) and c in ("red", "yellow") for c in short)
                    or len(set(short)) != len(short)):
                raise HTTPException(422, "short_handed is a list of colours: red, yellow")
            names["short_handed"] = sorted(short) or None
        if not names:
            raise HTTPException(400, "name red, yellow, or both")
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        if names.get("short_handed") and (src.format or "fours") != "fours":
            raise HTTPException(422, "a doubles team is two players")
        # Read before the update: an in-memory source is changed in place.
        before = {c: getattr(src, "team_" + c) for c in ("red", "yellow")
                  if "team_" + c in names and names["team_" + c] != getattr(src, "team_" + c)}
        repo.update_source(src.id, **names)
        log.info("teams %s set on %s by %s", names, src.id, me.id)
        if before:
            repo.put_team_name_edit(TeamNameEdit(
                id=slug.new_team_name_edit_id(), source_id=src.id, user_id=me.id, at=now(),
                before=before, after={c: names["team_" + c] for c in before}))
        return {"ok": True, **{c: names.get("team_" + c, getattr(src, "team_" + c))
                               for c in ("red", "yellow")},
                "short_handed": names.get("short_handed", src.short_handed) or []}

    @app.post("/api/games/{source_id}/switched-ends")
    def api_set_switched_ends(source_id: str, body: dict,
                              authorization: str | None = Header(default=None)):
        """A chart's End box ticking one end of a doubles team on or off as
        switched: ``{path, color, end, on}``, ``end`` by end identity, ``path``
        the chart's own page. Answers every end the colour now has switched.

        One end at a time, applied to what the game holds, so two ticks in
        quick succession both land, and a mark this chart does not show (saved
        against another reading of the game) stands. Kept on the game
        (Source.switched_ends), as "I played…" keeps them, so the charter
        changes them for every chart and link of the game. Whoever may change
        the chart may say -- signed in, since the change outlives the chart.
        """
        me = require_user(authorization)
        kind, chart, shown, run = play_target(body.get("path"))
        if kind == "g":
            raise HTTPException(422, "switched ends are marked on a chart")
        if kind == "s":
            raise HTTPException(403, "this is a view-only link")
        if chart.source_id != shown.id:
            raise HTTPException(409, MERGED_CHART)
        if shown.id != source_id:
            raise HTTPException(409, "that link is a different game")
        if not may_edit_chart(chart, me.id):
            raise HTTPException(403, "only the person or team who made this chart can change it")
        color, end, on = body.get("color"), body.get("end"), body.get("on")
        if not isinstance(color, str) or color not in ("red", "yellow"):
            raise HTTPException(422, "color must be red or yellow")
        if not isinstance(end, int) or isinstance(end, bool):
            raise HTTPException(422, "end must be one of the game's ends")
        if not isinstance(on, bool):
            raise HTTPException(422, "on must be true or false")
        checked_switched_ends(shown_doc(kind, chart, shown, run), [end])
        held = set(switched_of(shown, color))
        ends = sorted(held | {end} if on else held - {end})
        if not repo.bump_rate_limit("switch:" + me.id, now(), PLAY_HOUR_LIMIT, PLAY_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of changes; try again later")
        src = repo.set_switched_ends(shown.id, color, switched_entry(ends, me)) or shown
        log.info("switched ends of %s set to %s on %s by %s (chart %s)",
                 color, ends, src.id, me.id, chart.id)
        return {"ok": True, "color": color, "ends": switched_of(src, color)}

    @app.get("/api/games/{source_id}/team-names")
    def api_team_names(source_id: str, request: Request):
        """The names this game's league has already given its teams, to
        offer while somebody names this one's, so a team is one name in the
        thinking report rather than a spelling per night. Public, like the
        names on every game's page, and asked about on every open: a name
        typed on the last game has to be offered on the next."""
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        return json_revalidated(request, team_names.suggest(
            games_listed(repo.list_sources(limit=EVERY_SOURCE)), src), "public")

    @app.post("/api/games/{source_id}/scores")
    def api_enter_score(source_id: str, body: dict,
                        authorization: str | None = Header(default=None)):
        """An end's score, typed in by anyone signed in, where the board gave none.

        The wall board is read off the video and misses ends: a card never
        hung, a camera that lost the board, a recording that stops before the
        last card goes up. Somebody who was there knows the score, and the
        report's by-end table shows a "?" until they say.

        It lives on the source, like the team names, so every chart and link
        of the game shows it. Gaps only: an end the board read is refused here
        (with the board's figure, so the page can say what it says), and
        ``timeline.apply_entered_scores`` lets the board win if it reads one
        later. ``end`` is the end number the review link shows -- the game as
        ``game_doc`` serves it from the current run and play start.
        """
        me = require_user(authorization)
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        run = repo.get_run(src.current_run_id)
        if run is None or run.timeline_key is None:
            raise HTTPException(409, "that game has no finished run")
        doc = game_doc(run, src.game_index, src, src.play_start_s)
        if int(doc.get("schema_version") or 0) < 4:
            raise HTTPException(409, "that game was analysed before the wall board was read")
        ends = doc["games"][0]["ends"] if doc.get("games") else []
        number = body.get("end")
        served = (next((e for e in ends if e["number"] == number), None)
                  if isinstance(number, int) and not isinstance(number, bool) else None)
        if served is None:
            raise HTTPException(422, f"end must be one of the game's {len(ends)} ends")
        if served.get("score_source") == "board":
            raise HTTPException(409, {"message": "the wall board has a score for that end",
                                      "score": served["score"]})
        if body.get("clear") is True:
            entry = None
        else:
            most = format_mod.of_document(doc).stones_per_team
            red, yellow = body.get("red"), body.get("yellow")
            whole = all(isinstance(v, int) and not isinstance(v, bool) for v in (red, yellow))
            if not whole or min(red, yellow) < 0 or (red and yellow) or max(red, yellow) > most:
                raise HTTPException(422, f"one team scores 1 to {most}, or neither")
            entry = {"red": red, "yellow": yellow, "by": me.id, "at": _iso(now())}
        src = repo.set_entered_score(src.id, number, entry)
        log.info("score for end %d set to %s on %s by %s", number,
                 None if entry is None else {"red": entry["red"], "yellow": entry["yellow"]},
                 source_id, me.id)
        return {"ok": True,
                "entered_scores": {k: {f: v for f, v in e.items() if f != "by"}
                                   for k, e in (src.entered_scores or {}).items()}}

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
        for s in repo.list_sources(limit=EVERY_SOURCE):
            if s.league:
                continue
            run = repo.get_run(s.current_run_id)
            league = (run.league if run else None) or source.league_from_title(s.title or "")
            if league:
                repo.update_source(s.id, league=league)
                games += 1
        forget_leagues()
        log.info("relabelled %d runs and %d games from their titles", runs, games)
        return {"ok": True, "runs": runs, "games": games}

    @app.post("/api/submissions", status_code=201)
    async def submit(request: Request, authorization: str | None = Header(default=None)):
        me = current_user_or_none(authorization)
        # With accounts on, a chart always has somebody to protect it for
        # (may_edit_chart): no account, no chart. Accounts off keeps charting
        # anonymous, exactly as before accounts existed.
        if auth.enabled and me is None:
            raise HTTPException(401, "sign in to chart")
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
        fmt_name = requested_format(body.get("format"))
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
        fmt_name = fmt_name or allowed_format(source.format_from_title(meta.title))

        run = dedupe.find_reusable_run(repo.runs_for_video(link.video_id),
                                       settings.processing_version, start_s,
                                       game_format=fmt_name)
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
                      league=source.league_from_title(meta.title),
                      format=fmt_name)
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
        # With accounts on, a chart always has somebody to protect it for
        # (may_edit_chart): no account, no chart. Accounts off keeps charting
        # anonymous, exactly as before accounts existed.
        if auth.enabled and me is None:
            raise HTTPException(401, "sign in to chart")
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
    # Accounts exist so a person can find their charts again, so a team can
    # share one, and -- with accounts on -- so a chart belongs to someone: only
    # its owner, or its team's members, may change it (may_edit_chart). A
    # chart URL still shows the chart to whoever holds it.
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
            # Shot corrections only: an end-level key (a doubles role swap)
            # is not a charted shot.
            "shots_charted": sum(1 for k in chart.overrides if k.count(".") == 2),
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

    @app.get("/api/features")
    def features():
        """What the site pages may offer. Doubles stays off until the viewer and
        pipeline are ready for it, and the submit form only shows the choice
        when it is on."""
        return JSONResponse({"doubles": bool(settings.doubles_enabled),
                             "practice": bool(settings.practice_enabled)},
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/api/auth/config")
    def auth_config():
        """What the browser needs to start a sign-in, or that it cannot.

        Served rather than templated into each page: there are four places that
        render HTML and one of them would eventually be forgotten. With no
        project configured this reports disabled and the whole feature simply
        is not there. `emailLink` is whether the chooser offers an emailed
        link beside Google (Settings.email_link_signin).
        """
        if not (auth.enabled and settings.firebase_api_key):
            return JSONResponse({"enabled": False},
                                headers={"Cache-Control": "public, max-age=300"})
        return JSONResponse({"enabled": True,
                             "apiKey": settings.firebase_api_key,
                             "authDomain": settings.firebase_auth_domain,
                             "projectId": settings.firebase_project,
                             "emulator": settings.auth_emulator or None,
                             "emailLink": bool(settings.email_link_signin)},
                            headers={"Cache-Control": "public, max-age=300"})

    @app.post("/api/signin/relay")
    def signin_relay(request: Request, body: dict):
        """Hold an emailed sign-in link that opened in the wrong browser.

        The link opened in a phone mail app's own browser, not the one that
        asked for it. That page leaves the link here and shows the member the
        code; the asking browser trades the code for the link. Anyone can call
        this, so it holds only this site's own sign-in links (`relay_id_of`)
        and, even then, a link does nothing without the inbox it was sent to.
        """
        if not (auth.enabled and settings.firebase_api_key):
            raise HTTPException(503, "sign-in by emailed link is not set up")
        link = body.get("link")
        rid = signin.relay_id_of(link, settings.public_base_url, settings.firebase_api_key)
        if rid is None:
            if not signin.base_url_ok(settings.public_base_url):
                # Then every relay is refused. Say why where an operator will
                # look, not only in each 422.
                log.warning("sign-in relay refused: PUBLIC_BASE_URL %r has no scheme and host,"
                            " so no link can be checked as this site's",
                            settings.public_base_url)
            raise HTTPException(422, "that is not a sign-in link for this site")
        t = now()
        if not repo.bump_rate_limit("relay:" + ip_hash(request), t,
                                    signin.RELAY_HOUR_LIMIT, signin.RELAY_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of codes; try again later")
        held = repo.get_signin_relay(rid)
        if held is not None and held.live(t) and held.link == link:
            relay = held       # opened twice: the same code, not a second one
        else:
            # A newer link for the same request replaces the old. Redraw a few
            # times so it doesn't come back with the same digits -- cosmetic.
            code = signin.new_code()
            for _ in range(5):
                if held is None or code != held.code:
                    break
                code = signin.new_code()
            relay = SigninRelay(id=rid, code=code, link=link, created_at=t,
                                expires_at=t + timedelta(seconds=signin.RELAY_TTL_S))
            repo.put_signin_relay(relay)
        return JSONResponse({"code": relay.code, "expires_at": _iso(relay.expires_at)},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/signin/relay/{relay_id}/claim")
    def signin_claim(relay_id: str, request: Request, body: dict):
        """Trade a relay's code for its link, once.

        Wrong codes count against the relay, not the caller: the fifth deletes
        it, so six digits can't be guessed however many addresses try.
        """
        if not (auth.enabled and settings.firebase_api_key):
            raise HTTPException(503, "sign-in by emailed link is not set up")
        code = body.get("code")
        if not signin.REQUEST_ID.fullmatch(relay_id):
            raise HTTPException(422, "that is not a sign-in request")
        if not (isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code)):
            raise HTTPException(422, "a code is six digits")
        t = now()
        if not repo.bump_rate_limit("claim:" + ip_hash(request), t,
                                    signin.CLAIM_HOUR_LIMIT, signin.CLAIM_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of tries; try again later")
        verdict, link = repo.claim_signin_relay(relay_id, code, t, signin.RELAY_MAX_MISSES)
        if verdict == "wrong":
            raise HTTPException(404, "that code isn't right")
        if verdict != "ok":
            raise HTTPException(410, "that code has run out — send a new link")
        return JSONResponse({"link": link}, headers={"Cache-Control": "no-store"})

    @app.get("/api/me")
    def api_me(authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        teams = repo.teams_for_user(me.id)
        return {"user": {"id": me.id, "email": me.email, "name": me.name,
                         "picture_url": me.picture_url, "role": me.role},
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
        """Put a link you already hold into your list, or move it onto a team.

        Holding the chart id is what lets you claim a chart nobody owns -- for
        one made before charting needed an account, the link was its whole
        permission -- and claiming it makes it yours, so it then locks to you
        (may_edit_chart). What is refused is
        claiming a chart that already belongs to a team you are not on, which
        would otherwise let anyone you shared a link with take it. With
        ``team_id`` the chart moves onto that team: see below for who may.
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
        # Moving a chart onto a team ("I charted this as myself and meant to
        # chart it as my team"). The edit link lets anybody chart a game, so
        # it is not enough to take the chart: only the person who charted it
        # moves it. And a team keeps one chart per game, so the team's claim
        # on the game is taken first -- the same first-writer-wins as
        # resolving a chart -- and a team that already has one is told which,
        # with nothing changed, rather than quietly left holding two.
        if team_id and team_id != chart.team_id:
            if chart.owner_user_id and chart.owner_user_id != me.id:
                raise HTTPException(403, "only the person who charted it can move it")
            if chart.source_id:
                held = repo.claim_chart(team_id, chart.source_id, chart.id)
                if held != chart.id:
                    raise HTTPException(409, {"message": "that team already has a chart of this game",
                                              "chart": held})
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
            # The viewer uses the slug only to key the tab's saved cursor, so a
            # view-only page keys it on the link it was opened by: the chart id
            # is the edit link, and would be readable in the page source.
            # `guarded`: this chart is somebody's or a team's, so the page asks
            # access.json before it unlocks editing (may_edit_chart). Never on
            # a view link, an unowned chart, or with accounts off -- those boot
            # exactly as they always have.
            return HTMLResponse(viewer.boot_page({
                "slug": key if read_only else chart.id,
                "mode": "view" if read_only else "edit",
                "merge": True,
                "shared": chart.team_id is not None,
                "guarded": (not read_only and auth.enabled
                            and bool(chart.owner_user_id or chart.team_id)),
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

        @app.get(prefix + "/access.json")
        def access_json(key: str, authorization: str | None = Header(default=None)):
            """Whether the page should unlock editing for whoever is asking
            (chart_access). A guarded page asks once, before it renders."""
            chart, _run, read_only = lookup(kind, key)
            return JSONResponse(chart_access(chart, uid_of(authorization), read_only),
                                headers={"Cache-Control": "private, no-store"})

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

            Only whoever may_edit_chart allows may save: with accounts on, a
            chart's owner or its team's members, or anyone for a chart nobody
            owns. Refused before the body is read, so a refusal writes nothing.
            """
            chart, _run, read_only = lookup(kind, key)
            if read_only:
                raise HTTPException(403, "this is a view-only link")
            uid = uid_of(request.headers.get("authorization"))
            if not may_edit_chart(chart, uid):
                if uid is None:
                    raise HTTPException(401, "sign in to change this chart")
                raise HTTPException(403, "only the person or team who made this chart can change it")
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
    def canonical_source(sid: str) -> Source | None:
        src = repo.get_source(sid)
        # A page folded into another shows the game it is now part of. A few
        # hops at most: a folded page is never folded into again, but a
        # pointer loop must not hang a request.
        for _ in range(3):
            if src is None or src.merged_into is None:
                break
            src = repo.get_source(src.merged_into)
        return src

    def lookup_source(sid: str) -> tuple[Source, Run]:
        src = canonical_source(sid)
        if src is None:
            raise HTTPException(404, "no such game")
        run = repo.get_run(src.current_run_id)
        # A live game is watchable from its first published end -- and stays
        # so if its live coverage stops (a failed or interrupted live run),
        # showing what was published until another run takes the game over.
        published_live = run is not None and run.kind == "live" and run.revised_at
        if run is None or (run.status not in ("ready", "live") and not published_live):
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
        if run.kind == "live" and run.status not in ("live", "ready"):
            # Coverage stopped: the page stops looking for more and says so.
            doc["live"] = {**(doc.get("live") or {}), "in_progress": False, "stopped": True}
        # No slug and no share_url: this link is already the public one.
        doc["chart"] = {"read_only": True, "review": True,
                        "title": run.title, "league": run.league,
                        "played_at": _iso(src.played_at or run.published_at),
                        "source_id": src.id,
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

    # ----------------------------------------------------------- labelling
    # Members help train the software by marking stones on a still or the
    # moment of release in a clip. A member is only ever handed what
    # labelling.for_member lets through, and an answer is kept only in the
    # form labelling.check_answer returns.
    def label_kind(kind: str) -> str:
        if kind not in LABEL_KINDS:
            raise HTTPException(404, "no such kind of item")
        return kind

    def label_image_url(item: LabelItem) -> str:
        return store.presign_get(item.payload["image"], 3600)

    @app.get("/api/label/me")
    def label_me(authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        return {k: repo.label_count(me.id, k) for k in LABEL_KINDS}

    @app.get("/api/label/{kind}/next")
    def label_next(kind: str, n: int = Query(1, ge=1, le=2),
                   authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        label_kind(kind)
        items = labelling.next_items(repo, kind, me.id, now(), n)
        return {"items": [labelling.for_member(i, label_image_url(i)) for i in items],
                "count": repo.label_count(me.id, kind)}

    @app.post("/api/label/{kind}/{item_id}")
    async def label_submit(kind: str, item_id: str, request: Request,
                           authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        label_kind(kind)
        raw = await request.body()
        if len(raw) > MAX_LABEL_BYTES:
            raise HTTPException(413, "that answer is too long")
        try:
            body = json.loads(raw, parse_constant=_no_constants)
        except ValueError:
            raise HTTPException(400, "send JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "send a JSON object")
        item = repo.get_label_item(item_id)
        if item is None or item.kind != kind:
            raise HTTPException(404, "no such item")
        try:
            checked = labelling.check_answer(item, body.get("answer"))
        except ValueError as err:
            raise HTTPException(422, str(err)) from None
        seconds = body.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise HTTPException(422, "seconds is not a number")
        # Clamped as the number it came in as: float() of a huge integer overflows.
        seconds = float(min(max(seconds, 0), 3600))
        t = now()
        result, _ = repo.submit_label(
            LabelAnswer(id=label_answer_id(item_id, me.id), item_id=item_id, kind=kind,
                        uid=me.id, at=t, seconds=seconds, answer=checked),
            t, labelling.MAX_LABELS)
        if result == "missing":
            raise HTTPException(404, "no such item")
        if result in ("full", "held", "closed"):
            raise HTTPException(409, result)
        return {"result": result, "count": repo.label_count(me.id, kind)}

    @app.post("/api/label/{kind}/{item_id}/skip")
    def label_skip(kind: str, item_id: str, authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        label_kind(kind)
        got = repo.get_label_item(item_id)
        if got is None or got.kind != kind:
            raise HTTPException(404, "no such item")
        repo.skip_label_item(item_id, me.id, now(), labelling.RETIRE_AFTER_SKIPS)
        return {"ok": True}

    # ------------------------------------------------------------- plays
    # Which rocks of a game a person threw -- their team's colour and their
    # slot in the throwing order -- said on the game's page and read back by
    # the "My shots" report. Private to whoever said it, until they let their
    # teammates read the report too (shares_shots_with); only they ever change
    # it. A play said on a chart link remembers that chart, whose grading the
    # report reads; the chart's id is its edit link, so it never leaves the
    # server: these routes answer with play_json and, for watching, the share
    # or review link.
    def play_json(play: Play) -> dict:
        return {"source_id": play.source_id, "color": play.color, "slot": play.slot,
                "format": format_mod.FORMATS.get(play.format, format_mod.FOURS).to_json(),
                "graded": play.chart_id is not None,
                "updated_at": _iso(play.updated_at)}

    def find_play(user_id: str, source_id: str) -> tuple[Play | None, Source | None]:
        """A person's play on a game, and the game it is part of now.

        A play saved before its game was folded into another is still under
        the id it was saved with: asked for by that id, it is found directly;
        asked for by the game it was folded into, only by looking through the
        person's plays for one whose page now points here -- done only once
        both direct reads miss, since every play costs a read.
        """
        src = canonical_source(source_id)
        play = repo.get_play(user_id, src.id) if src else None
        if play is None and (src is None or src.id != source_id):
            play = repo.get_play(user_id, source_id)
        if play is None and src is not None:
            folded = [p for p in repo.plays_for_user(user_id) if p.source_id != src.id
                      and getattr(repo.get_source(p.source_id), "merged_into", None) == src.id]
            play = folded[0] if folded else None
        return play, src

    def play_target(path) -> tuple[str, Chart | None, Source, Run]:
        """The game a page's own path shows, and its chart if it has one."""
        m = FLAG_PATH.match(path) if isinstance(path, str) else None
        if not m:
            raise HTTPException(400, "path must be a chart or game link")
        kind, key = m.groups()
        if kind == "g":
            src, run = lookup_source(key)
            return kind, None, src, run
        chart, run, _ = lookup(kind, key)
        src = canonical_source(chart.source_id) if chart.source_id else None
        if run.status != "ready" or chart.game_index is None or src is None:
            raise HTTPException(409, "that chart's game is not ready yet")
        return kind, chart, src, run

    def checked_switched_ends(doc: dict, value) -> list[int]:
        """The ends ``value`` says a doubles pair switched, checked against
        ``doc`` -- the game as the person saying so sees it -- sorted.

        By the number each end was detected with (timeline.end_identity), as
        Source.switched_ends keeps them. The game's first end is refused: who
        threw its 1st & 5th is what Player A means, so it is never switched.
        """
        if not format_mod.of_document(doc).swappable:
            raise HTTPException(422, "only a doubles game has switched ends")
        if (not isinstance(value, list)
                or not all(isinstance(n, int) and not isinstance(n, bool) for n in value)
                or len(set(value)) != len(value)):
            raise HTTPException(422, "switched ends are a list of distinct end numbers")
        ends = [timeline.end_identity(e) for e in doc["games"][0]["ends"]] if doc.get("games") else []
        if ends and ends[0] in value:
            raise HTTPException(422, "the first end sets who is who: it cannot be switched")
        for n in value:
            if n not in ends:
                raise HTTPException(422, f"the game has no end {n}")
        return sorted(value)

    def shown_doc(kind: str, chart: Chart | None, src: Source, run: Run) -> dict:
        """The game a page's own path shows (play_target's answer), as it
        shows it: the chart's document for a chart, the review link's for a
        game."""
        if chart is not None:
            return chart_doc(chart, run, read_only=True)
        return game_doc(run, src.game_index, src, src.play_start_s)

    # A chart's ends are the game's as it was read when the chart was made.
    # Once that game is part of another (Source.merged_into), its end numbers
    # are not the new game's, so switched ends are not taken from it.
    MERGED_CHART = ("this chart's game is now part of another: "
                    "mark switched ends on that game's page")

    def switched_entry(ends: list[int], me: User) -> dict | None:
        return {"ends": ends, "by": me.id, "at": _iso(now())} if ends else None

    def switched_of(src: Source, color: str) -> list[int]:
        return list(((src.switched_ends or {}).get(color) or {}).get("ends") or [])

    @app.put("/api/me/plays/{source_id}")
    def api_save_play(source_id: str, body: dict,
                      authorization: str | None = Header(default=None)):
        """Say which rocks of this game you threw: ``color`` and ``slot``.

        From the game's page, ``path`` is that page's own path, resolved here
        the way a flag's is, so which chart's grading the report reads is the
        server's word. From the report page there is no path, and only the
        colour and slot of a play already saved change.

        ``switched_ends``, when sent, is the ends the claimed colour's pair
        threw the other way round (Source.switched_ends), checked against the
        page's own game; an empty list clears them. Left out, they are left as
        they were, so a partner saving their own rocks never unmarks them.
        """
        me = require_user(authorization)
        existing, src = find_play(me.id, source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        path = body.get("path")
        kind = chart = run = None
        if path is not None:
            kind, chart, shown, run = play_target(path)
            if shown.id != src.id:
                raise HTTPException(409, "that link is a different game")
            # The run's own record of its format, not its timeline: loading a
            # few hundred KB to learn "fours" would push other people's games
            # out of load_doc's cache.
            fmt = format_mod.FORMATS.get(run.format or "fours", format_mod.FOURS)
            chart_id, link = (chart.id if chart else None), kind
        elif existing is None:
            raise HTTPException(422, "open the game to say which rocks you threw")
        else:
            fmt = format_mod.FORMATS.get(existing.format, format_mod.FOURS)
            chart_id, link = existing.chart_id, existing.link
        color, slot = body.get("color"), body.get("slot")
        if not isinstance(color, str) or color not in ("red", "yellow"):
            raise HTTPException(422, "color must be red or yellow")
        if not isinstance(slot, int) or isinstance(slot, bool) or not 1 <= slot <= len(fmt.positions):
            raise HTTPException(422, f"slot must be 1 to {len(fmt.positions)}")
        if (fmt.short_throw_table and color in (src.short_handed or [])
                and slot not in fmt.short_throw_table):
            raise HTTPException(422, "that team played with three: lead, second or skip")
        switched = None
        if "switched_ends" in body:
            if path is None:
                raise HTTPException(422, "open the game to mark switched ends")
            if chart is not None and chart.source_id != src.id:
                raise HTTPException(409, MERGED_CHART)
            # Loaded only when asked: most saves say nothing about them.
            switched = checked_switched_ends(shown_doc(kind, chart, src, run),
                                             body["switched_ends"])
        if not repo.bump_rate_limit("play:" + me.id, now(), PLAY_HOUR_LIMIT, PLAY_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of changes; try again later")
        t = now()
        play = Play(id=play_id(me.id, src.id), user_id=me.id, source_id=src.id,
                    color=color, slot=slot, created_at=existing.created_at if existing else t,
                    updated_at=t, format=fmt.name, chart_id=chart_id, link=link)
        repo.put_play(play)
        if existing is not None and existing.source_id != src.id:
            repo.delete_play(me.id, existing.source_id)
        if switched is not None:
            src = repo.set_switched_ends(src.id, color, switched_entry(switched, me)) or src
            log.info("switched ends of %s set to %s on %s by %s", color, switched, src.id, me.id)
        if settings.game_clips_enabled:
            # Asked for now, while the game's video is surely still cached;
            # the report asks again for anything this misses.
            try:
                doc, run, _ = play_doc(play, src)
                ask_for_clips(play, doc, run)
            except HTTPException as exc:
                log.info("clips for play %s not asked for: %s", play.id, exc.detail)
            except Exception:  # noqa: BLE001
                log.exception("clips for play %s not asked for", play.id)
        log.info("play %s %d (%s) on %s by %s", color, slot, link, src.id, me.id)
        # A doubles play says which ends its pair switched; fours never do.
        mine = {"switched_ends": switched_of(src, color)} if fmt.swappable else {}
        return {"play": {**play_json(play), **mine}}

    @app.get("/api/me/plays/{source_id}")
    def api_my_play(source_id: str, authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        play, _ = find_play(me.id, source_id)
        return {"play": play_json(play) if play else None}

    @app.delete("/api/me/plays/{source_id}")
    def api_clear_play(source_id: str, authorization: str | None = Header(default=None)):
        me = require_user(authorization)
        play, _ = find_play(me.id, source_id)
        if play is None:
            return {"ok": True, "removed": False}
        if not repo.bump_rate_limit("play:" + me.id, now(), PLAY_HOUR_LIMIT, PLAY_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of changes; try again later")
        return {"ok": True, "removed": repo.delete_play(me.id, play.source_id)}

    def shares_shots_with(viewer: User, owner_id: str) -> bool:
        """Whether ``viewer`` may read ``owner_id``'s report: their own, or a
        teammate's who has let their teams see it. Asked afresh every time, so
        turning sharing off or leaving the team ends it at once."""
        if viewer.id == owner_id:
            return True
        owner = repo.get_user(owner_id)
        if owner is None or not owner.share_shots:
            return False
        return any(owner_id in team.member_ids for team in repo.teams_for_user(viewer.id))

    def require_reader(authorization, owner_id: str) -> None:
        """404, not 403, for anyone else: nobody learns whether there is
        anything to see, or whether such a person exists."""
        if not shares_shots_with(require_user(authorization), owner_id):
            raise HTTPException(404, "no shots shared with you")

    def shots_sharing(me: User) -> dict:
        """Whether this person shares their report, the teams that would see
        it, and the teammates sharing theirs with them, each once."""
        teams = repo.teams_for_user(me.id)
        players, users = {}, {}
        for team in teams:
            for uid in team.member_ids:
                if uid == me.id:
                    continue
                if uid not in users:
                    users[uid] = repo.get_user(uid)
                u = users[uid]
                if u is None or not u.share_shots:
                    continue
                players.setdefault(uid, {"id": uid, "name": u.name or u.email or "A teammate",
                                         "teams": []})["teams"].append(team.name)
        return {"share": bool(me.share_shots), "teams": [t.name for t in teams],
                "players": sorted(players.values(), key=lambda p: p["name"].lower())}

    @app.get("/api/me/shots-sharing")
    def api_shots_sharing(authorization: str | None = Header(default=None)):
        return shots_sharing(require_user(authorization))

    @app.put("/api/me/shots-sharing")
    def api_set_shots_sharing(body: dict, authorization: str | None = Header(default=None)):
        """Let everyone on a team with you read your report, or stop: ``share``."""
        me = require_user(authorization)
        on = body.get("share")
        if not isinstance(on, bool):
            raise HTTPException(422, "share must be true or false")
        if not repo.bump_rate_limit("share:" + me.id, now(), PLAY_HOUR_LIMIT, PLAY_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of changes; try again later")
        me = repo.update_user(me.id, share_shots=on) or me
        log.info("shots sharing %s for %s", "on" if on else "off", me.id)
        return shots_sharing(me)

    @app.put("/api/me/name")
    def api_set_name(body: dict, authorization: str | None = Header(default=None)):
        """Say what teammates see: someone who came by emailed link has no name."""
        me = require_user(authorization)
        name = signin.clean_name(body.get("name"))
        if name is None:
            raise HTTPException(422, f"a name is 1 to {signin.NAME_MAX} characters of plain text")
        if not repo.bump_rate_limit("name:" + me.id, now(), PLAY_HOUR_LIMIT, PLAY_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of changes; try again later")
        me = repo.update_user(me.id, name=name) or me
        log.info("name set for %s", me.id)
        return {"name": me.name}

    def plays_list(user_id: str) -> dict:
        """Every game this person said they played, newest first, with what the
        report lists it by and the link to watch it on -- the chart's share
        link when the play came from a chart, else the game's review link."""
        out, seen = [], set()
        for play in repo.plays_for_user(user_id):   # newest first
            src = canonical_source(play.source_id)
            # Two plays on one game: one saved before its page was folded
            # into another. The newer is the one the person meant.
            if src is not None and src.id in seen:
                continue
            if src is not None:
                seen.add(src.id)
            chart = live_chart(repo.get_chart(play.chart_id)) if play.chart_id else None
            run_id = chart.run_id if chart else (src.current_run_id if src else None)
            run = repo.get_run(run_id) if run_id else None
            item = play_json(play)
            item.update({
                "title": (src.title if src else None) or (run.title if run else None),
                "league": (src.league if src else None) or (run.league if run else None),
                "sheet": (src.sheet if src else None) or (run.sheet if run else None),
                "played_at": _iso((src.played_at if src else None)
                                  or (run.published_at if run else None)),
                "teams": {"red": src.team_red if src else None,
                          "yellow": src.team_yellow if src else None},
                "short_handed": (src.short_handed if src else None) or [],
                "view_path": (f"/s/{chart.share_slug}/" if chart and chart.share_slug
                              else f"/g/{src.id}/" if src else None),
                "status": "ok" if src and run else "gone",
            })
            out.append(item)
        out.sort(key=lambda p: p["played_at"] or "", reverse=True)
        return {"plays": out}

    @app.get("/api/me/plays")
    def api_my_plays(authorization: str | None = Header(default=None)):
        return plays_list(require_user(authorization).id)

    @app.get("/api/players/{user_id}/plays")
    def api_players_plays(user_id: str, authorization: str | None = Header(default=None)):
        """A teammate's list, read-only, once they share it."""
        require_reader(authorization, user_id)
        return plays_list(user_id)

    def play_doc(play: Play, src: Source) -> tuple[dict, Run, str | None]:
        """The game as this play's report reads it -- graded by the chart the
        play was saved from, if it was -- not yet cut down to the player:
        (doc, the run it is from, why it is ungraded when it should be)."""
        doc, fallback = None, None
        if play.chart_id:
            chart = live_chart(repo.get_chart(play.chart_id))
            run = repo.get_run(chart.run_id) if chart else None
            if chart and run and chart.game_index is not None:
                if run.status != "ready":
                    # Being analysed again in place. Not gone: its grading
                    # comes back with it, and its share link still opens it.
                    raise HTTPException(409, "this game is being analysed again; try later")
                # read_only: the chart block must not carry the edit link.
                doc = timeline.apply_overrides(chart_doc(chart, run, read_only=True),
                                               chart.overrides)
            else:
                fallback = ("the chart this was saved from is gone, so this is the "
                            "game as detected, without its grading")
        if doc is None:
            src, run = lookup_source(src.id)
            doc = game_doc(run, src.game_index, src, src.play_start_s)
        return doc, run, fallback

    def report_run(play: Play, src: Source) -> Run | None:
        """The run play_doc reads, without loading its document; None where
        play_doc would refuse."""
        if play.chart_id:
            chart = live_chart(repo.get_chart(play.chart_id))
            run = repo.get_run(chart.run_id) if chart else None
            if chart and run and chart.game_index is not None:
                return run if run.status == "ready" else None
        try:
            return lookup_source(src.id)[1]
        except HTTPException:
            return None

    # When this instance last found no clip job to hand out, until when it
    # may say so again without asking (CLIP_QUIET_S).
    clip_quiet = {"until": None}

    def clip_queue_stirred():
        clip_quiet["until"] = None

    def ask_for_clips(play: Play, doc: dict, run: Run, again: bool = False) -> dict | None:
        """Clips of the rocks this play names in ``doc`` (service/gameclips.py),
        asked for if they have not been, and where they stand; None if that
        went wrong -- the report and the play stand without them. Marks the
        player's rocks in ``doc`` with their names. ``again``: those cut in
        an older style are cut again."""
        try:
            rocks = gameclips.player_rocks(doc, play.color, play.slot)
            clip_queue_stirred()
            return gameclips.ensure(repo, play, run, rocks, doc.get("calibration"), now(),
                                    again=again)
        except Exception:  # noqa: BLE001
            log.exception("clips for play %s were not asked for", play.id)
            return None

    def clips_on():
        if not settings.game_clips_enabled:
            raise HTTPException(404, "clips are not switched on")

    def report_doc(user_id: str, source_id: str, request: Request):
        """The game as the report reads it: graded by the chart the play was
        saved from, if it was, then cut down to this player's rocks and the
        little the browser needs of everyone else's (timeline.for_player)."""
        play, src = find_play(user_id, source_id)
        if play is None or src is None:
            raise HTTPException(404, "you have not said which rocks you threw in that game")
        doc, run, fallback = play_doc(play, src)
        clips = ask_for_clips(play, doc, run) if settings.game_clips_enabled else None
        doc = timeline.for_player(doc, play.color, play.slot)
        out = {"doc": doc, "fallback": fallback}
        if clips is not None:
            out["clips"] = clips
        return json_revalidated(request, out, "private")

    @app.get("/api/me/plays/{source_id}/doc")
    def api_my_play_doc(source_id: str, request: Request,
                        authorization: str | None = Header(default=None)):
        return report_doc(require_user(authorization).id, source_id, request)

    @app.get("/api/players/{user_id}/plays/{source_id}/doc")
    def api_players_play_doc(user_id: str, source_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_reader(authorization, user_id)
        return report_doc(user_id, source_id, request)

    def report_clips(user_id: str, source_id: str) -> dict:
        """Where a play's clips stand, without the document: the page asks
        this while any are on their way."""
        clips_on()
        play, src = find_play(user_id, source_id)
        run = report_run(play, src) if play and src else None
        if run is None:
            raise HTTPException(404, "you have not said which rocks you threw in that game")
        return {"clips": gameclips.state_for(repo, play.id, run)}

    @app.get("/api/me/plays/{source_id}/clips")
    def api_my_play_clips(source_id: str, authorization: str | None = Header(default=None)):
        return report_clips(require_user(authorization).id, source_id)

    @app.get("/api/players/{user_id}/plays/{source_id}/clips")
    def api_players_play_clips(user_id: str, source_id: str,
                               authorization: str | None = Header(default=None)):
        require_reader(authorization, user_id)
        return report_clips(user_id, source_id)

    def report_clip(user_id: str, source_id: str, rock: str) -> dict:
        """A short-lived link to one of a play's clips."""
        clips_on()
        play, src = find_play(user_id, source_id)
        run = report_run(play, src) if play and src else None
        if run is None or not gameclips.GAME_ROCK.match(rock):
            raise HTTPException(404, "no clip for that rock")
        got = gameclips.state_for(repo, play.id, run)["rocks"].get(rock) or {}
        if got.get("clip") is not True:
            raise HTTPException(404, "no clip for that rock")
        return clip_links(run, rock, src.id)

    def clip_links(run: Run, rock: str, source_id: str | None) -> dict:
        """Short-lived links to a rock's clip: one to play, one that saves it."""
        key = clip_key(run.video_id, run.id, rock)
        name = "curling-{}-e{}-r{}.mp4".format(source_id or run.video_id,
                                              *rock.split(".")[1:])
        return {"url": store.presign_get(key, CLIP_URL_S),
                "download": store.presign_get(key, CLIP_URL_S, download_as=name)}

    # --------------------------------------------------------- shot clips
    # A clip of any one shot, from its game's page (service/gameclips.py,
    # ask_shot): anyone may see where one stands and play or save it, and
    # anyone signed in may ask for one while the game's video is kept.
    def shot_clip_target(path: str, rock: str) -> tuple[str | None, Run]:
        """The game a page shows, as (its source id, the run it is read
        from), for a clip of ``rock`` on it."""
        m = FLAG_PATH.match(str(path or ""))
        if not m or not gameclips.GAME_ROCK.match(str(rock or "")):
            raise HTTPException(404, "no such shot")
        kind, key = m.groups()
        if kind == "g":
            src, run = lookup_source(key)
            return src.id, run
        chart, run, _ = lookup(kind, key)
        return chart.source_id, run

    def shot_clip_answer(run: Run, rock: str, source_id: str | None, state: dict) -> dict:
        if state["state"] == "ready":
            return {**state, **clip_links(run, rock, source_id)}
        if state["state"] == "none" and gameclips.shot_expired(run, now()):
            return {"state": "expired"}
        return state

    @app.get("/api/shot-clips")
    def api_shot_clip(path: str = Query(...), rock: str = Query(...)):
        """Where a clip of one shot stands; the page asks every few seconds
        while it is on its way. A game still being streamed has clips of the
        rocks in its published ends, cut from the recording being made."""
        clips_on()
        sid, run = shot_clip_target(path, rock)
        if run.status not in ("ready", "live"):
            return {"state": "busy"}
        return shot_clip_answer(run, rock, sid, gameclips.shot_state(repo, run, rock, now()))

    @app.post("/api/shot-clips")
    async def api_ask_shot_clip(request: Request, authorization: str | None = Header(default=None)):
        """Ask for a clip of one shot: ``{path, rock}``, the page and the
        rock's name (`gameclips.rock_name`)."""
        clips_on()
        user = require_user(authorization)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "send JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "send a JSON object")
        rock = str(body.get("rock") or "")
        sid, run = shot_clip_target(body.get("path"), rock)
        if run.status not in ("ready", "live"):
            raise HTTPException(409, "this game is being analysed again; try later")
        t = now()
        state = gameclips.shot_state(repo, run, rock, t)
        if state["state"] in ("none", "failed"):
            if gameclips.shot_expired(run, t):
                raise HTTPException(410, "this game's video is no longer kept")
            doc = run_doc(run)
            spec = gameclips.find_rock(doc, rock)
            if spec is None:
                raise HTTPException(404, "no such shot")
            if not repo.bump_rate_limit("shotclip:" + user.id, t, SHOT_CLIP_HOUR_LIMIT,
                                        SHOT_CLIP_DAY_LIMIT):
                raise HTTPException(429, "that is a lot of clips; try again later")
            clip_queue_stirred()
            state = gameclips.ask_shot(repo, run, sid or "", user.id, spec,
                                       doc.get("calibration"), t)
        return shot_clip_answer(run, rock, sid, state)

    @app.get("/api/me/plays/{source_id}/clips/{rock}")
    def api_my_play_clip(source_id: str, rock: str,
                         authorization: str | None = Header(default=None)):
        return report_clip(require_user(authorization).id, source_id, rock)

    @app.get("/api/players/{user_id}/plays/{source_id}/clips/{rock}")
    def api_players_play_clip(user_id: str, source_id: str, rock: str,
                              authorization: str | None = Header(default=None)):
        require_reader(authorization, user_id)
        return report_clip(user_id, source_id, rock)

    # ------------------------------------------------------------- practice
    def practice_on():
        if not settings.practice_enabled:
            raise HTTPException(404, "practice is not switched on")

    def practice_doc(watch):
        """A watch's latest practice.json, or None before its first publish."""
        if watch is None or watch.revised_at is None:
            return None
        run = repo.get_run(watch.run_id)
        try:
            return load_doc(practice_key(run.video_id, run.id), _iso(watch.revised_at))
        except KeyError:
            return None

    def my_session(session_id, me):
        s = repo.get_session(session_id)
        if s is None or s.user_id != me.id:
            raise HTTPException(404, "no such session")
        return s

    @app.post("/api/practice/sessions", status_code=201)
    async def practice_start(request: Request,
                             authorization: str | None = Header(default=None)):
        practice_on()
        me = require_user(authorization)
        # The club's cameras are not for just anyone who signed in. The address
        # is client_ip's, which a forged X-Forwarded-For cannot move. Joining a
        # sheet's watch is a Start like any other.
        if me.role not in ROLES and client_ip(request) not in settings.club_ips:
            raise HTTPException(403, CAMERAS_NOT_ALLOWED)
        body = await request.json()
        try:
            sheet = int(body.get("sheet"))
        except (TypeError, ValueError):
            raise HTTPException(422, "say which sheet") from None
        # Asked before the rate limit, so a refusal costs no one a session's worth.
        if practice.league_on(repo, now()):
            raise HTTPException(409, LEAGUE_ON)
        if sheet in practice.CAMERA_SHEETS and practice.alive_watch(repo, sheet) is None:
            # A new watch takes a sheet's four cameras over the Ryzen's link.
            watching = practice.camera_watches(repo)
            if watching >= settings.practice_max_camera_watches:
                raise HTTPException(409, cameras_full(watching))
        if not repo.bump_rate_limit("practice:" + me.id, now(), PRACTICE_HOUR_LIMIT,
                                    PRACTICE_DAY_LIMIT):
            raise HTTPException(429, "that is a lot of sessions; try again later")
        try:
            s = practice.start(repo, me, sheet, body.get("hand", "right"), now(),
                               settings.processing_version)
        except practice.NoCameras:
            raise HTTPException(404, f"Sheet {sheet} has no cameras.") from None
        except practice.LeagueOn:    # one began between the check and the start
            raise HTTPException(409, LEAGUE_ON) from None
        return {"id": s.id, "watch_id": s.watch_id}

    @app.get("/api/practice/sessions/{session_id}")
    def practice_read(session_id: str, request: Request,
                      authorization: str | None = Header(default=None)):
        practice_on()
        s = practice.settle(repo, my_session(session_id, require_user(authorization)), now())
        view = practice.session_view(repo, practice_doc(repo.get_watch(s.watch_id)), s, now())
        return json_revalidated(request, view, "private")

    @app.post("/api/practice/sessions/{session_id}/stop")
    def practice_stop(session_id: str, authorization: str | None = Header(default=None)):
        practice_on()
        s = my_session(session_id, require_user(authorization))
        practice.stop(repo, s, now())
        return {"ok": True}

    @app.patch("/api/practice/sessions/{session_id}/throws/{throw_id}")
    async def practice_hide(session_id: str, throw_id: str, request: Request,
                            authorization: str | None = Header(default=None)):
        practice_on()
        s = my_session(session_id, require_user(authorization))
        if not PRACTICE_THROW_ID.match(throw_id):
            raise HTTPException(422, "not a throw")
        body = await request.json()
        practice.hide(repo, s, throw_id, bool(body.get("hidden")))
        return {"ok": True}

    @app.get("/api/practice/sessions/{session_id}/throws/{throw_id}/clip")
    def practice_clip(session_id: str, throw_id: str,
                      authorization: str | None = Header(default=None)):
        """A short-lived link to one throw's clip, for the session's owner, when
        the throw is in their window and its clip is up."""
        practice_on()
        s = my_session(session_id, require_user(authorization))
        if not PRACTICE_THROW_ID.match(throw_id):
            raise HTTPException(404, "no such throw")
        watch = repo.get_watch(s.watch_id)
        doc = practice_doc(watch)
        if not any(th["id"] == throw_id and th.get("clip")
                   for th, _ in practice.in_window(doc, s, now())):
            raise HTTPException(404, "no clip for that throw")
        run = repo.get_run(watch.run_id)
        return {"url": store.presign_get(clip_key(run.video_id, run.id, throw_id), CLIP_URL_S)}

    @app.get("/api/me/practice")
    def my_practice(authorization: str | None = Header(default=None)):
        """Every session of mine, on any sheet, newest first, with its count of
        throws. Sessions on one sheet in a morning share a watch, so each
        watch's document is read once."""
        practice_on()
        me = require_user(authorization)
        t = now()
        docs = {}
        out = []
        for s in repo.sessions_for_user(me.id):
            practice.settle(repo, s, t)
            if s.watch_id not in docs:
                docs[s.watch_id] = practice_doc(repo.get_watch(s.watch_id))
            out.append({"id": s.id, "sheet": s.sheet, "started_at": _iso(s.started_at),
                        "ended_at": _iso(s.ended_at), "end_reason": s.end_reason,
                        "active": s.active,
                        "throws": practice.throw_count(docs[s.watch_id], s, t)})
        return {"sessions": out}

    def practice_watch_of(job):
        return repo.get_run(job.run_id), repo.watch_for_job(job.id)

    def practice_should_stop(job) -> bool:
        """A practice job's sessions' clocks, run: True when no session is
        watching any more and the worker should stop."""
        _, watch = practice_watch_of(job)
        if watch is None:
            return True
        return practice.sweep(repo, watch, practice_doc(watch), now()) == 0

    def practice_publish(job, body):
        """A practice watch's latest document is up: say so on the watch (its
        revised_at is what readers' caches key on), keep the lease, and say
        whether anyone is still watching."""
        run, watch = practice_watch_of(job)
        if not store.exists(practice_key(run.video_id, run.id)):
            raise HTTPException(409, "practice.json has not been uploaded")
        t = now()
        got = body.get("practice") or {}
        repo.update_run(run.id, status="live", revised_at=t, error=None)
        repo.update_job(job.id, progress_at=t, phase="live",
                        lease_expires_at=t + timedelta(seconds=settings.lease_s))
        if watch is None:
            return {"ok": True, "stop": True}
        repo.update_watch(watch.id, status=got.get("status", watch.status), revised_at=t,
                          recorded_s=got.get("recorded_s"), throws=int(got.get("throws") or 0))
        return {"ok": True, "stop": practice_should_stop(job)}

    def practice_complete(job, body):
        """A practice watch has ended -- stopped, out of stream, or given up
        calibrating: its job is done and any session still on it ends."""
        run, watch = practice_watch_of(job)
        t = now()
        got = body.get("practice") or {}
        repo.update_run(run.id, status="ready", ready_at=t, error=None)
        repo.update_job(job.id, state="done", finished_at=t, phase="done", fraction=1.0)
        if watch is not None:
            failed = got.get("status") == "failed"
            repo.update_watch(watch.id, status="failed" if failed else "ended",
                              recorded_s=got.get("recorded_s", watch.recorded_s),
                              throws=int(got.get("throws") or watch.throws))
            practice.end_all(repo, watch, t, "failed" if failed else "stream_ended")
        return {"ok": True}

    def practice_resume(job):
        """What a camera watch handed out again needs to carry on: the worker
        that had it is gone, and its recording with it (nothing from the
        cameras is kept), so the next one records from its claim -- on the
        session's clock, after the throws already published. None for a first
        claim, or before anything was published."""
        if job.attempts <= 1:
            return None
        try:
            doc = practice_doc(repo.watch_for_job(job.id))
        except ValueError as e:                    # not JSON: start afresh
            log.warning("practice job %s: its practice.json cannot be read: %s", job.id, e)
            return None
        if doc is None or doc.get("wall_t0") is None:
            return None
        # The session's clock began t0_s before this document's recording did:
        # 0 unless that watch was itself resumed.
        return {"wall_t0": doc["wall_t0"] - (doc.get("t0_s") or 0.0),
                "throws": doc.get("throws") or []}

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
        # What this worker can do. One from before live games says nothing and
        # gets recordings only: handed a live stream it would try to download
        # it whole.
        kinds = tuple(k for k in (body.get("kinds") or ("vod",)) if k in JOB_KINDS)
        if not body.get("cameras"):
            # Practice reads the club's cameras, which only a worker that can
            # see them (WORKER_CAMERAS=1) may claim: the stand-in must not.
            kinds = tuple(k for k in kinds if k != "practice")
        while True:
            job = repo.claim_job(str(body.get("worker_id", "worker")), t, settings.lease_s,
                                 kinds=kinds)
            if job is None:
                return Response(status_code=204)
            if job.kind != "practice" or not practice_should_stop(job):
                break
            # Every session on it ended while it waited (Stop, idle, a league,
            # no worker free): handed out, it would read the sheet with
            # nobody's session on until its first heartbeat said stop. Done as
            # a stopped watch completes, and on to the next.
            log.info("practice job %s: no session left on it; finished unclaimed", job.id)
            practice_complete(job, {})
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
        # A practice watch is recorded from the sheet's cameras, from its claim
        # on; one handed out again resumes its session (practice_resume). A
        # job left from when practice read YouTube says no source, and the
        # worker refuses it.
        practice_fields = {}
        if job.kind == "practice" and run.source == practice.CAMERAS:
            practice_fields = {"source": practice.CAMERAS}
            resume = practice_resume(job)
            if resume is not None:
                practice_fields["resume"] = resume
        return {"job": {
            "id": job.id, "run_id": run.id, "video_id": run.video_id,
            "window_start_s": run.window_start_s, "window_end_s": run.window_end_s,
            "sheet": run.sheet, "processing_version": run.processing_version,
            "format": run.format or "fours", "kind": job.kind or "vod",
            # What a worker with the video on disk needs instead of asking
            # YouTube (`worker.cached_info`).
            "title": run.title, "duration_s": run.duration_s, "channel_id": run.channel_id,
            "attempt": job.attempts, "lease_s": settings.lease_s,
            **practice_fields,
        }}

    @app.post("/api/worker/release")
    async def release(request: Request, authorization: str | None = Header(default=None)):
        """A worker starting up hands back what its id held: the process that
        held it is gone, and its work with it. Each job goes back as an
        expired lease would (repo.requeue_expired), only without waiting out
        the lease -- so a camera watch resumes (practice_resume) in seconds,
        not ten minutes, and fewer of its throws are lost. The clips
        worker's clip jobs too."""
        require_worker(authorization)
        body = await request.json()
        worker_id = body.get("worker_id")
        if not worker_id:
            raise HTTPException(422, "say which worker")
        t = now()
        jobs = repo.release_jobs(str(worker_id), t)
        clip_jobs = gameclips.release(repo, str(worker_id), t)
        if clip_jobs:
            clip_queue_stirred()
        if jobs or clip_jobs:
            log.info("worker %s started again: %d job(s) and %d clip job(s) handed back",
                     worker_id, jobs, clip_jobs)
        return {"ok": True, "jobs": jobs, "clip_jobs": clip_jobs}

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
        if job.kind == "practice" and practice_should_stop(job):
            # Nobody is watching this sheet any more (stopped, idle, or out of
            # time): the worker reads what it has, publishes, and completes.
            return {"ok": True, "lease_expires_at": _iso(lease), "stop": True}
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
            if job.kind == "practice":
                clip = PRACTICE_CLIP.match(name)
                if clip is not None:
                    if int(f.get("bytes") or 0) > CLIP_MAX_BYTES:
                        raise HTTPException(400, f"{name} is over {CLIP_MAX_BYTES} bytes")
                    key = clip_key(run.video_id, run.id, clip.group(1))
                    uploads.append({"name": name, "key": key, **signed(key, "video/mp4")})
                    continue
                if name != "practice.json":
                    raise HTTPException(400, "a practice job uploads practice.json or "
                                             f"clips/<throw>.mp4, not {name}")
                key = practice_key(run.video_id, run.id)
            elif name not in ("timeline.json", "meta.json"):
                raise HTTPException(400, f"unknown artifact {name}")
            else:
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
        allowed = (timeline_key(run.video_id, run.id), meta_key(run.video_id, run.id),
                   practice_key(run.video_id, run.id))
        clips = f"runs/{run.video_id}/{run.id}/"
        is_clip = (job.kind == "practice" and key.startswith(clips)
                   and PRACTICE_CLIP.match(key[len(clips):]) is not None)
        if key not in allowed and not is_clip and not key.startswith(f"detcache/{run.video_id}/"):
            raise HTTPException(400, "that key does not belong to this job")
        data = await request.body()
        if is_clip and len(data) > CLIP_MAX_BYTES:
            raise HTTPException(413, "that clip is too big")
        store.put_bytes(key, data, request.headers.get("content-type", "application/octet-stream"))
        return {"ok": True, "bytes": len(data)}

    @app.post("/api/worker/jobs/{job_id}/yield")
    async def yield_job(job_id: str, request: Request,
                        authorization: str | None = Header(default=None)):
        """A recording stepping aside for a live stream. Back in the queue at
        once, and not a failure: the attempt it was on is given back."""
        require_worker(authorization)
        body = await request.json()
        job = owned_job(job_id, body.get("worker_id"))
        repo.update_job(job.id, state="queued", worker_id=None, lease_expires_at=None,
                        run_after=now(), attempts=max(0, job.attempts - 1),
                        error_kind="yielded", message="paused for live games")
        repo.update_run(job.run_id, status="queued")
        return {"ok": True}

    @app.post("/api/worker/jobs/{job_id}/publish")
    async def publish(job_id: str, request: Request,
                      authorization: str | None = Header(default=None)):
        """A live run's timeline as it stands: served from now on, its games
        given sources so they can be watched, the job's lease renewed."""
        require_worker(authorization)
        body = await request.json()
        job = owned_job(job_id, body.get("worker_id"))
        if job.kind == "practice":
            return practice_publish(job, body)
        if (job.kind or "vod") != "live":
            raise HTTPException(400, "only a live run publishes before it completes")
        run = repo.get_run(job.run_id)
        tkey = timeline_key(run.video_id, run.id)
        if not store.exists(tkey):
            raise HTTPException(409, "timeline.json has not been uploaded")
        t = now()
        games = body.get("games") or []
        title = body.get("title", run.title)
        repo.update_run(run.id, status="live", revised_at=t, games=games, title=title,
                        league=run.league or source.league_from_title(title),
                        sheet=body.get("sheet", run.sheet),
                        duration_s=body.get("duration_s", run.duration_s),
                        timeline_key=tkey, error=None)
        repo.update_job(job.id, progress_at=t, phase="live",
                        lease_expires_at=t + timedelta(seconds=settings.lease_s))
        run = repo.get_run(run.id)
        sources = []
        for game in games:
            src = dedupe.find_or_create_source(repo, run, game, t)
            if src.game_end_s != float(game["end_s"]):
                repo.update_source(src.id, game_end_s=float(game["end_s"]))
            sources.append(src.id)
        return {"ok": True, "sources": sources}

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
        if job.kind == "practice":
            return practice_complete(job, body)
        # A worker that predates formats sends none and builds fours whatever
        # it was asked; storing that as a doubles reading would be wrong in
        # every end. Old fours runs are unaffected.
        want = run.format or "fours"
        got = body.get("format") or "fours"
        if got != want:
            error = (f"this run's format is {want}, but the worker built {got} "
                     f"-- update the worker")
            # Failed here, as a permanent failure would be, and not just
            # refused: the worker reads the 409 as "no longer yours" and walks
            # away, and a job left running would come back when its lease ran
            # out, to be claimed by the same old worker for ever.
            t = now()
            repo.update_job(job.id, state="failed", error=error, error_kind="permanent",
                            finished_at=t)
            repo.update_run(run.id, status="failed", error=error)
            raise HTTPException(409, error)
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
        settle_sources_for_run(run)
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
        blocked_out = kind == "blocked" and job.blocked_retries >= len(BLOCKED_LADDER_S)
        if (kind == "permanent" or blocked_out
                or (kind != "blocked" and job.attempts >= job.max_attempts)):
            repo.update_job(job.id, state="failed", error=error, error_kind=kind, finished_at=t)
            repo.update_run(job.run_id, status="failed", error=error)
            if job.kind == "practice":
                # Nothing will send this watch's heartbeats again: end its
                # sessions now, so the sheet's page can start afresh.
                _, watch = practice_watch_of(job)
                if watch is not None:
                    repo.update_watch(watch.id, status="failed", error=error)
                    practice.end_all(repo, watch, t, "failed")
            return {"ok": True, "final": True}
        extra = {}
        if kind == "blocked":
            delay = BLOCKED_LADDER_S[job.blocked_retries]
            # The claim counted an attempt; a block gives it back.
            extra = {"attempts": max(0, job.attempts - 1),
                     "blocked_retries": job.blocked_retries + 1}
        else:
            # `or` would turn an explicit 0 -- "retry now" -- into 60.
            asked = body.get("retry_after_s")
            delay = float(60 if asked is None else asked)
        repo.update_job(job.id, state="queued", worker_id=None, lease_expires_at=None,
                        run_after=t + timedelta(seconds=delay), error=error, error_kind=kind,
                        **extra)
        repo.update_run(job.run_id, status="queued", error=error)
        return {"ok": True, "final": False, "retry_after_s": delay}

    @app.get("/api/worker/detcache/{video_id}")
    def detcache_list(video_id: str, authorization: str | None = Header(default=None)):
        require_worker(authorization)
        keys = store.list_keys(f"detcache/{video_id}/")
        return [{"digest": Path(k).stem, "url": store.presign_get(k)} for k in keys]

    # ------------------------------------------------------------ clip jobs
    # The clips worker's own queue (service/gameclips.py): clips of the rocks
    # people say they threw. Nothing here writes a run -- a clip job only
    # reads the run it is cut from.
    def owned_clip_job(job_id: str, worker_id: str | None) -> ClipJob:
        job = repo.get_clip_job(job_id)
        if job is None:
            raise HTTPException(404, "no such clip job")
        if job.state != "running" or (worker_id and job.worker_id != worker_id):
            raise HTTPException(409, "this clip job is not yours any more")
        return job

    def clip_names(job: ClipJob) -> set:
        return {r["name"] for r in job.spec.get("rocks", [])}

    @app.post("/api/worker/clips/claim")
    async def clips_claim(request: Request, authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        t = now()
        if clip_quiet["until"] is not None and t < clip_quiet["until"]:
            return Response(status_code=204)
        job = gameclips.claim(repo, str(body.get("worker_id", "clips")), t, settings.lease_s)
        if job is None:
            clip_quiet["until"] = t + timedelta(seconds=CLIP_QUIET_S)
            return Response(status_code=204)
        return {"job": {"id": job.id, "run_id": job.run_id, "video_id": job.video_id,
                        "spec": job.spec, "attempt": job.attempts, "lease_s": settings.lease_s}}

    @app.post("/api/worker/clips/{job_id}/progress")
    async def clips_progress(job_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_clip_job(job_id, body.get("worker_id"))
        lease = now() + timedelta(seconds=settings.lease_s)
        repo.update_clip_job(job.id, lease_expires_at=lease)
        return {"ok": True, "lease_expires_at": _iso(lease)}

    @app.post("/api/worker/clips/{job_id}/artifacts")
    async def clips_artifacts(job_id: str, request: Request,
                              authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_clip_job(job_id, body.get("worker_id"))
        asked = clip_names(job)
        uploads = []
        for f in body.get("files", []):
            name = str(f.get("name", ""))
            m = GAME_CLIP.match(name)
            if m is None or m.group(1) not in asked:
                raise HTTPException(400, f"{name} is not a clip this job asked for")
            if int(f.get("bytes") or 0) > CLIP_MAX_BYTES:
                raise HTTPException(400, f"{name} is over {CLIP_MAX_BYTES} bytes")
            key = clip_key(job.video_id, job.run_id, m.group(1))
            target = store.presign_put(key, "video/mp4")
            if target["url"].startswith("memory://"):
                # No real bucket behind this store: take the bytes ourselves.
                target = {"url": url_for(f"/api/worker/clips/{job.id}/upload?key={key}"),
                          "headers": {"Content-Type": "video/mp4"}}
            uploads.append({"name": name, "key": key, **target})
        return {"uploads": uploads}

    @app.put("/api/worker/clips/{job_id}/upload")
    async def clips_upload(job_id: str, request: Request, key: str = Query(...),
                           authorization: str | None = Header(default=None)):
        """The fallback upload path, used only when the store cannot sign URLs."""
        require_worker(authorization)
        job = repo.get_clip_job(job_id)
        if job is None or job.state != "running":
            raise HTTPException(409, "this clip job is not running")
        if key not in {clip_key(job.video_id, job.run_id, n) for n in clip_names(job)}:
            raise HTTPException(400, "that key does not belong to this clip job")
        data = await request.body()
        if len(data) > CLIP_MAX_BYTES:
            raise HTTPException(413, "that clip is too big")
        store.put_bytes(key, data, "video/mp4")
        return {"ok": True, "bytes": len(data)}

    @app.post("/api/worker/clips/{job_id}/complete")
    async def clips_complete(job_id: str, request: Request,
                             authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = repo.get_clip_job(job_id)
        if job is None:
            raise HTTPException(404, "no such clip job")
        if job.state == "done":
            return {"ok": True, "already": True}
        job = owned_clip_job(job_id, body.get("worker_id"))
        repo.update_clip_job(job.id, state="done", finished_at=now(), lease_expires_at=None,
                             rocks=gameclips.clean_results(job, body.get("rocks")))
        return {"ok": True}

    @app.post("/api/worker/clips/{job_id}/fail")
    async def clips_fail(job_id: str, request: Request,
                         authorization: str | None = Header(default=None)):
        require_worker(authorization)
        body = await request.json()
        job = owned_clip_job(job_id, body.get("worker_id"))
        final = gameclips.fail(repo, job, str(body.get("error", ""))[:500], now())
        return {"ok": True, "final": final}

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
        # A ready run retried is read again in place, so what its games'
        # thinking summaries were read from is about to be replaced. Drop them
        # and the report counts the games as pending until /complete writes
        # new ones, rather than showing numbers nothing backs any more.
        for src in repo.sources_for_video(run.video_id):
            if src.current_run_id == run.id and src.thinking is not None:
                repo.update_source(src.id, thinking=None)
        repo.update_run(run.id, status="queued", error=None)
        repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued", created_at=t, run_after=t))
        return {"ok": True}

    @app.post("/api/admin/reprocess")
    async def admin_reprocess(request: Request, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        body = await request.json()
        vid = source.video_id(str(body["video_id"]))
        # A reprocess is how a game read as the wrong format gets read again,
        # so it may say which; left out, it is the base run's.
        asked = requested_format(body.get("format"))
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
                  format=asked or allowed_format(base.format if base else None),
                  playlist_id=base.playlist_id if base else None)
        repo.put_run(run)
        repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued", created_at=t, run_after=t))
        return {"ok": True, "run_id": run.id}

    @app.post("/api/admin/clips/backfill")
    def admin_clips_backfill(recut: bool = False,
                             authorization: str | None = Header(default=None)):
        """Ask for clips of every play's rocks, as opening each play's report
        would: for plays saved before clips existed. Nothing is cut here, and
        a game whose video has left every cache comes back video_gone from the
        clips worker. A game that cannot be read now is skipped and said.
        ``recut`` asks again for clips cut in an older style
        (gameclips.CLIP_STYLE); each keeps its old clip until the new one is
        made. Ask it once: a
        second ask before the first is cut queues every rock again."""
        require_admin(authorization)
        clips_on()
        skipped, done = [], 0
        for play in repo.all_plays():
            src = canonical_source(play.source_id)
            try:
                if src is None:
                    raise HTTPException(404, "no such game")
                doc, run, _ = play_doc(play, src)
            except HTTPException as exc:
                skipped.append({"play_id": play.id, "source_id": play.source_id,
                                "why": exc.detail})
                continue
            if ask_for_clips(play, doc, run, again=recut) is None:
                skipped.append({"play_id": play.id, "source_id": play.source_id,
                                "why": "asking failed; see the log"})
                continue
            done += 1
        log.info("clips backfill: %d plays asked for, %d skipped", done, len(skipped))
        return {"plays": done + len(skipped), "asked": done, "skipped": skipped}

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

    @app.post("/api/admin/sources/{source_id}/practice")
    async def admin_source_practice(source_id: str, request: Request,
                                    authorization: str | None = Header(default=None)):
        """Say by hand whether a game is practice: true hides it from the
        catalogue, false lists it whatever runs say, null hands it back to them."""
        require_admin(authorization)
        body = await request.json()
        said = body.get("practice")
        if said is not None and not isinstance(said, bool):
            raise HTTPException(422, "practice is true, false or null")
        if repo.get_source(source_id) is None:
            raise HTTPException(404, "no such game")
        repo.update_source(source_id, practice_override=said)
        src = repo.get_source(source_id)
        log.info("source %s practice set by hand: %r", source_id, said)
        return {"source_id": src.id, "practice": src.practice,
                "practice_override": src.practice_override, "listed": not src.is_practice}

    @app.post("/api/admin/flags/{flag_id}/resolve")
    def admin_resolve_flag(flag_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        got = repo.resolve_flag(flag_id, now())
        if got is None:
            raise HTTPException(404, "no such flag")
        return _flag_json(got)

    @app.post("/api/admin/label-items")
    async def admin_label_items_create(request: Request,
                                       authorization: str | None = Header(default=None)):
        """Make items, a new one `pending` until its image is in the bucket.
        The reply says where to put each image; an item that already exists is
        left as it is, so the owner's script can run again after a failure."""
        require_admin(authorization)
        body = await request.json()
        given = body.get("items") if isinstance(body, dict) else None
        if not isinstance(given, list) or len(given) > MAX_LABEL_ITEMS_PER_CALL:
            raise HTTPException(422, f"items is a list of at most {MAX_LABEL_ITEMS_PER_CALL}")
        for g in given:
            if (not isinstance(g, dict) or not isinstance(g.get("id"), str)
                    or not LABEL_ID.match(g["id"])
                    or g.get("kind") not in LABEL_KINDS
                    or not isinstance(g.get("batch"), str) or not g["batch"]
                    or not isinstance(g.get("payload"), dict)
                    or not isinstance(g.get("reference"), (dict, type(None)))):
                raise HTTPException(422, "each item needs an id, kind, batch and payload")
        out = []
        for g in given:
            key = labelling.image_key(g["id"], g["kind"])
            have = repo.get_label_item(g["id"])
            if have is None:
                have = LabelItem(id=g["id"], kind=g["kind"], created_at=now(), batch=g["batch"],
                                 payload={**g["payload"], "image": key},
                                 reference=g.get("reference"))
                repo.put_label_item(have)
            # Only a pending item still wants its image; anything further
            # along has one and must not be pointed at a fresh upload.
            out.append({"id": have.id, "state": have.state,
                        "upload": (store.presign_put(key, "image/jpeg")
                                   if have.state == "pending" else None)})
        return {"items": out}

    @app.post("/api/admin/label-items/ready")
    async def admin_label_items_ready(request: Request,
                                      authorization: str | None = Header(default=None)):
        """Open the pending items whose image has arrived."""
        require_admin(authorization)
        body = await request.json()
        ids = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(422, "ids is a list of item ids")
        opened, missing = [], []
        for item_id in ids:
            got = repo.get_label_item(item_id)
            if got is None or got.state != "pending":
                continue
            if store.exists(got.payload["image"]):
                repo.update_label_item(item_id, state="open")
                opened.append(item_id)
            else:
                missing.append(item_id)
        return {"opened": opened, "missing_image": missing}

    @app.get("/api/admin/label-items")
    def admin_label_items(kind: str, state: str, after: str | None = None,
                          limit: int = Query(100, ge=1, le=500),
                          authorization: str | None = Header(default=None)):
        """Items in a state, oldest first, each with its answers. Page on with
        `after`, the `after` of the last page."""
        require_admin(authorization)
        if kind not in LABEL_KINDS or state not in LABEL_STATES:
            raise HTTPException(422, "kind is box or release; state is "
                                + ", ".join(LABEL_STATES))
        since = None
        if after:
            try:
                since = datetime.fromisoformat(after)
            except ValueError:
                raise HTTPException(422, "after is an ISO time") from None
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        items = repo.list_label_items(kind, state, limit, since)
        return {"items": [{**i.to_dict(), "image_url": label_image_url(i),
                           "answers": [a.to_dict() for a in repo.label_answers(i.id)]}
                          for i in items],
                "after": items[-1].created_at.isoformat() if items else None}

    @app.get("/api/admin/team-name-edits")
    def admin_team_name_edits(source_id: str | None = None,
                              limit: int = Query(200, ge=1, le=1000),
                              authorization: str | None = Header(default=None)):
        """Who named which game's teams what, newest first: one game's with
        `source_id`, everyone's without. Each edit carries the account as it
        is now and the game's title, so a listing reads without lookups."""
        require_admin(authorization)
        edits = repo.team_name_edits(source_id, limit)
        users, games = {}, {}
        out = []
        for e in edits:
            if e.user_id not in users:
                users[e.user_id] = repo.get_user(e.user_id)
            if e.source_id not in games:
                games[e.source_id] = repo.get_source(e.source_id)
            u, src = users[e.user_id], games[e.source_id]
            out.append({"id": e.id, "source_id": e.source_id, "at": _iso(e.at),
                        "title": src.title if src else None,
                        "user": {"uid": e.user_id, "email": u.email if u else None,
                                 "name": u.name if u else None},
                        "before": e.before, "after": e.after,
                        "now": ({c: getattr(src, "team_" + c) for c in ("red", "yellow")}
                                if src else None)})
        return {"edits": out}

    def _account_json(u: User) -> dict:
        return {"id": u.id, "email": u.email, "name": u.name, "picture_url": u.picture_url,
                "created_at": _iso(u.created_at), "last_seen_at": _iso(u.last_seen_at),
                "role": u.role}

    @app.get("/api/admin/users")
    def admin_users(limit: int = Query(500, ge=1, le=1000),
                    authorization: str | None = Header(default=None)):
        """Every account, newest first, for an admin to validate: the admin
        token (scripts/users.py) or a signed-in admin (/users)."""
        require_any_admin(authorization)
        return {"users": [_account_json(u) for u in repo.list_users(limit)]}

    @app.put("/api/admin/users/{user_id}/role")
    def admin_set_role(user_id: str, body: dict,
                       authorization: str | None = Header(default=None)):
        """Validate an account, or take that away: `{"role": "validated"}` or
        `{"role": null}`. Admins are made and unmade only with the admin
        token, so a signed-in admin can neither make one nor change an
        admin's role, their own included."""
        by = require_any_admin(authorization)
        if "role" not in body or (body["role"] is not None and body["role"] not in ROLES):
            raise HTTPException(422, f"role is one of {', '.join(ROLES)}, or null for neither")
        role = body["role"]
        target = repo.get_user(user_id)
        if target is None:
            raise HTTPException(404, "no such account")
        if by is not None and "admin" in (role, target.role):
            raise HTTPException(403, "admins are made and unmade by the site's owner")
        got = repo.update_user(user_id, role=role) or target
        log.info("role of %s set to %s by %s", user_id, role, by.id if by else "the admin token")
        return _account_json(got)

    # ------------------------------------------------------------- review
    # Each night every game that finished processing is read for the signs
    # that something went wrong (curling_score.autoreview), and one that shows
    # them gets a ⚑ flag of its own beside the ones viewers send. Cloud
    # Scheduler calls this hourly from 03:00 to 06:00 (deploy/scheduler.sh,
    # REVIEW=1); each call does what fits in its time and leaves the rest.

    def review_candidates(since: datetime, until: datetime | None = None) -> list:
        """(source, run) for each game whose current run finished since
        ``since`` (and before ``until``) and has not been read at that finish,
        oldest first."""
        out = []
        for run in repo.runs_ready_since(since):
            if run.status != "ready" or run.ready_at is None:
                continue
            if until is not None and run.ready_at >= until:
                continue
            for src in repo.sources_for_video(run.video_id):
                if (src.current_run_id != run.id or src.merged_into is not None
                        or src.is_practice):
                    continue
                done = repo.get_review(review_id(src.id, run.id))
                if (done is not None and done.run_ready_at is not None
                        and done.run_ready_at >= run.ready_at):
                    continue
                out.append((src, run))
        out.sort(key=lambda pair: (pair[1].ready_at, pair[0].game_index))
        return out

    def review_baseline(before: datetime) -> autoreview.Baseline:
        """Normal, from the games read in the fortnight before ``before`` --
        never from the batch itself, so a bad night cannot hide in its own."""
        since = before - timedelta(days=autoreview.BASELINE_DAYS)
        return autoreview.Baseline.from_ends(
            (r.format, m) for r in repo.reviews_ready_between(since, before)
            if r.format and not r.error for m in r.ends)

    def auto_flag(src: Source, run: Run, got: autoreview.GameReview) -> Flag:
        fid = autoreview.flag_id(src.id, run.id, run.ready_at)
        first = next((f for f in got.findings if f.strength == "strong"), got.findings[0])
        earlier = next((r for r in repo.reviews_for_source(src.id)
                        if r.flag_id and r.flag_id != fid), None)
        extra = f"earlier auto-flag {earlier.flag_id} (run {earlier.run_id})" if earlier else ""
        return Flag(
            id=fid, created_at=now(), note=autoreview.summary(got, extra, MAX_FLAG_NOTE),
            where={"link": "g", "chart_id": None, "share_slug": None, "source_id": src.id,
                   "run_id": run.id, "video_id": run.video_id,
                   "processing_version": run.processing_version, "title": run.title},
            place={"game_index": src.game_index, "end": first.end, "rock": first.rock,
                   "t_video_s": first.t_video_s},
            origin="auto", findings=[f.to_dict() for f in got.findings + got.notes])

    def review_one(src: Source, run: Run, baseline, with_flag: bool):
        """One game read: its record and the flag it would raise. Writes nothing."""
        try:
            doc = game_doc(run, src.game_index, src, src.play_start_s)
            if not doc.get("games"):
                raise LookupError(f"game {src.game_index} is not in run {run.id}")
            got, error = autoreview.review_game(doc["games"][0], baseline), None
        except Exception as e:  # noqa: BLE001 - one bad game never stops the night
            log.exception("could not review %s (run %s)", src.id, run.id)
            got, error = None, f"{type(e).__name__}: {e}"[:500]
        flag = auto_flag(src, run, got) if with_flag and got and got.raise_flag else None
        record = Review(
            id=review_id(src.id, run.id), source_id=src.id, run_id=run.id,
            reviewed_at=now(), video_id=run.video_id, game_index=src.game_index,
            format=got.format if got else None, run_ready_at=run.ready_at,
            processing_version=run.processing_version,
            ends=[m.to_dict() for m in got.ends] if got else [],
            findings=[f.to_dict() for f in got.findings] if got else [],
            notes=[f.to_dict() for f in got.notes] if got else [],
            flag_id=flag.id if flag else None, error=error)
        return record, flag

    def iso_param(name: str, value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            t = datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, f"{name} must be an ISO time") from None
        return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)

    @app.post("/api/admin/review")
    def admin_review(dry_run: bool = False, flag: bool = True, since: str | None = None,
                     until: str | None = None, source_id: str | None = None,
                     limit: int = Query(autoreview.BATCH, ge=1, le=200),
                     authorization: str | None = Header(default=None)):
        """Read the games that finished since ``since`` (default: the last
        LOOKBACK_DAYS), and before ``until`` if given, and flag the ones that
        look wrong.

        Idempotent: a game is read once per finish of its run, and its flag's
        id is derived, so a repeated call never adds a second -- nor reopens
        one that was resolved. ``dry_run`` writes nothing and returns the flags
        it would raise; ``flag=false`` records what it read but raises nothing,
        to give a new deployment its baseline (with ``until``, leaving the most
        recent games for the first real night); ``source_id`` reads that one
        game again now. Call it until ``pending`` is 0.
        """
        require_admin(authorization)
        started = now()
        if source_id is not None:
            src = repo.get_source(source_id)
            if src is None:
                raise HTTPException(404, "no such game")
            run = repo.get_run(src.current_run_id)
            if run is None or run.status != "ready" or run.ready_at is None:
                raise HTTPException(409, "that game's run is not ready")
            batch = [(src, run)]
        else:
            t0 = (iso_param("since", since)
                  or started - timedelta(days=autoreview.LOOKBACK_DAYS))
            batch = review_candidates(t0, iso_param("until", until))
        baseline = review_baseline(min(r.ready_at for _s, r in batch)) if batch else None
        reviewed, flagged, errors, would = 0, 0, [], []
        for src, run in batch[:limit]:
            if reviewed and (now() - started).total_seconds() > autoreview.TIME_BUDGET_S:
                break
            record, fl = review_one(src, run, baseline, with_flag=flag)
            reviewed += 1
            if record.error:
                errors.append({"source_id": src.id, "error": record.error})
            if fl is not None:
                if dry_run:
                    would.append({"source_id": src.id, "title": run.title,
                                  "flag": _flag_json(fl)})
                    flagged += 1
                elif repo.put_flag_if_absent(fl):
                    flagged += 1
            if not dry_run:
                repo.put_review(record)
        log.info("review: %d read, %d flagged, %d errors, %d left",
                 reviewed, flagged, len(errors), len(batch) - reviewed)
        out = {"ok": True, "reviewed": reviewed, "flagged": flagged, "errors": errors,
               "pending": len(batch) - reviewed}
        if dry_run:
            out["would_flag"] = would
        return out

    # The playlists take the admin token (deploy/README.md's curl) or a
    # signed-in admin (/playlists). The poll below is the scheduler's alone.
    @app.get("/api/admin/playlists")
    def admin_playlists(authorization: str | None = Header(default=None)):
        require_any_admin(authorization)
        # The schedules are in the club's time, which the page has to say.
        return {"club_tz": settings.club_tz, "playlists": [
            {**p.to_dict(), "created_at": _iso(p.created_at), "last_polled_at": _iso(p.last_polled_at)}
            for p in repo.list_playlists()]}

    def playlist_id_from(text) -> str:
        """A playlist's id, from the id itself or any link that carries it.
        A video's link, or anything else that is not one, is refused: watched,
        it would fail three polls and switch itself off."""
        text = str(text or "").strip()
        found = parse_qs(urlparse(text).query).get("list")
        pid = found[0] if found else text
        if not PLAYLIST_ID.match(pid):
            raise HTTPException(422, "that is not a YouTube playlist's link or id")
        return pid

    def playlist_fields(body: dict, allowed: set) -> dict:
        unknown = set(body) - allowed
        if unknown:
            raise HTTPException(422, f"not a playlist setting: {', '.join(sorted(unknown))}")
        fields = dict(body)
        try:
            if "label" in fields:
                # It becomes the league of every run the playlist queues.
                fields["label"] = str(fields["label"] or "").strip()
                if not fields["label"]:
                    raise ValueError("a playlist needs a name")
                if len(fields["label"]) > 60:
                    raise ValueError("that name is too long for a league")
            if "schedule" in fields:
                fields["schedule"] = playlists.check_schedule(fields["schedule"])
            for key in ("live_poll_s", "idle_poll_s"):
                if key in fields and fields[key] is not None:
                    fields[key] = float(fields[key])
                    if fields[key] < 60:
                        raise ValueError(f"{key} is at least 60 seconds")
            if "live_poll_s" in fields and fields["live_poll_s"] is None:
                raise ValueError("live_poll_s cannot be empty")
            if "practice" in fields and not isinstance(fields["practice"], bool):
                raise ValueError("practice is true or false")
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e)) from e
        return fields

    @app.post("/api/admin/playlists", status_code=201)
    async def admin_add_playlist(request: Request, authorization: str | None = Header(default=None)):
        require_any_admin(authorization)
        body = await request.json()
        pid = playlist_id_from(body.pop("playlist_id", None))
        if any(p.playlist_id == pid for p in repo.list_playlists()):
            raise HTTPException(409, f"playlist {pid} is already watched")
        body["label"] = str(body.get("label") or "").strip() or pid
        fields = playlist_fields(body, {"label", "schedule", "live_poll_s", "idle_poll_s",
                                        "practice"})
        pl = playlists.new_watched_playlist(pid, fields.pop("label"), now())
        for key, value in fields.items():
            setattr(pl, key, value)
        repo.put_playlist(pl)
        return {"ok": True, "id": pl.id}

    @app.patch("/api/admin/playlists/{pid}")
    async def admin_change_playlist(pid: str, request: Request,
                                    authorization: str | None = Header(default=None)):
        """A playlist's label, schedule, polling intervals, or whether it is
        watched at all. Re-enabling one forgets the failures that disabled it."""
        require_any_admin(authorization)
        if repo.get_playlist(pid) is None:
            raise HTTPException(404, "no such playlist")
        fields = playlist_fields(await request.json(),
                                 {"label", "enabled", "schedule", "live_poll_s", "idle_poll_s",
                                  "practice"})
        if fields.get("enabled") is True:
            fields.update(failures=0, last_error=None)
        pl = repo.update_playlist(pid, **fields)
        return {"ok": True, "playlist": {**pl.to_dict(), "created_at": _iso(pl.created_at),
                                         "last_polled_at": _iso(pl.last_polled_at)}}

    @app.delete("/api/admin/playlists/{pid}")
    def admin_delete_playlist(pid: str, authorization: str | None = Header(default=None)):
        require_any_admin(authorization)
        repo.delete_playlist(pid)
        return {"ok": True}

    @app.post("/api/admin/poll-playlists")
    def admin_poll(force: bool = Query(False),
                   authorization: str | None = Header(default=None)):
        """The scheduler's call, every few minutes. Each playlist is polled
        when its own schedule says it is due; ``force`` polls every one."""
        require_admin(authorization)
        t = now()
        result = playlists.poll(
            repo, youtube, processing_version=settings.processing_version, now=t,
            allowed_channels=settings.allowed_channels or None,
            max_hours=min(settings.max_hours, dedupe.MAX_WHOLE_S / 3600),
            initial_status="pending_approval" if settings.require_approval else "queued",
            doubles_enabled=settings.doubles_enabled, tz=settings.club_tz,
            force=force, live=settings.live_enabled,
        )
        # A league stream going live closes practice on every sheet: leagues
        # use all five. Before the index's early return, so it never waits for
        # the hour; and a failure here must not stop the poll. The live run is
        # made once, so while one is being recorded with a practice watch
        # still running, every poll ends practice again: one failed ending
        # must not leave a session reading the cameras into the league.
        try:
            if result["live"] or (settings.practice_enabled
                                  and practice.league_over_practice(repo, t)):
                ended = practice.end_for_league(repo, t)
                if ended:
                    log.info("a league went live: ended %d practice session(s)", ended)
        except Exception as e:  # noqa: BLE001 - the poll has queued the league already
            log.warning("ending practice for a league failed: %s", e)
        # The league index rides on the first call of each hour, so a stream
        # the club files in its playlist after the game gets its league within
        # the hour. Every call would spend the whole channel's quota twenty
        # times over.
        if not (force or t.minute < 3):
            result["index"] = "not due"
            return result
        try:
            result["index"] = index_playlists(always_stamp=False)
        except Exception as e:  # noqa: BLE001 - the index must never stop the poll
            log.warning("playlist index failed: %s", e)
            result["index"] = {"error": str(e)}
        return result

    return app


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")
