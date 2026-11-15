"""Watch a league's playlist and queue each new recording as it appears.

The club streams every sheet on league night and drops the recordings into one
playlist per league. Polling that playlist once an hour catches each VOD within
the hour after YouTube finishes archiving it -- "after the league finishes each
week" without a weekly schedule that would have to know the fixture list.

A playlist can also carry a schedule: its league nights. Inside those windows
it is polled every few minutes, so a stream is caught as it goes live and, with
``live`` on, queued as a live job that follows the game while it is played.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from curling_score.ingest import source
from curling_score.service import slug
from curling_score.service.records import Job, Run, WatchedPlaylist
from curling_score.service.youtube import SubmissionError, validate_submission

# A backfill of a whole season should trickle, not flood: this many new runs
# per poll, the rest next hour.
MAX_AUTO_PER_POLL = 10
# And the recordings one poll queues are each this much later than the last.
# A full download takes 6-9 minutes. 10/16 two polls queued five apiece at
# once, and YouTube bot-checked the worker's address for two hours.
DOWNLOAD_STAGGER_S = 600
# A playlist that fails this many polls running is probably gone; stop asking
# and show it in admin rather than spend quota on it every hour.
DISABLE_AFTER_FAILURES = 3
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# The scheduler fires every three minutes, a few seconds either way. A playlist
# polled a little under its interval ago is due, or a 180 s interval would slip
# to 360 s.
DUE_SLACK_S = 20.0
# A sheet's standby (its cameras recorded from the schedule's start, ahead of
# its stream) that no stream has taken over by this long after it was opened
# is given up and its recording deleted: the sheet has no game tonight, or its
# stream went somewhere the poll cannot see. Its stream, if it turns up after
# all, is read as one without a standby is. Streams go up about 30 min into
# the window (Monday Open: 18:00 window, 18:30 streams).
STANDBY_MAX_S = 90 * 60


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def check_schedule(schedule) -> list:
    """``schedule`` if it is a list of well-formed windows; ValueError if not."""
    if not isinstance(schedule, list):
        raise ValueError("a schedule is a list of windows")
    for w in schedule:
        if not isinstance(w, dict) or set(w) != {"days", "start", "end"}:
            raise ValueError('a window is {"days": [...], "start": "HH:MM", "end": "HH:MM"}')
        if not w["days"] or any(d not in DAYS for d in w["days"]):
            raise ValueError(f"days are some of {', '.join(DAYS)}")
        for key in ("start", "end"):
            text = w[key]
            ok = (isinstance(text, str) and len(text) == 5 and text[2] == ":"
                  and text[:2].isdigit() and text[3:].isdigit()
                  and int(text[:2]) < 24 and int(text[3:]) < 60)
            if not ok:
                raise ValueError(f"{key} is a 24-hour HH:MM time, not {text!r}")
    return schedule


def in_window(schedule, now: datetime, tz: str) -> bool:
    """Whether ``now`` falls in one of the windows, read in the club's time.

    A window whose end is before its start runs past midnight into the next
    day, so a late draw's 22:00-01:00 still counts at half past midnight.
    """
    local = now.astimezone(ZoneInfo(tz))
    day, prev = DAYS[local.weekday()], DAYS[(local.weekday() - 1) % 7]
    t = local.hour * 60 + local.minute
    for w in schedule or []:
        start, end = _minutes(w["start"]), _minutes(w["end"])
        if start < end:
            if day in w["days"] and start <= t < end:
                return True
        elif (day in w["days"] and t >= start) or (prev in w["days"] and t < end):
            return True
    return False


def window_start(schedule, now: datetime, tz: str) -> datetime | None:
    """When the window ``now`` is in began, or None outside every window."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    day, prev = DAYS[local.weekday()], DAYS[(local.weekday() - 1) % 7]
    t = local.hour * 60 + local.minute

    def at(date, minutes):
        return datetime(date.year, date.month, date.day, minutes // 60, minutes % 60,
                        tzinfo=zone).astimezone(now.tzinfo)

    for w in schedule or []:
        start, end = _minutes(w["start"]), _minutes(w["end"])
        if start < end:
            if day in w["days"] and start <= t < end:
                return at(local, start)
        elif day in w["days"] and t >= start:
            return at(local, start)
        elif prev in w["days"] and t < end:
            return at(local - timedelta(days=1), start)
    return None


