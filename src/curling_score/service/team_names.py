"""The names a league's teams have gone by, offered back when somebody names
the teams of another of its games.

Typed in by hand, game by game, so the same team turns up as "Dimmit" on one
night and "dimmit " on the next, and the thinking report counts them as two.
Offering the names already used is what keeps them one.

The league is the one /thinking groups by (thinking_report.league_of): the
club's playlist, which is a season of a league, so last season's teams -- who
may since have split up or taken new names -- are not offered.
"""

from curling_score.service.thinking_report import league_of


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def suggest(sources, game) -> dict:
    """{"league", "names"}: every team name used in `game`'s league, the most
    used first, then the most recently used, then by letter.

    Names that differ only in case or spacing are one name, said the way it is
    said most often. A game with no league gets none: everything unlabelled
    lands in "Other", whose teams have nothing to do with each other.
    """
    league, origin = league_of(game)
    if origin == "none":
        return {"league": None, "names": []}
    seen = {}
    for s in sources:
        if league_of(s)[0] != league:
            continue
        when = s.played_at or s.created_at
        for name in (s.team_red, s.team_yellow):
            said = " ".join((name or "").split())
            if not said:
                continue
            n = seen.setdefault(_key(said), {"uses": 0, "last": when, "said": {}})
            n["uses"] += 1
            n["last"] = max(n["last"], when)
            uses, last = n["said"].get(said, (0, when))
            n["said"][said] = (uses + 1, max(last, when))
    order = sorted(seen.items(), key=lambda kv: (-kv[1]["uses"], -kv[1]["last"].timestamp(),
                                                 kv[0]))
    # A spelling's (uses, last used), so an even split goes to the newer one.
    return {"league": league,
            "names": [max(n["said"], key=lambda s: (*n["said"][s], s)) for _, n in order]}
