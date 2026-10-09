"""The thinking-time report: in each league, the teams that took longest to
decide their shots, per end played.

It works from the summary each Source carries, never from a timeline, so the
page costs one list of sources however many games there are. The summary is
the game's `thinking_time` as the review link serves it -- after the warm-up
is trimmed -- and every figure in it is a lower bound: the first rock of each
end is never timed, and a throw the camera did not see is timed from an
estimate or not at all.
"""

from curling_score.ingest.source import league_from_title

TOP = 20


def summarize(game: dict, run_id: str, play_start_s: float | None) -> dict:
    """What the report needs from one trimmed game, keyed by what it was read
    from. A timeline from before thinking time existed reads as nothing
    measured, which the report leaves out rather than waiting on forever."""
    tt = game.get("thinking_time") or {}
    return {
        "run_id": run_id, "play_start_s": play_start_s,
        "ends": len(game.get("ends") or []),
        "red": float(tt.get("red") or 0.0), "yellow": float(tt.get("yellow") or 0.0),
        "measured_shots": int(tt.get("measured_shots") or 0),
        "unmeasured_shots": int(tt.get("unmeasured_shots") or 0),
        "estimated_shots": int(tt.get("estimated_shots") or 0),
    }


def current(src) -> dict | None:
    """The source's summary, if it was read from the run and play start the
    game has now. A reprocess or a new start time makes it stale."""
    t = src.thinking
    if not t or t.get("run_id") != src.current_run_id:
        return None
    if t.get("play_start_s") != src.play_start_s:
        return None
    return t


def league_of(src) -> tuple[str, str]:
    """(name, where the name came from). The playlist is the club's own name
    for the league; the title's tail is the same words in another order, and
    the hand label is the last resort because it is the one that drifts."""
    if src.playlist_title:
        return src.playlist_title, "playlist"
    named = league_from_title(src.title)
    if named:
        return named, "title"
    if src.league:
        return src.league, "label"
    return "Other", "none"


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _rows(src, t: dict, games_in_video: int):
    """One row per team: the game as that team played it."""
    ends = max(t["ends"], 1)
    for colour, other in (("red", "yellow"), ("yellow", "red")):
        yield {
            "source_id": src.id, "video_id": src.video_id, "title": src.title,
            "sheet": src.sheet, "played_at": _iso(src.played_at),
            "colour": colour, "team": getattr(src, f"team_{colour}"),
            "opponent": getattr(src, f"team_{other}"),
            "game_index": src.game_index, "games_in_video": games_in_video,
            "format": src.format or "fours",
            "ends": t["ends"], "thinking_s": t[colour], "per_end_s": t[colour] / ends,
        }


def _when(row):
    return row["played_at"] or ""


def build(sources, top: int = TOP) -> dict:
    """Group games by league and rank its teams, one row per team per game,
    by thinking per end.

    A league is also split by format: a doubles end is ten thrown rocks and
    a fours end sixteen, so their per-end times are not the same measure.
    """
    per_video = {}
    for s in sources:
        per_video[s.video_id] = per_video.get(s.video_id, 0) + 1

    groups, pending = {}, 0
    for s in sources:
        t = current(s)
        if t is None:
            pending += 1
            continue
        if t["measured_shots"] <= 0 or t["red"] + t["yellow"] <= 0:
            continue
        name, origin = league_of(s)
        rows = list(_rows(s, t, per_video[s.video_id]))
        fmt = rows[0]["format"]
        g = groups.setdefault((name, fmt), {"league": name, "format": fmt,
                                            "origins": set(), "games": 0, "rows": []})
        g["origins"].add(origin)
        g["games"] += 1
        g["rows"].extend(rows)

    leagues = []
    for g in groups.values():
        rows = sorted(g["rows"], key=lambda r: (r["per_end_s"], _when(r)), reverse=True)
        dates = [r["played_at"] for r in rows if r["played_at"]]
        leagues.append({
            "league": g["league"], "format": g["format"],
            # A playlist title and a stream-title league that read the same
            # land in one group; the playlist is the one that names it.
            "from": "playlist" if "playlist" in g["origins"] else sorted(g["origins"])[0],
            "games": g["games"],
            "first_played_at": min(dates) if dates else None,
            "last_played_at": max(dates) if dates else None,
            # One team's thinking per end it played, so it reads against the rows.
            "avg_per_end_s": sum(r["thinking_s"] for r in rows) / sum(max(r["ends"], 1) for r in rows),
            "teams": rows[:top],
        })
    leagues.sort(key=lambda lg: (lg["last_played_at"] or "", lg["league"]), reverse=True)
    return {"leagues": leagues, "pending": pending, "top": top}