def due(pl, now: datetime, tz: str) -> bool:
    """Whether a playlist should be polled now, by its schedule."""
    if pl.last_polled_at is None:
        return True
    interval = pl.live_poll_s if in_window(pl.schedule, now, tz) else pl.idle_poll_s
    if interval is None:
        return False
    return (now - pl.last_polled_at).total_seconds() >= interval - DUE_SLACK_S


# A camera game that is queued, or recording, or finishing its last ends.
_CAMERA_GOING = ("queued", "processing", "live")


# A camera game whose stream has ended lets its cameras go at the worker's
# next heartbeat (live.manager.HEARTBEAT_S, 60 s): until then its sheet is
# still being read.
RELEASE_S = 120.0
# A stream on a sheet still read by the game before it waits this long for
# the cameras, then is read from the stream instead.
CAMERA_WAIT_S = 15 * 60.0


def camera_games(repo) -> int:
    """On how many sheets league games are being read from the cameras,
    standbys among them: what the cap (CAMERA_MAX_SHEETS) counts. Two games
    on one sheet (the next recorded while the last finishes) are one sheet. A
    game whose stream has ended, finishing its last ends, reads no camera any
    more."""
    return len({r.sheet for status in _CAMERA_GOING for r in repo.list_runs(status=status, limit=500)
                if r.kind in ("live", "standby") and r.source == "cameras"
                and r.stream_ended_at is None})


# A sheet's next stream is recorded from this long before its scheduled
# start: its game may begin while the last league's stream is still on.
LOOKAHEAD_S = 600.0
# A game started on a sheet still read by the one before waits at most this
# long for that one to say where its own game ended (Run.own_until), then is
# read from its opening.
FLOOR_WAIT_S = 45 * 60.0
# A camera game with no lookahead opening is read from this long before its
# stream began: its first rocks may come before the stream does.
EARLY_READ_S = 600.0


def camera_game_fields(repo, run, now: datetime) -> dict:
    """What a camera game's worker is told of its place on the sheet: where to
    read from (``analysis_from``), when the next game opened on its sheet
    (``handoff_at``), and the floor of its own span -- where the game before
    said its own ended, its opening when there is no camera game before it
    or that one cannot say -- or that it is still to be said
    (``floor_pending``)."""
    def iso(t):
        return t.isoformat() if t is not None else None

    start = run.opened_at or (run.published_at - timedelta(seconds=EARLY_READ_S)
                              if run.published_at is not None else None)
    floor, pending = start, False
    prev = repo.get_run(run.prev_run_id) if run.prev_run_id else None
    if prev is not None and prev.source == "cameras" and prev.status != "failed":
        if prev.own_until is not None:
            floor = prev.own_until
        elif run.opened_at is None or now < run.opened_at + timedelta(seconds=FLOOR_WAIT_S):
            floor, pending = None, True
    return {"analysis_from": iso(start), "handoff_at": iso(run.handoff_at),
            "floor_at": iso(floor), "floor_pending": pending}


def _sheet_reader(repo, sheet, but: str | None = None) -> Run | None:
    """The camera game (or standby) reading the sheet's cameras now, if any."""
    going = [r for status in _CAMERA_GOING for r in repo.list_runs(status=status, limit=500)
             if r.sheet == sheet and r.source == "cameras" and r.kind in ("live", "standby")
             and r.id != but]
    return max(going, key=lambda r: r.created_at) if going else None


def _expected(repo, video_id: str) -> bool:
    """Whether a run is, or waits to be, this stream's game."""
    return bool(repo.runs_for_video(video_id)) or any(
        r.expected_video_id == video_id for r in _waiting_standbys(repo))


def _waiting_standbys(repo) -> list:
    """The standbys still waiting for their streams, oldest first."""
    runs = [r for status in ("queued", "processing") for r in repo.list_runs(status=status, limit=500)
            if r.kind == "standby"]
    return sorted(runs, key=lambda r: r.created_at)


