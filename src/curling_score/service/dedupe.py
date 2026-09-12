"""Which run a submission needs, and which game in it the person meant.

Two people who paste the same stream are asking about the same video, and the
expensive part -- downloading and detecting -- should happen once. But "the
same game" is not "the same video": a four-hour stream holds two, and the
start time a person gives is a rough pointer at one of them, not a boundary.
So a *run* is per video window, a *source* is per discovered game, and a chart
attaches to a source by snapping the requested time to what the segmenter
actually found.
"""

from datetime import datetime

from curling_score.service import slug
from curling_score.service.records import Chart, Run, Source

# A stream up to this long is analysed whole; a start time is then only a
# pointer at which game, not a bound on the work.
MAX_WHOLE_S = 5 * 3600.0
# Longer streams are analysed around the requested time: this far before it
# (a game rarely runs more than a few minutes early against its listing) and
# this far after (a full eight ends with generous overtime).
WINDOW_BEFORE_S = 600.0
WINDOW_AFTER_S = 4 * 3600.0
# How far outside a game's own span a start time may fall and still be read as
# that game. Games are at least 240 s apart, so padded windows stay disjoint.
SNAP_PAD_S = 120.0
# Two sources overlapping by more than this share of the shorter one are the
# same game seen by two runs.
SAME_GAME_OVERLAP = 0.5


class NeedsStartTime(Exception):
    """A stream too long to analyse whole, submitted without saying where."""


def window_for(duration_s: float, start_s: float | None):
    """The part of the video a run should cover: ``(start, end)`` or whole."""
    if duration_s <= MAX_WHOLE_S:
        return None, None
    if start_s is None:
        raise NeedsStartTime()
    return max(0.0, start_s - WINDOW_BEFORE_S), min(duration_s, start_s + WINDOW_AFTER_S)


def find_reusable_run(runs: list[Run], processing_version: str,
                      start_s: float | None) -> Run | None:
    """An existing run of the current version whose window holds ``start_s``."""
    live = ("pending_approval", "queued", "processing", "ready")
    candidates = [
        r for r in runs
        if r.processing_version == processing_version and r.status in live
        and r.covers(start_s)
    ]
    # Prefer one that is already done, then the most recent.
    candidates.sort(key=lambda r: (r.status != "ready", -r.created_at.timestamp()))
    return candidates[0] if candidates else None


def snap_to_game(games: list[dict], start_s: float | None):
    """Which discovered game a start time points at: ``(index, distance_s)``.

    No start time means the first game. A time inside a game's padded span is
    that game, at distance 0. Otherwise the nearest game, and the distance is
    reported so the page can say "no game at 1:23:45 -- showing the nearest".
    """
    if not games:
        return None, None
    ordered = sorted(games, key=lambda g: g["start_s"])
    if start_s is None:
        return ordered[0]["index"], 0.0
    for g in ordered:
        if g["start_s"] - SNAP_PAD_S <= start_s <= g["end_s"] + SNAP_PAD_S:
            return g["index"], 0.0

    def distance(g):
        if start_s < g["start_s"]:
            return g["start_s"] - start_s
        return start_s - g["end_s"]

    nearest = min(ordered, key=distance)
    return nearest["index"], distance(nearest)


def same_game(a_start, a_end, b_start, b_end) -> bool:
    overlap = min(a_end, b_end) - max(a_start, b_start)
    shorter = max(1e-9, min(a_end - a_start, b_end - b_start))
    return overlap / shorter > SAME_GAME_OVERLAP


def find_or_create_source(repo, run: Run, game: dict, now: datetime) -> Source:
    """The source for one discovered game, made once per game per video."""
    for s in repo.sources_for_video(run.video_id):
        if same_game(s.game_start_s, s.game_end_s, game["start_s"], game["end_s"]):
            # A newer run of the same game moves the source forward, but
            # existing charts stay pinned to the run they were made from.
            if s.current_run_id != run.id:
                repo.update_source(s.id, current_run_id=run.id,
                                   game_index=int(game["index"]))
            return s
    source = Source(
        id=slug.new_slug(slug.SHORT_BYTES, "s_"),
        video_id=run.video_id,
        game_start_s=float(game["start_s"]),
        game_end_s=float(game["end_s"]),
        current_run_id=run.id,
        game_index=int(game["index"]),
        created_at=now,
        title=run.title,
        sheet=run.sheet,
        league=run.league,
        played_at=run.published_at,
    )
    repo.put_source(source)
    return source


def resolve_chart(repo, chart: Chart, run: Run, now: datetime) -> Chart:
    """Attach a chart to the game its start time points at, once the run is ready.

    This is also where a team finds out it already had a chart for this game.
    Two teammates who paste the same unprocessed link both get a link back and
    both wait on the status page; only here, when the games are finally known,
    can we tell that they asked for the same one. Whoever claimed it first
    keeps it, and the other link starts pointing at theirs.

    Folding the loser away is safe rather than lossy: a chart whose run is not
    ready serves the status page, not the viewer, and its timeline 404s -- so
    there is nothing in it to lose. The one path that could put grading in an
    unresolved chart is a POST straight to its overrides, and that case is
    flagged rather than folded.
    """
    index, distance = snap_to_game(run.games, chart.requested_start_s)
    if index is None:
        return chart
    game = next(g for g in run.games if g["index"] == index)
    source = find_or_create_source(repo, run, game, now)
    fields = {"source_id": source.id, "game_index": index,
              "snap_distance_s": distance, "updated_at": now}
    key = chart.team_id or chart.owner_user_id
    if key:
        winner = repo.claim_chart(key, source.id, chart.id)
        if winner != chart.id:
            if chart.overrides:
                # Someone charted into this before it resolved. Two people's
                # grading is never merged behind their backs.
                return repo.update_chart(chart.id, duplicate_of=winner, **fields)
            return repo.update_chart(chart.id, superseded_by=winner, updated_at=now)
    return repo.update_chart(chart.id, **fields)


def resolve_charts_for_run(repo, run: Run, now: datetime) -> int:
    """Every chart waiting on this run gets its game; every game gets a source."""
    for game in run.games:
        find_or_create_source(repo, run, game, now)
    n = 0
    # Oldest first, so that when two teammates raced, the one who asked first
    # keeps their link as the team's. Neither repo promises an order.
    waiting = sorted(repo.charts_for_run(run.id), key=lambda c: (c.created_at, c.id))
    for chart in waiting:
        if chart.source_id is None and not chart.superseded_by:
            resolve_chart(repo, chart, run, now)
            n += 1
    return n
