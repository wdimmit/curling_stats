"""Which YouTube playlist each game's stream was published in.

The club files every stream in one playlist per league or event -- "2025-2026
Tuesday Super League", "2026 5U National Championship" -- and that title,
unlike the league people type into the catalogue, is spelled the same way for
every game in it. That makes it the thing to group a league's games by.

YouTube lists a playlist's videos but has no call for a video's playlists, so
this indexes the channel instead: list its playlists, fifty to a call, and
read the videos of only those whose count has moved since the last pass. The
first pass over a channel with two hundred playlists is a few hundred calls,
so each pass reads at most `max_scans` of them and says how many it left, and
the hourly poll finishes the job.
"""

import logging
from datetime import timedelta

from curling_score.service.records import PlaylistIndexEntry

log = logging.getLogger(__name__)

# How many playlists one pass reads the videos of. Each is a call per fifty
# videos, and the pass runs inside a request with Cloud Run's 60 s limit.
MAX_SCANS = 40
# A playlist whose count has not moved is read again after this anyway: a
# video swapped for another leaves the count where it was.
RESCAN_AFTER = timedelta(days=7)


def channels_to_index(repo, allowed_channels) -> list[str]:
    """The club's channel when the service is limited to it, otherwise every
    channel a run has come from."""
    if allowed_channels:
        return sorted(allowed_channels)
    return sorted({r.channel_id for r in repo.list_runs() if r.channel_id})


def refresh(repo, youtube, channels, now, max_scans: int = MAX_SCANS) -> dict:
    """Bring the index up to date with YouTube, a bounded amount at a time."""
    known = {e.id: e for e in repo.list_playlist_index()}
    scanned = retitled = emptied = pending = failed = 0
    for channel in channels:
        listed = set()
        for meta in youtube.channel_playlists(channel):
            listed.add(meta.playlist_id)
            have = known.get(meta.playlist_id)
            current = (have is not None and have.item_count == meta.item_count
                       and now - have.scanned_at < RESCAN_AFTER)
            if current:
                if have.title != meta.title:
                    have.title = meta.title
                    repo.put_playlist_index(have)
                    retitled += 1
                continue
            if scanned + failed >= max_scans:
                pending += 1
                continue
            try:
                ids = youtube.playlist_video_ids(meta.playlist_id)
            except Exception as e:  # noqa: BLE001 - one playlist must not stop the rest
                # Deleted or made private between the listing and the read.
                # Tried again next pass; it spends budget so a quota error
                # cannot turn into a call per playlist.
                log.warning("playlist %s could not be read: %s", meta.playlist_id, e)
                failed += 1
                continue
            repo.put_playlist_index(PlaylistIndexEntry(
                id=meta.playlist_id, channel_id=channel, title=meta.title,
                item_count=meta.item_count, scanned_at=now, video_ids=ids))
            scanned += 1
        # A playlist the channel no longer lists holds nothing any more. There
        # is no delete for the index, and an empty entry claims no video.
        for e in known.values():
            if e.channel_id == channel and e.id not in listed and e.video_ids:
                e.item_count, e.video_ids, e.scanned_at = 0, [], now
                repo.put_playlist_index(e)
                emptied += 1
    log.info("playlist index: %d scanned, %d retitled, %d emptied, %d failed, %d pending",
             scanned, retitled, emptied, failed, pending)
    return {"scanned": scanned, "retitled": retitled, "emptied": emptied,
            "failed": failed, "pending": pending}


def assignments(entries) -> dict:
    """video id -> the playlist entry it belongs to.

    A video can sit in more than one playlist: the club has kept whole-season
    catch-alls ("2023-2024 GCC Curling Season", 525 videos) beside the league
    ones. The smallest playlist holding a video is the most specific name for
    it, so it wins; the id breaks a tie so the answer never flips between
    passes.
    """
    best = {}
    for e in entries:
        for v in e.video_ids:
            cur = best.get(v)
            if cur is None or (e.item_count, e.id) < (cur.item_count, cur.id):
                best[v] = e
    return best


def stamp(repo, sources=None) -> int:
    """Write each source's playlist from the index; returns how many changed.

    A video the index does not hold gets none, which is also what un-stamps a
    game whose stream was taken out of its playlist."""
    best = assignments(repo.list_playlist_index())
    changed = 0
    for s in repo.list_sources() if sources is None else sources:
        e = best.get(s.video_id)
        want = (e.id, e.title) if e else (None, None)
        if (s.playlist_id, s.playlist_title) != want:
            repo.update_source(s.id, playlist_id=want[0], playlist_title=want[1])
            changed += 1
    return changed