def _standby_for(repo, playlist_id: str, sheet, video_id: str | None = None) -> Run | None:
    """The sheet's standby for this playlist, if one is waiting and its job
    is queued or recording: the one opened for this stream first."""
    found = []
    for r in _waiting_standbys(repo):
        if r.playlist_id == playlist_id and r.sheet == sheet:
            job = repo.job_for_run(r.id)
            if job is not None and job.state in ("queued", "running"):
                found.append(r)
    for r in found:
        if video_id is not None and r.expected_video_id == video_id:
            return r
    return found[0] if found else None


def _sheet_busy(repo, sheet, now: datetime | None = None) -> bool:
    """Whether something is reading the sheet's cameras: a camera game whose
    stream has not ended (or, given ``now``, ended within ``RELEASE_S``), a
    standby, or practice. Two readers on one camera are never tried."""
    def reading(r):
        if r.stream_ended_at is None:
            return True
        return now is not None and (now - r.stream_ended_at).total_seconds() < RELEASE_S
    return any(r.sheet == sheet and r.source == "cameras" and reading(r)
               and r.kind in ("live", "standby", "practice")
               for status in _CAMERA_GOING for r in repo.list_runs(status=status, limit=500))


def _found_already(repo, playlist_id: str, sheet, since: datetime) -> bool:
    """Whether this window's stream on the sheet was found already."""
    return any(r.kind == "live" and r.playlist_id == playlist_id and r.sheet == sheet
               and r.created_at >= since
               for status in _CAMERA_GOING for r in repo.list_runs(status=status, limit=500))


def _give_up(repo, run, now: datetime, why: str) -> None:
    error = f"no stream on sheet {run.sheet}: {why}; its recording is deleted"
    job = repo.job_for_run(run.id)
    if job is not None and job.state in ("queued", "running"):
        # A worker recording it is told at its next heartbeat (409: not
        # its job any more), and deletes what it recorded.
        repo.update_job(job.id, state="failed", error=error, error_kind="permanent",
                        finished_at=now)
    repo.update_run(run.id, status="failed", error=error)


