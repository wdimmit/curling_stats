"""Every shot linked to the stream that shows it.

A camera game is read from a little before its stream's scheduled start, and
followed past its stream's end while it goes on (live/lane, playlists): some
of its shots are in no part of its own video. Its sheet's games in a row know
each other (Run.prev_run_id, next_run_id), and each knows the wall time of its
video's t = 0 (Run.wall_t0 at its recording's start, wall_t0_end at its end).
A shot before its own stream began is placed on the stream before; one after
its own ended, on the next -- when that stream was on at the moment. The shot
says which (``video``: ``{"id", "shift_s"}``, the video and how far its t is
from the shot's), or that no stream shows it (``video``: None). A shot in its
own stream is left as it is.
"""

import logging
from copy import deepcopy

from curling_score.ingest.source import watch_url_at

log = logging.getLogger(__name__)


def _length(run) -> float | None:
    """How long ``run``'s stream ran, or None while it is still on."""
    if run.published_at is None or run.stream_ended_at is None:
        return None
    return (run.stream_ended_at - run.published_at).total_seconds()


def _shot_t(shot):
    for key in ("t_release_s", "t_enter_s", "t_rest_s"):
        if shot.get(key) is not None:
            return float(shot[key])
    return None


def _outside(t, run) -> bool:
    length = _length(run)
    return t < 0 or (length is not None and t > length)


def annotate(doc: dict, run, get_run) -> dict:
    """``doc`` with each shot outside ``run``'s own stream given its
    ``video`` (and ``youtube_url``); ``doc`` itself when none is, or when
    anything goes wrong -- a link is never worth a page that fails."""
    try:
        return _annotate(doc, run, get_run)
    except Exception:  # noqa: BLE001
        log.exception("stream links for run %s", getattr(run, "id", None))
        return doc


def _annotate(doc, run, get_run):
    if getattr(run, "source", None) != "cameras" or run.wall_t0 is None:
        return doc
    shots = [s for g in doc.get("games", []) for e in g.get("ends", [])
             for s in e.get("shots") or []]
    if not any((t := _shot_t(s)) is not None and _outside(t, run) for s in shots):
        return doc
    out = {**doc, "games": deepcopy(doc.get("games", []))}
    neighbours = {}

    def neighbour(run_id):
        if run_id not in neighbours:
            neighbours[run_id] = get_run(run_id) if run_id else None
        return neighbours[run_id]

    for game in out["games"]:
        for end in game.get("ends", []):
            for shot in end.get("shots") or []:
                t = _shot_t(shot)
                if t is None or not _outside(t, run):
                    continue
                early = t < 0
                other = neighbour(run.prev_run_id if early else run.next_run_id)
                link = None
                if (other is not None and other.wall_t0 is not None
                        and other.published_at is not None):
                    wall = (run.wall_t0 if early else (run.wall_t0_end or run.wall_t0)) + t
                    other_t0 = (other.wall_t0_end or other.wall_t0) if early else other.wall_t0
                    other_t = wall - other_t0
                    length = _length(other)
                    if other_t >= 0 and (length is None or other_t <= length):
                        link = {"id": other.video_id, "shift_s": round(other_t - t, 2)}
                shot["video"] = link
                seek = shot.get("t_video_s")
                seek = t if seek is None else float(seek)
                shot["youtube_url"] = (None if link is None
                                       else watch_url_at(link["id"], seek + link["shift_s"]))
    return out
