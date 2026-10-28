"""Practice sessions: a person's window on a sheet's cameras.

The work for a sheet is one *watch*: a practice run and job that a worker
claims, records from the sheet's cameras from its Start on, and publishes
throw by throw into the run's practice.json. A *session* is one person's
Start-to-Stop on that sheet. A second person starting on the same sheet joins
the watch; each sees the throws inside their own window. When no session on a
watch is active any more, the worker's next heartbeat is told to stop.

The cameras are the club's own, read by a worker on its network that says it
can see them. A watch has no YouTube video behind it, so it is named by
camera_video_id and its Run says ``source="cameras"``. Practice only runs
while no league game is: league_on. (Until 2026-10-25 practice could read a
sheet's YouTube practice stream instead. Those sessions are still read, but
none is started.)

A session ends when its owner stops it, after IDLE_S with no throw (counted
from Start when nothing has been thrown), after MAX_S, when a league feed goes
live (end_for_league), or when its watch ends. The API keeps those clocks, on
each practice heartbeat and publish: the worker knows nothing of sessions.
"""

import math
import re
from datetime import datetime, timedelta, timezone

from curling_score.service import slug
from curling_score.service.records import (Job, PracticeSession, PracticeWatch, Run,
                                           SensorTiming, sensor_timing_id)

IDLE_S = 900.0
MAX_S = 10800.0
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
# A run a worker is on: claimed, or published. Few at any time, unlike the
# queue of recordings waiting.
WORKED_ON = ("processing", "live")


# A person's own sensors, pushed by their app (records.SensorTiming). A timing
# is paired with the camera throw that crossed the throwing hog line nearest
# it, within MATCH_S: one person's throws are 20 s and more apart, so the
# phone's clock may be a few seconds out and still pair.
MATCH_S = 4.0
# A timing pushed this long after its session ended still belongs to it: an
# app that was out of signal catches up.
TIMING_GRACE_S = 120.0
# ...and one this far before Start, to a phone whose clock runs behind ours.
CLOCK_SLACK_S = 5.0
# A timing more than this far ahead of our clock is a clock gone wrong.
FUTURE_S = 60.0
# A push's sent_at further than this from our clock is not a phone clock that
# drifted but a bug in the app -- seconds sent as milliseconds, say.
SENT_AT_RANGE_S = 86400.0
# An unpaired timing this young, in a running session, is a throw the cameras
# have not published yet (about 15-20 s after it stops), not one they missed.
PENDING_S = 60.0
SHORT_S = (1.5, 8.0)
LONG_S = (6.0, 30.0)
# Which crossing the app's one time is: the back line, the throwing hog line
# or the far hog line.
AT_IS = ("back", "hog", "far")
CLIENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class BadTiming(ValueError):
    """A timing the app sent that cannot be read; the message says why."""


class NoSession(Exception):
    """No session of the person's held the timing's time."""


class NoCameras(Exception):
    """The club has no cameras on that sheet."""


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


def league_over_practice(repo, now) -> bool:
    """Whether a league game is being recorded while a practice watch is
    still running: the poll's cue to end practice again, should ending it when
    the league went live have failed. Reads only the runs being worked on."""
    cutoff = now - timedelta(seconds=LEAGUE_FRESH_S)
    runs = [run for status in WORKED_ON for run in repo.list_runs(status=status)]
    return (any(run.kind == "live" and run.created_at >= cutoff for run in runs)
            and any(run.kind == "practice" for run in runs))


def watch_alive(repo, watch) -> bool:
    if watch.status in ENDED:
        return False
    job = repo.get_job(watch.job_id)
    return job is not None and job.state in ("queued", "running")


def alive_watch(repo, sheet: int):
    """The sheet's running watch, which a Start there joins; None for none."""
    return next((w for w in repo.watches_for_sheet(sheet) if watch_alive(repo, w)), None)


def camera_watches(repo) -> int:
    """How many sheets are being read from the cameras now: what the Ryzen's
    link to them has to carry."""
    return sum(1 for sheet in CAMERA_SHEETS
               for w in repo.watches_for_sheet(sheet) if watch_alive(repo, w))