def standbys(repo, *, processing_version: str, now: datetime,
             tz: str = "America/Los_Angeles", camera_sheets: set | frozenset = frozenset(),
             camera_max_sheets: int = 0) -> dict:
    """Record each camera sheet from its league's schedule start.

    On an enabled ``cameras`` playlist whose window has just opened (within
    ``STANDBY_MAX_S``), each sheet in ``camera_sheets`` gets a standby: a run
    (kind "standby") whose job ("camera_standby") records the sheet's cameras
    until the poll finds its stream and hands the job to the game
    (``poll``). Once a window a sheet; while fewer than ``camera_max_sheets``
    camera games and standbys are going; never on a sheet whose cameras
    something else is reading (it waits for the next call), nor on one whose
    stream was found already.

    A standby no stream has taken over within ``STANDBY_MAX_S``, or whose
    playlist no longer reads the cameras in an open window, is given up: its
    job fails, and the worker deletes its recording. Returns the runs
    ``opened`` and ``closed``.
    """
    opened, closed = [], []
    pls = {pl.playlist_id: pl for pl in repo.list_playlists()}
    for run in _waiting_standbys(repo):
        pl = pls.get(run.playlist_id)
        if now - run.created_at >= timedelta(seconds=STANDBY_MAX_S):
            why = f"none within {STANDBY_MAX_S / 60:.0f} min of the schedule's start"
        elif pl is None or not pl.enabled or pl.input != "cameras":
            why = "its playlist stopped reading the cameras"
        elif run.expected_video_id is None and window_start(pl.schedule, now, tz) is None:
            # A standby opened for a scheduled stream keeps to its schedule,
            # not the playlist's window.
            why = "its playlist stopped reading the cameras"
        else:
            continue
        _give_up(repo, run, now, why)
        closed.append(run.id)

    # Each sheet's scheduled stream, from LOOKAHEAD_S before it: whatever the
    # sheet's cameras are doing -- the game before may still be on, read by a
    # second caller on each camera -- and whatever the window says.
    for pl in pls.values():
        if not pl.enabled or pl.input != "cameras":
            continue
        for up in pl.upcoming:
            sheet, vid = up.get("sheet"), up.get("video_id")
            try:
                at = datetime.fromisoformat(up["scheduled_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if (sheet not in camera_sheets or now < at - timedelta(seconds=LOOKAHEAD_S)
                    or now - at >= timedelta(seconds=STANDBY_MAX_S) or _expected(repo, vid)):
                continue
            prev = _sheet_reader(repo, sheet)
            if prev is None and camera_games(repo) >= camera_max_sheets:
                continue
            run = Run(id=slug.new_run_id(),
                      video_id=f"standby-{sheet}-{now.strftime('%Y%m%dT%H%M%S')}",
                      processing_version=processing_version, status="queued", created_at=now,
                      title=f"Sheet {sheet} - {pl.label} - waiting for the stream",
                      playlist_id=pl.playlist_id, league=pl.label, kind="standby",
                      sheet=sheet, source="cameras", expected_video_id=vid, opened_at=now,
                      prev_run_id=None if prev is None else prev.id)
            repo.put_run(run)
            repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued",
                             created_at=now, run_after=now, kind="camera_standby"))
            if prev is not None:
                repo.update_run(prev.id, next_run_id=run.id, handoff_at=now)
            opened.append(run.id)

    for pl in pls.values():
        if not pl.enabled or pl.input != "cameras":
            continue
        began = window_start(pl.schedule, now, tz)
        if began is None or now - began >= timedelta(seconds=STANDBY_MAX_S):
            continue
        key = began.isoformat()
        done = list(pl.standby_sheets) if pl.standby_window == key else []
        before = list(done)
        # A sheet whose stream is scheduled is opened for it (above).
        scheduled = {u.get("sheet") for u in pl.upcoming}
        for sheet in sorted(camera_sheets):
            if sheet in done or sheet in scheduled:
                continue
            if _found_already(repo, pl.playlist_id, sheet, began):
                done.append(sheet)
                continue
            if camera_games(repo) >= camera_max_sheets:
                break
            if _sheet_busy(repo, sheet):
                continue
            run = Run(id=slug.new_run_id(),
                      video_id=f"standby-{sheet}-{now.strftime('%Y%m%dT%H%M%S')}",
                      processing_version=processing_version, status="queued", created_at=now,
                      title=f"Sheet {sheet} - {pl.label} - waiting for the stream",
                      playlist_id=pl.playlist_id, league=pl.label, kind="standby",
                      sheet=sheet, source="cameras")
            repo.put_run(run)
            repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued",
                             created_at=now, run_after=now, kind="camera_standby"))
            opened.append(run.id)
            done.append(sheet)
        if pl.standby_window != key or done != before:
            repo.update_playlist(pl.id, standby_window=key, standby_sheets=sorted(done))
    return {"opened": opened, "closed": closed}


def _hand_over(repo, standby: Run, meta, pl, now: datetime) -> None:
    """The sheet's standby becomes its stream's game: the same run and job,
    now the stream's. A worker recording it learns at its next heartbeat."""
    repo.update_run(standby.id, video_id=meta.video_id, title=meta.title,
                    channel_id=meta.channel_id, published_at=meta.published_at,
                    league=pl.label, format=source.format_from_title(meta.title),
                    kind="live", source="cameras", created_at=now)
    repo.update_job(repo.job_for_run(standby.id).id, kind="camera_live")


def camera_playlists(repo) -> set:
    """The playlists with a camera game recording."""
    return {r.playlist_id for status in ("processing", "live")
            for r in repo.list_runs(status=status, limit=500)
            if r.kind == "live" and r.source == "cameras" and r.stream_ended_at is None}


def mark_stream_ended(repo, video_id: str, now: datetime,
                      ended_at: datetime | None = None) -> None:
    """A stream no longer live: its camera game, if one is going, is told to
    stop recording (the worker's heartbeat), once -- dated by when YouTube
    says it ended, else now. A shot after it is linked to the next stream."""
    for r in repo.runs_for_video(video_id):
        if (r.kind == "live" and r.source == "cameras" and r.status in ("processing", "live")
                and r.stream_ended_at is None):
            repo.update_run(r.id, stream_ended_at=ended_at or now)


