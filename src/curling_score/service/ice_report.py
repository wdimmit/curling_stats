"""The ice maker's report: each league's draws, so the page can read how much
the ice curled in one and set it against another.

A draw is the games of one league that started together -- the 7 pm and the
9 pm games of a Super League night are two draws, though each sheet's
recording holds both. Nothing in a stream says when its games began, so a
game's start is the stream's own start plus where the game begins in it, and
the games of a league fall into draws wherever one starts well after the
draw before it began.

The curl itself is read in the browser, from each game's review timeline,
with the viewer's own contact-aware curl (frontend/core/ice.mjs): it exists
nowhere in Python, and the list here costs one read of the sources.
"""

from datetime import timedelta

from curling_score.service.thinking_report import league_of

# A league game runs about two hours; the games of one draw start within a
# few minutes of each other, the next draw two hours on.
DRAW_GAP = timedelta(minutes=90)


def start_of(src):
    """When the game started: the stream's start plus the game's place in it,
    from the warm-up's end when somebody has said where that is. None for a
    game with no date."""
    if src.played_at is None:
        return None
    at = src.play_start_s if src.play_start_s is not None else src.game_start_s
    return src.played_at + timedelta(seconds=float(at or 0.0))


def _game(src) -> dict:
    return {"source_id": src.id, "sheet": src.sheet,
            "team_red": src.team_red, "team_yellow": src.team_yellow}


def draws(sources) -> dict:
    """Every league's draws, newest first, each with its games by sheet.

    A league is split by format as the thinking report splits it: a doubles
    draw and a fours draw are not the same ice test. Games with no date are
    left out -- there is no telling which draw they were."""
    groups = {}
    for s in sources:
        at = start_of(s)
        if at is None:
            continue
        key = (league_of(s)[0], s.format or "fours")
        groups.setdefault(key, []).append((at, s))

    leagues = []
    for (name, fmt), games in groups.items():
        games.sort(key=lambda g: (g[0], g[1].sheet or 0))
        out = []
        for at, s in games:
            if not out or at - out[-1]["_first"] > DRAW_GAP:
                out.append({"_first": at, "games": []})
            out[-1]["games"].append(_game(s))
        for d in out:
            d["start"] = d.pop("_first").isoformat()
            d["games"].sort(key=lambda g: (g["sheet"] is None, g["sheet"] or 0))
        out.reverse()
        leagues.append({"league": name, "format": fmt, "draws": out})
    leagues.sort(key=lambda lg: (lg["draws"][0]["start"], lg["league"]), reverse=True)
    return {"leagues": leagues}