def start(repo, user, sheet: int, hand: str, now, processing_version: str):
    """A new session for ``user`` on ``sheet``: on the sheet's running watch,
    or a new one on its cameras. NoCameras for a sheet without any; LeagueOn
    while a league game is on."""
    if league_on(repo, now):
        raise LeagueOn(sheet)
    watch = alive_watch(repo, sheet)
    if watch is None:
        if sheet not in CAMERA_SHEETS:
            raise NoCameras(sheet)
        run = Run(id=slug.new_run_id(), video_id=camera_video_id(sheet, now),
                  processing_version=processing_version, status="queued", created_at=now,
                  title=f"Sheet {sheet} - Practice on the cameras", sheet=sheet,
                  published_at=now, kind="practice", format="fours", source=CAMERAS)
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
    timings = repo.timings_for_session(session.id)
    matched, unpaired = match_timings(doc, session, timings, now)
    throws = []
    for th, at in in_window(doc, session, now):
        th = {**th, "released_at": _iso(at), "hidden": th["id"] in session.hidden}
        if th["id"] in matched:
            timing, gap = matched[th["id"]]
            th["sensor"] = {**timing_view(timing), "gap_s": gap}
        throws.append(th)
    # A timing nothing paired yet, while the session runs, is most likely a
    # throw the cameras are still measuring.
    pending = status == "active" and session.active
    return {
        "session": {"id": session.id, "sheet": session.sheet, "hand": session.hand,
                    "started_at": _iso(session.started_at), "ended_at": _iso(session.ended_at),
                    "end_reason": session.end_reason, "status": status},
        "watch": {"status": None if watch is None else watch.status,
                  "recorded_s": None if watch is None else watch.recorded_s,
                  "busy_s": None if watch is None else watch.busy_s},
        "throws": throws,
        # Whether the page shows anything of sensors at all: only for a
        # session an app has pushed to.
        "has_sensors": bool(timings),
        "timings": [{**timing_view(t),
                     "pending": pending and (now - t.hog_at).total_seconds() < PENDING_S}
                    for t in unpaired],
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


def end_for_league(repo, now) -> int:
    """A league feed has gone live: end every active session on every running
    watch, on every sheet -- leagues use all five -- with reason "league", and
    say how many ended. Each watch's next heartbeat then finds nobody watching
    and is told to stop (api.practice_should_stop). A watch already gone is
    left for settle() to end as failed or stream_ended."""
    ended = 0
    for sheet in CAMERA_SHEETS:
        for watch in repo.watches_for_sheet(sheet):
            if not watch_alive(repo, watch):
                continue
            for s in repo.sessions_for_watch(watch.id):
                if s.active:
                    stop(repo, s, now, "league")
                    ended += 1
    return ended


# ------------------------------------------------------------ sensor timings
def _seconds(item, name, bounds):
    v = item.get(name)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise BadTiming(f"{name} is not a number")
    if not bounds[0] <= v <= bounds[1]:
        raise BadTiming(f"{name} must be {bounds[0]:g}-{bounds[1]:g} s")
    return float(v)


def clock_offset(sent_at_ms, now: datetime) -> float:
    """Our clock less the phone's, in seconds, from the push's ``sent_at``
    (the phone's clock as it sent): what puts the app's times on ours. It
    also counts the push's time in flight, a fraction of a second late."""
    if (isinstance(sent_at_ms, bool) or not isinstance(sent_at_ms, (int, float))
            or not math.isfinite(sent_at_ms)):
        raise BadTiming("sent_at must be epoch milliseconds")
    offset = now.timestamp() - sent_at_ms / 1000.0
    if abs(offset) > SENT_AT_RANGE_S:
        raise BadTiming("sent_at is more than a day from now: is it in milliseconds?")
    return offset


def read_timing(item, offset_s: float | None = None) -> dict:
    """One timing as the app sent it, checked: its client_id, the crossing
    ``at`` (epoch ms) marks, both durations, and from those ``hog_at``, when
    the rock crossed the throwing hog line -- on our clock when the push said
    how far the phone's is off (``offset_s``, clock_offset), else on the
    phone's. BadTiming says what is wrong."""
    if not isinstance(item, dict):
        raise BadTiming("a timing is an object")
    cid = item.get("client_id")
    if not isinstance(cid, str) or not CLIENT_ID.match(cid):
        raise BadTiming("client_id must be 1-64 letters, digits or ._:-")
    at_ms = item.get("at")
    if isinstance(at_ms, bool) or not isinstance(at_ms, (int, float)) or not math.isfinite(at_ms):
        raise BadTiming("at must be epoch milliseconds")
    at_is = item.get("at_is")
    if at_is not in AT_IS:
        raise BadTiming("at_is must be back, hog or far")
    short_s, long_s = _seconds(item, "short_s", SHORT_S), _seconds(item, "long_s", LONG_S)
    if short_s is None and long_s is None:
        raise BadTiming("send short_s, long_s or both")
    try:
        at = datetime.fromtimestamp(at_ms / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise BadTiming("at must be epoch milliseconds") from None
    if at_is == "back" and short_s is None:
        raise BadTiming("at_is back needs short_s")
    if at_is == "far" and long_s is None:
        raise BadTiming("at_is far needs long_s")
    hog_at = (at + timedelta(seconds=short_s) if at_is == "back"
              else at - timedelta(seconds=long_s) if at_is == "far" else at)
    if offset_s is not None:
        hog_at += timedelta(seconds=offset_s)
        offset_s = round(offset_s, 3)
    return {"client_id": cid, "at": at, "at_is": at_is, "short_s": short_s,
            "long_s": long_s, "hog_at": hog_at, "clock_offset_s": offset_s}


def session_for(repo, user_id: str, at: datetime, now: datetime):
    """The person's session whose window holds ``at``: Start (less the
    clock slack) to Stop (plus the grace), or to now while it runs."""
    for s in repo.sessions_for_user(user_id):
        settle(repo, s, now)
        lo = s.started_at - timedelta(seconds=CLOCK_SLACK_S)
        hi = (now if s.active else s.ended_at) + timedelta(seconds=TIMING_GRACE_S)
        if lo <= at <= hi:
            return s
    return None


def add_timing(repo, user_id: str, item, now: datetime, key_id: str | None = None,
               offset_s: float | None = None):
    """Keep one timing the app pushed, on the person's session that held it.
    A timing already kept under its client_id is handed back as it was, so
    the app may send again whatever it is unsure arrived. BadTiming for one
    that cannot be read; NoSession when no session of theirs held it.
    ``offset_s`` is the phone's clock against ours, from the push's sent_at."""
    got = read_timing(item, offset_s)
    tid = sensor_timing_id(user_id, got["client_id"])
    kept = repo.get_timing(tid)
    if kept is not None:
        return kept
    if (got["hog_at"] - now).total_seconds() > FUTURE_S:
        raise BadTiming("at is in the future: is the app's clock right?")
    session = session_for(repo, user_id, got["hog_at"], now)
    if session is None:
        raise NoSession()
    timing = SensorTiming(id=tid, user_id=user_id, session_id=session.id, received_at=now,
                          key_id=key_id, **got)
    repo.put_timing(timing)
    return timing


def _wall(doc, t):
    if t is None or doc.get("wall_t0") is None:
        return None
    return doc["wall_t0"] + (t - (doc.get("t0_s") or 0.0))


def match_timings(doc, session, timings, now):
    """Pair the session's timings with its throws by when each crossed the
    throwing hog line: the camera's ``t_hog_s``, or else its far crossing
    less the timing's long time. The closest pairs within MATCH_S go first,
    one throw to one timing. Hidden throws pair too, so a timing never turns
    up as unmatched because its throw was said to be someone else's.

    Gives ({throw id: (timing, gap_s)}, [unpaired timings]), where gap_s is
    the app's hog time less the camera's: the phone's clock against ours."""
    doc = doc or {}
    throws = [th for th, _ in in_window(doc, session, now) if not placed(th)]
    pairs = []
    for timing in timings:
        app = timing.hog_at.timestamp()
        for th in throws:
            cam = _wall(doc, th.get("t_hog_s"))
            if cam is None and timing.long_s is not None:
                far = _wall(doc, th.get("t_far_s"))
                cam = None if far is None else far - timing.long_s
            if cam is not None and abs(app - cam) <= MATCH_S:
                pairs.append((abs(app - cam), app - cam, timing.id, th["id"]))
    matched, used = {}, set()
    for _, gap, tid, thid in sorted(pairs):
        if tid in used or thid in matched:
            continue
        used.add(tid)
        matched[thid] = (next(t for t in timings if t.id == tid), round(gap, 2))
    return matched, [t for t in timings if t.id not in used]


def timing_view(timing) -> dict:
    return {"id": timing.id, "hog_at": _iso(timing.hog_at), "short_s": timing.short_s,
            "long_s": timing.long_s}