# A stream counts as upcoming when it is scheduled within this of now.
UPCOMING_PAST_S = 30 * 60.0
UPCOMING_AHEAD_S = 16 * 3600.0


def upcoming_streams(metas, now: datetime) -> list[dict]:
    """The sheets' streams still to come among ``metas``, soonest first."""
    out = []
    for meta in metas:
        if meta.live_status != "upcoming" or meta.scheduled_at is None:
            continue
        lead = (meta.scheduled_at - now).total_seconds()
        sheet = source.sheet_from_title(meta.title)
        if sheet is None or not -UPCOMING_PAST_S <= lead <= UPCOMING_AHEAD_S:
            continue
        out.append({"video_id": meta.video_id, "sheet": sheet,
                    "scheduled_at": meta.scheduled_at.isoformat()})
    return sorted(out, key=lambda u: u["scheduled_at"])


def poll(repo, youtube, *, processing_version: str, now: datetime,
         allowed_channels: set[str] | None = None, max_hours: float = 5.0,
         max_new: int = MAX_AUTO_PER_POLL, initial_status: str = "queued",
         tz: str = "America/Los_Angeles", force: bool = False,
         camera_sheets: set | frozenset = frozenset(), camera_max_sheets: int = 0) -> dict:
    """Look at every enabled playlist that is due. Returns what happened.

    A run's format comes from its title. ``force`` polls every playlist
    whatever its schedule. A stream that has gone live is queued as a live
    job, which skips approval -- the admin chose the playlist -- and the
    recording cap, and leaves the video unseen until its recording is
    archived. On a ``cameras`` playlist a stream whose title names a sheet in
    ``camera_sheets`` is read from that sheet's cameras instead, while fewer
    than ``camera_max_sheets`` camera games are queued or going; any other is
    read from the stream, as on any playlist.
    """
    created, live_created, skipped, errors = [], [], [], []
    n_queued = 0

    watching = camera_playlists(repo)
    for pl in repo.list_playlists():
        # A camera game must hear its stream end within minutes, whatever the
        # schedule: its cameras record until it does (privacy page).
        camera_due = (pl.playlist_id in watching and
                      (pl.last_polled_at is None or
                       (now - pl.last_polled_at).total_seconds() >= pl.live_poll_s - DUE_SLACK_S))
        if not pl.enabled or not (force or camera_due or due(pl, now, tz)):
            continue
        try:
            ids = youtube.playlist_video_ids(pl.playlist_id)
        except Exception as exc:  # noqa: BLE001 - any failure counts against it
            failures = pl.failures + 1
            repo.update_playlist(pl.id, failures=failures, last_error=str(exc)[:200],
                                 last_polled_at=now,
                                 enabled=failures < DISABLE_AFTER_FAILURES)
            errors.append((pl.playlist_id, str(exc)[:200]))
            continue

        seen = set(pl.last_seen_video_ids)
        new_ids = [v for v in ids if v not in seen]
        accepted_now = []
        metas = youtube.videos(new_ids) if new_ids else []
        # Upcoming streams are never marked seen, so every poll sees them all.
        upcoming = upcoming_streams(metas, now) if pl.input == "cameras" else []
        for meta in metas:
            if meta.live_status != "live":
                mark_stream_ended(repo, meta.video_id, now, meta.ended_at)
            if meta.live_status == "live":
                if allowed_channels and meta.channel_id not in allowed_channels:
                    accepted_now.append(meta.video_id)
                    skipped.append((meta.video_id, "channel_not_allowed"))
                    continue
                # Once per stream: a live run that failed is left to the
                # recording's ordinary run, not queued again every poll.
                if not any(r.kind == "live" for r in repo.runs_for_video(meta.video_id)):
                    sheet = source.sheet_from_title(meta.title)
                    standby = (_standby_for(repo, pl.playlist_id, sheet, meta.video_id)
                               if pl.input == "cameras" and sheet is not None else None)
                    if standby is not None:
                        _hand_over(repo, standby, meta, pl, now)
                        live_created.append(standby.id)
                        continue
                    on_cameras = pl.input == "cameras" and sheet in camera_sheets
                    if on_cameras and _sheet_busy(repo, sheet, now):
                        # The game before it still has the cameras (back to
                        # back leagues): wait for them, the stream left unseen
                        # so the next poll looks again -- for a while.
                        waited = ((now - meta.published_at).total_seconds()
                                  if meta.published_at is not None else CAMERA_WAIT_S)
                        if waited < CAMERA_WAIT_S:
                            skipped.append((meta.video_id, "cameras_busy"))
                            continue
                        on_cameras = False
                    cameras = on_cameras and camera_games(repo) < camera_max_sheets
                    run = Run(
                        id=slug.new_run_id(), video_id=meta.video_id,
                        processing_version=processing_version, status="queued",
                        created_at=now, title=meta.title, channel_id=meta.channel_id,
                        published_at=meta.published_at, playlist_id=pl.playlist_id,
                        league=pl.label, format=source.format_from_title(meta.title),
                        kind="live", sheet=sheet, source="cameras" if cameras else None,
                    )
                    repo.put_run(run)
                    repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued",
                                     created_at=now, run_after=now,
                                     kind="camera_live" if cameras else "live"))
                    live_created.append(run.id)
                continue
            if len(created) >= max_new:
                break
            try:
                validate_submission(meta, allowed_channels, max_hours)
            except SubmissionError as exc:
                # A stream still live, or one YouTube has not archived yet, is
                # left unseen so the next poll looks again; anything else is
                # remembered as seen so it is not re-examined every hour.
                if exc.code not in ("still_live", "no_duration"):
                    accepted_now.append(meta.video_id)
                skipped.append((meta.video_id, exc.code))
                continue
            # A live run no worker ever started gives way to the recording:
            # left queued, it would stand in for a game nobody processed.
            for r in repo.runs_for_video(meta.video_id):
                if r.kind == "live" and r.status == "queued":
                    error = "the stream ended before a worker picked it up"
                    job = repo.job_for_run(r.id)
                    if job is not None and job.state == "queued":
                        repo.update_job(job.id, state="failed", error=error,
                                        error_kind="permanent", finished_at=now)
                    repo.update_run(r.id, status="failed", error=error)
            # A live run still going -- finishing its last ends -- keeps the
            # recording unseen until it is ready, so if it fails even now the
            # recording is still there to be queued the ordinary way.
            if any(r.kind == "live" and r.status in ("processing", "live")
                   for r in repo.runs_for_video(meta.video_id)):
                skipped.append((meta.video_id, "live_in_progress"))
                continue
            if any(r.processing_version == processing_version
                   and r.status != "failed"
                   for r in repo.runs_for_video(meta.video_id)):
                accepted_now.append(meta.video_id)
                continue
            run = Run(
                id=slug.new_run_id(), video_id=meta.video_id,
                processing_version=processing_version, status=initial_status,
                created_at=now, title=meta.title, channel_id=meta.channel_id,
                duration_s=meta.duration_s, published_at=meta.published_at,
                playlist_id=pl.playlist_id, league=pl.label,
                format=source.format_from_title(meta.title),
            )
            repo.put_run(run)
            if initial_status == "queued":
                # One poll can find a whole draw's recordings at once; spaced
                # out, the workers do not ask YouTube for them all together.
                n_queued += 1
                after = now + timedelta(seconds=DOWNLOAD_STAGGER_S * (n_queued - 1))
                repo.put_job(Job(id=slug.new_job_id(), run_id=run.id,
                                 state="queued", created_at=now, run_after=after))
            created.append(run.id)
            accepted_now.append(meta.video_id)
        repo.update_playlist(
            pl.id, last_polled_at=now, failures=0, last_error=None,
            last_seen_video_ids=sorted(seen | set(accepted_now)), upcoming=upcoming,
        )
    return {"created": created, "live": live_created, "skipped": skipped,
            "errors": errors}


def new_watched_playlist(playlist_id: str, label: str, now: datetime) -> WatchedPlaylist:
    return WatchedPlaylist(id=slug.new_slug(slug.SHORT_BYTES, "p_"),
                           playlist_id=playlist_id, label=label, created_at=now)
