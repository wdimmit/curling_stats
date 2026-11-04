"""The thinking-time report: in each league, the teams that took longest --
and the quickest -- to decide their shots, per end played.

It works from the summary each Source carries, never from a timeline, so the
page costs one list of sources however many games there are. The summary is
the game's `thinking_time` as the review link serves it -- after the warm-up
is trimmed -- and every figure in it is a lower bound: the first rock of each
end is never timed, and a throw the camera did not see is timed from an
estimate or not at all.
"""

from datetime import timedelta

from curling_score.ingest.source import league_from_title

TOP = 20
# A league is listed while it is being played: one with no game this long is
# a past season, however slow its teams were.
RECENT = timedelta(days=14)


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


def team_key(name: str) -> str:
    """A typed team name as one team: "Dimmit" and "dimmit " are the same."""
    return " ".join(name.split()).casefold()


def _by_team(rows) -> tuple[list[dict], int]:
    """Every named team in the league, slowest first: its thinking over all
    the ends it played, so a long game counts for more than a short one.
    Each spelling a team went by is one team, called the way it was written
    most often, the newer on a tie. A row with no name is no team anyone can
    find, so it is left out and counted."""
    teams, unnamed = {}, 0
    for r in rows:
        said = " ".join((r["team"] or "").split())
        if not said:
            unnamed += 1
            continue
        t = teams.setdefault(team_key(said), {"games": 0, "thinking_s": 0.0, "ends": 0,
                                              "said": {}})
        t["games"] += 1
        t["thinking_s"] += r["thinking_s"]
        t["ends"] += max(r["ends"], 1)
        uses, last = t["said"].get(said, (0, ""))
        t["said"][said] = (uses + 1, max(last, _when(r)))
    out = [{"team": max(t["said"], key=lambda s: (*t["said"][s], s)),
            "games": t["games"], "ends": t["ends"], "thinking_s": t["thinking_s"],
            "per_end_s": t["thinking_s"] / t["ends"]} for t in teams.values()]
    out.sort(key=lambda t: (-t["per_end_s"], t["team"].casefold()))
    return out, unnamed


def build(sources, top: int = TOP, since=None) -> dict:
    """Group games by league and rank its teams, one row per team per game,
    by thinking per end -- the slowest `top` rows as `teams`, and the
    quickest `top` as `fastest`, both ranked over every game in the league,
    and every named team's average over all its games as `by_team`.

    A league is also split by format: a doubles end is ten thrown rocks and
    a fours end sixteen, so their per-end times are not the same measure.

    With `since`, only leagues with a game on or after it are listed, ranked
    over all their games. A game with no date counts from when it was charted.
    """
    per_video = {}
    for s in sources:
        per_video[s.video_id] = per_video.get(s.video_id, 0) + 1

    groups, pending = {}, []
    for s in sources:
        t = current(s)
        if t is None:
            pending.append(((league_of(s)[0], s.format or "fours"), s.played_at or s.created_at))
            continue
        if t["measured_shots"] <= 0 or t["red"] + t["yellow"] <= 0:
            continue
        name, origin = league_of(s)
        rows = list(_rows(s, t, per_video[s.video_id]))
        fmt = rows[0]["format"]
        g = groups.setdefault((name, fmt), {"league": name, "format": fmt,
                                            "origins": set(), "games": 0, "rows": [],
                                            "latest": None})
        g["origins"].add(origin)
        g["games"] += 1
        g["rows"].extend(rows)
        when = s.played_at or s.created_at
        if g["latest"] is None or when > g["latest"]:
            g["latest"] = when

    if since is not None:
        groups = {k: g for k, g in groups.items() if g["latest"] >= since}
        # Still to come: a game in a league on the page, or a recent one that
        # may put its league there.
        pending = [p for p in pending if p[0] in groups or p[1] >= since]

    leagues = []
    for g in groups.values():
        slowest = sorted(g["rows"], key=lambda r: (r["per_end_s"], _when(r)), reverse=True)
        fastest = sorted(g["rows"], key=lambda r: (r["per_end_s"], _when(r)))
        dates = [r["played_at"] for r in slowest if r["played_at"]]
        by_team, unnamed = _by_team(g["rows"])
        leagues.append({
            "league": g["league"], "format": g["format"],
            # A playlist title and a stream-title league that read the same
            # land in one group; the playlist is the one that names it.
            "from": "playlist" if "playlist" in g["origins"] else sorted(g["origins"])[0],
            "games": g["games"],
            "first_played_at": min(dates) if dates else None,
            "last_played_at": max(dates) if dates else None,
            # One team's thinking per end it played, so it reads against the rows.
            "avg_per_end_s": sum(r["thinking_s"] for r in slowest) / sum(max(r["ends"], 1) for r in slowest),
            "teams": slowest[:top],
            "fastest": fastest[:top],
            "by_team": by_team, "unnamed_rows": unnamed,
        })
    leagues.sort(key=lambda lg: (lg["last_played_at"] or "", lg["league"]), reverse=True)
    return {"leagues": leagues, "pending": len(pending), "top": top}
