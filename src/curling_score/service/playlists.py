"""Watch a league's playlist and queue each new recording as it appears.

The club streams every sheet on league night and drops the recordings into one
playlist per league. Polling that playlist once an hour catches each VOD within
the hour after YouTube finishes archiving it -- "after the league finishes each
week" without a weekly schedule that would have to know the fixture list.

A playlist can also carry a schedule: its league nights. Inside those windows
it is polled every few minutes, so a stream is caught as it goes live and, with
``live`` on, queued as a live job that follows the game while it is played.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from curling_score.ingest import source
from curling_score.service import slug
from curling_score.service.records import Job, Run, WatchedPlaylist
from curling_score.service.youtube import SubmissionError, validate_submission

# A backfill of a whole season should trickle, not flood: this many new runs
# per poll, the rest next hour.
MAX_AUTO_PER_POLL = 10
# A playlist that fails this many polls running is probably gone; stop asking
# and show it in admin rather than spend quota on it every hour.
DISABLE_AFTER_FAILURES = 3
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# The scheduler fires every three minutes, a few seconds either way. A playlist
# polled a little under its interval ago is due, or a 180 s interval would slip
# to 360 s.
DUE_SLACK_S = 20.0


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


def due(pl, now: datetime, tz: str) -> bool:
    """Whether a playlist should be polled now, by its schedule."""
    if pl.last_polled_at is None:
        return True
    interval = pl.live_poll_s if in_window(pl.schedule, now, tz) else pl.idle_poll_s
    if interval is None:
        return False
    return (now - pl.last_polled_at).total_seconds() >= interval - DUE_SLACK_S


def poll(repo, youtube, *, processing_version: str, now: datetime,
         allowed_channels: set[str] | None = None, max_hours: float = 5.0,
         max_new: int = MAX_AUTO_PER_POLL, initial_status: str = "queued",
         doubles_enabled: bool = False, tz: str = "America/Los_Angeles",
         force: bool = False, live: bool = False) -> dict:
    """Look at every enabled playlist that is due. Returns what happened.

    A run's format comes from its title, and a doubles title is read as fours
    unless ``doubles_enabled`` says the service makes doubles runs yet.
    ``force`` polls every playlist whatever its schedule. ``live`` queues a
    stream that has gone live as a live job, which skips approval -- the admin
    chose the playlist -- and the recording cap, and leaves the video unseen
    until its recording is archived.
    """
    created, live_created, skipped, errors = [], [], [], []

    def fmt_of(title):
        return source.format_from_title(title) if doubles_enabled else "fours"

    for pl in repo.list_playlists():
        if not pl.enabled or not (force or due(pl, now, tz)):
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
        for meta in youtube.videos(new_ids) if new_ids else []:
            if live and meta.live_status == "live":
                if allowed_channels and meta.channel_id not in allowed_channels:
                    accepted_now.append(meta.video_id)
                    skipped.append((meta.video_id, "channel_not_allowed"))
                    continue
                if not any(r.kind == "live" and r.status != "failed"
                           for r in repo.runs_for_video(meta.video_id)):
                    run = Run(
                        id=slug.new_run_id(), video_id=meta.video_id,
                        processing_version=processing_version, status="queued",
                        created_at=now, title=meta.title, channel_id=meta.channel_id,
                        published_at=meta.published_at, playlist_id=pl.playlist_id,
                        league=pl.label, format=fmt_of(meta.title), kind="live",
                    )
                    repo.put_run(run)
                    repo.put_job(Job(id=slug.new_job_id(), run_id=run.id, state="queued",
                                     created_at=now, run_after=now, kind="live"))
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
                format=fmt_of(meta.title),
            )
            repo.put_run(run)
            if initial_status == "queued":
                repo.put_job(Job(id=slug.new_job_id(), run_id=run.id,
                                 state="queued", created_at=now, run_after=now))
            created.append(run.id)
            accepted_now.append(meta.video_id)
        repo.update_playlist(
            pl.id, last_polled_at=now, failures=0, last_error=None,
            last_seen_video_ids=sorted(seen | set(accepted_now)),
        )
    return {"created": created, "live": live_created, "skipped": skipped,
            "errors": errors}


def new_watched_playlist(playlist_id: str, label: str, now: datetime) -> WatchedPlaylist:
    return WatchedPlaylist(id=slug.new_slug(slug.SHORT_BYTES, "p_"),
                           playlist_id=playlist_id, label=label, created_at=now)
