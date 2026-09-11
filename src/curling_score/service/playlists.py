"""Watch a league's playlist and queue each new recording as it appears.

The club streams every sheet on league night and drops the recordings into one
playlist per league. Polling that playlist once an hour catches each VOD within
the hour after YouTube finishes archiving it -- "after the league finishes each
week" without a weekly schedule that would have to know the fixture list.
"""

from datetime import datetime

from curling_score.service import slug
from curling_score.service.records import Job, Run, WatchedPlaylist
from curling_score.service.youtube import SubmissionError, validate_submission

# A backfill of a whole season should trickle, not flood: this many new runs
# per poll, the rest next hour.
MAX_AUTO_PER_POLL = 10
# A playlist that fails this many polls running is probably gone; stop asking
# and show it in admin rather than spend quota on it every hour.
DISABLE_AFTER_FAILURES = 3


def poll(repo, youtube, *, processing_version: str, now: datetime,
         allowed_channels: set[str] | None = None, max_hours: float = 5.0,
         max_new: int = MAX_AUTO_PER_POLL, initial_status: str = "queued") -> dict:
    """Look at every enabled playlist once. Returns what happened, for the log."""
    created, skipped, errors = [], [], []
    for pl in repo.list_playlists():
        if not pl.enabled:
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
    return {"created": created, "skipped": skipped, "errors": errors}


def new_watched_playlist(playlist_id: str, label: str, now: datetime) -> WatchedPlaylist:
    return WatchedPlaylist(id=slug.new_slug(slug.SHORT_BYTES, "p_"),
                           playlist_id=playlist_id, label=label, created_at=now)
