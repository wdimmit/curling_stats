"""Practice sessions: a person's window on a sheet's practice stream.

The work for a sheet is one *watch*: a practice run and job that a worker
claims, records from the stream's DVR window, and publishes throw by throw
into the run's practice.json. A *session* is one person's Start-to-Stop on
that sheet. A second person starting on the same sheet joins the watch; each
sees the throws inside their own window. When no session on a watch is
active any more, the worker's next heartbeat is told to stop.

The stream is the sheet's practice stream on YouTube, or -- with the API's
PRACTICE_SOURCE=cameras -- the club's own cameras, read by a worker that can
see them. A camera watch has no YouTube video behind it, so it is named by
camera_video_id and its Run says ``source="cameras"``. Either way practice
only runs while no league game is: league_on.

A session ends when its owner stops it, after IDLE_S with no throw, after
MAX_S, or when its watch ends. The API keeps those clocks, on each practice
heartbeat and publish: the worker knows nothing of sessions.
"""

from datetime import datetime, timedelta, timezone

from curling_score.ingest import source
from curling_score.service import slug
from curling_score.service.records import Job, PracticeSession, PracticeWatch, Run

IDLE_S = 1200.0
MAX_S = 10800.0
# The footage before Start a worker records to calibrate from (practice.watch).
LOOKBACK_S = 1200.0
# A camera watch starts recording at Start: there is no DVR window to look back in.
CAMERA_LOOKBACK_S = 0.0
# A throw nothing saw released is dated this long before it came to rest.
REST_TO_THROW_S = 20.0
ENDED = ("ended", "failed")
# Run.source of a watch read from the club's cameras, and the sheets they cover.
CAMERAS = "cameras"
CAMERA_SHEETS = (1, 2, 3, 4, 5)
# A league game's run, while it is still on: queued for a worker, being
# recorded, or published end by end. Any such run made in the last
# LEAGUE_FRESH_S is a league on; an older one is a stream that was never
# closed out, which must not shut practice for ever.
LEAGUE_STATUSES = ("queued", "processing", "live")
LEAGUE_FRESH_S = 6 * 3600


class NoStream(Exception):
    """No live practice stream for that sheet (or, on the cameras, no such sheet)."""


class LeagueOn(Exception):
    """A league game is on; practice opens when it ends."""


def camera_video_id(sheet: int, now: datetime) -> str:
    """The name of a camera watch's recording, in a YouTube video id's place:
    which sheet, and when it started, so every watch has a name of its own."""
    return f"cams{sheet}-{now:%Y%m%d-%H%M%S}"


def league_on(repo, now) -> bool:
    """Whether a league game is on, on any sheet: leagues use every sheet, so
    while one is, nobody practises."""
    cutoff = now - timedelta(seconds=LEAGUE_FRESH_S)
    return any(run.kind == "live" and run.created_at >= cutoff
               for status in LEAGUE_STATUSES for run in repo.list_runs(status=status))


def live_stream_for_sheet(repo, youtube, sheet: int):
    """The sheet's live stream in a practice playlist, or None. Several
    playlists or several live streams of a sheet: the most recently published."""
    best = None
    for pl in repo.list_playlists():
        if not (pl.enabled and pl.practice):
            continue
        try:
            ids = youtube.playlist_video_ids(pl.playlist_id)
        except Exception:  # noqa: BLE001 - a playlist gone is no stream
            continue
        for meta in youtube.videos(ids) if ids else []:
            if meta.live_status != "live" or source.sheet_from_title(meta.title) != sheet:
                continue
            if best is None or (meta.published_at or datetime.min.replace(tzinfo=timezone.utc)) \
                    > (best.published_at or datetime.min.replace(tzinfo=timezone.utc)):
                best = meta
    return best


def watch_alive(repo, watch) -> bool:
    if watch.status in ENDED:
        return False
    job = repo.get_job(watch.job_id)
    return job is not None and job.state in ("queued", "running")


def start(repo, youtube, user, sheet: int, hand: str, now, processing_version: str,
          *, source: str = "youtube"):
    """A new session for ``user`` on ``sheet``: on the sheet's running watch,
    or a new one -- on its live YouTube stream, or with ``source="cameras"``
    on the club's cameras, which asks YouTube nothing. NoStream when there is
    no stream (or no such sheet on the cameras); LeagueOn while a league game
    is on, whichever the source."""
    if league_on(repo, now):
        raise LeagueOn(sheet)
    watch = next((w for w in repo.watches_for_sheet(sheet) if watch_alive(repo, w)), None)
    if watch is None:
        if source == CAMERAS:
            if sheet not in CAMERA_SHEETS:
                raise NoStream(sheet)
            run = Run(id=slug.new_run_id(), video_id=camera_video_id(sheet, now),
                      processing_version=processing_version, status="queued", created_at=now,
                      title=f"Sheet {sheet} - Practice on the cameras", sheet=sheet,
                      published_at=now, kind="practice", format="fours", source=CAMERAS)
        else:
            meta = live_stream_for_sheet(repo, youtube, sheet)
            if meta is None:
                raise NoStream(sheet)
            run = Run(id=slug.new_run_id(), video_id=meta.video_id,
                      processing_version=processing_version, status="queued", created_at=now,
                      title=meta.title, channel_id=meta.channel_id, sheet=sheet,
                      published_at=meta.published_at, kind="practice", format="fours")
        job = Job(id=slug.new_job_id(), run_id=run.id, state="queued", created_at=now,
                  run_after=now, kind="practice")
        watch = PracticeWatch(id=slug.new_slug(slug.SHORT_BYTES, "pw_"), sheet=sheet,
                              video_id=run.video_id, run_id=run.id, job_id=job.id,
                              created_at=now)
        repo.put_run(run)
        repo.put_job(job)
        repo.put_watch(watch)
    session = PracticeSession(id=slug.new_slug(slug.SHORT_BYTES, "ps_"), user_id=user.id,
                              sheet=sheet, watch_id=watch.id, started_at=now,
                              hand="left" if hand == "left" else "right")
    repo.put_session(session)
    return session


def thrown_at(doc, th):
    """When a throw was released, by the wall clock; None without one."""
    wall_t0 = doc.get("wall_t0")
    t = th.get("t_release_s")
    if t is None and th.get("t_rest_s") is not None:
        t = th["t_rest_s"] - REST_TO_THROW_S
    if wall_t0 is None or t is None:
        return None
    return datetime.fromtimestamp(wall_t0 + (t - (doc.get("t0_s") or 0.0)), tz=timezone.utc)


def _iso(dt):
    return None if dt is None else dt.isoformat()


def in_window(doc, session, now):
    """The throws in ``doc`` released between the session's Start and Stop
    (or ``now``), each with when that was."""
    doc = doc or {}
    end = session.ended_at or now
    for th in doc.get("throws", []):
        at = thrown_at(doc, th)
        if at is not None and session.started_at <= at <= end:
            yield th, at


def placed(th) -> bool:
    """A stone that arrived with nothing seen releasing it was placed by hand
    for a drill, not thrown. The page leaves these out (core/practice.mjs
    shown), so the count does too."""
    return th.get("arrived") and th.get("release_source") is None


def throw_count(doc, session, now) -> int:
    """How many throws the session's page shows as the owner's."""
    return sum(1 for th, _ in in_window(doc, session, now)
               if not placed(th) and th["id"] not in session.hidden)


def session_view(repo, doc, session, now) -> dict:
    """What the page reads: the session, its watch, and the throws in its
    window, each saying when it was thrown and whether it was hidden."""
    watch = repo.get_watch(session.watch_id)
    status = ("active" if session.active
              else "failed" if session.end_reason == "failed" else "ended")
    if session.active and watch is not None and not watch_alive(repo, watch):
        job = repo.get_job(watch.job_id)
        status = "failed" if (watch.status == "failed"
                              or (job is not None and job.state == "failed")) else "ended"
    throws = [{**th, "released_at": _iso(at), "hidden": th["id"] in session.hidden}
              for th, at in in_window(doc, session, now)]
    return {
        "session": {"id": session.id, "sheet": session.sheet, "hand": session.hand,
                    "started_at": _iso(session.started_at), "ended_at": _iso(session.ended_at),
                    "end_reason": session.end_reason, "status": status},
        "watch": {"status": None if watch is None else watch.status,
                  "recorded_s": None if watch is None else watch.recorded_s,
                  "busy_s": None if watch is None else watch.busy_s},
        "throws": throws,
    }


def stop(repo, session, now, reason="stop"):
    if session.active:
        repo.update_session(session.id, ended_at=now, end_reason=reason)
        session.ended_at, session.end_reason = now, reason


def settle(repo, session, now):
    """End a session whose watch is gone -- its job failed or was finished
    without a word reaching us -- so it never reads as active for ever and
    the sheet's page can start afresh."""
    if not session.active:
        return session
    watch = repo.get_watch(session.watch_id)
    if watch is not None and watch_alive(repo, watch):
        return session
    job = repo.get_job(watch.job_id) if watch is not None else None
    failed = watch is None or watch.status == "failed" or (job is not None
                                                           and job.state == "failed")
    stop(repo, session, now, "failed" if failed else "stream_ended")
    return session


# The throws a session can hide: a session is a few hundred throws at most.
MAX_HIDDEN = 500


def hide(repo, session, throw_id: str, hidden: bool):
    ids = [t for t in session.hidden if t != throw_id] + ([throw_id] if hidden else [])
    repo.update_session(session.id, hidden=ids[-MAX_HIDDEN:])


def sweep(repo, watch, doc, now) -> int:
    """End the sessions on ``watch`` that have run out; how many are left."""
    times = [t for t in (thrown_at(doc, th) for th in (doc or {}).get("throws", []))
             if t is not None]
    left = 0
    for s in repo.sessions_for_watch(watch.id):
        if not s.active:
            continue
        if (now - s.started_at).total_seconds() >= MAX_S:
            stop(repo, s, now, "max")
            continue
        last = max([s.started_at] + [t for t in times if t >= s.started_at])
        if (now - last).total_seconds() >= IDLE_S:
            stop(repo, s, now, "idle")
            continue
        left += 1
    return left


def end_all(repo, watch, now, reason):
    for s in repo.sessions_for_watch(watch.id):
        stop(repo, s, now, reason)
