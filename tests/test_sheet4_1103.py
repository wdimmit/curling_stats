"""11/03 Tuesday Super League sheet 4 (T2jGXKs2s9Q): the 7 pm and 9 pm games,
charted live as one thirteen-end game.

The 7 pm game's last rock rested at 7692 s and its house was cleared by 7720;
the 9 pm game's first rock entered at 8037. In between, a tan broom head held
into the top panel's corner behind the back line read as a yellow stone at 7920
and 7925 s (conf 0.61, 0.44). Counted, it joined end 8 -- end_s 7935, the
published value -- and the empty sheet either side of it came out 130 s and
115 s, neither long enough to end the game nor to have the board asked.

The fixture is the kept recording's keyframe counts over 6600-9000 s by
overhead-3, of every stone on the sheet and of those in play (the back line).
"""

import json
from pathlib import Path

import pytest

from curling_score.game import segment

DOC = json.loads((Path(__file__).parent / "fixtures" / "profiles"
                  / "T2jGXKs2s9Q.json").read_text())


def _samples(key):
    return [segment.Sample(t=t, top_stones=top, bottom_stones=bot,
                           top_playable=bool(tp), bottom_playable=bool(bp))
            for t, top, bot, tp, bp in DOC[key]]


def _games(key):
    return [[(e.house, e.start_s, e.end_s) for e in g.ends]
            for g in segment.segment_games(_samples(key))]


def test_the_broom_head_at_7920_is_wholly_past_the_back_line():
    top = [(e["t"], e["x_px"], e["y_px"]) for e in DOC["excluded"] if e["panel"] == "top"]
    assert (7920.0, 24.0, 6.8) in top


def test_at_7925_it_touches_the_paint_and_still_counts():
    """Its box reaches about a pixel past the paint's outer edge: the back
    line alone does not part the games."""
    assert not any(e["t"] == 7925.0 for e in DOC["excluded"])
    row = next(r for r in DOC["samples"] if r[0] == 7925.0)
    assert row[1] == 1


@pytest.mark.parametrize("key", ["samples", "samples_on_sheet"])
def test_the_night_is_two_games(key):
    """On either count: the stray stone no longer breaks up the empty sheet."""
    games = _games(key)
    assert len(games) == 2
    last_7pm = games[0][-1]
    first_9pm = games[1][0]
    assert last_7pm[0] == "top" and last_7pm[2] < 7800.0
    assert first_9pm[0] == "bottom" and 8030.0 <= first_9pm[1] <= 8060.0


def test_without_the_stray_rule_the_back_line_alone_keeps_them_one(monkeypatch):
    monkeypatch.setattr(segment, "STRAY_KEYFRAMES", 0)
    # The window opens mid end 7, too little of it to be an end: end 8, run on
    # to the stray at 7925, then the 9 pm game's first end, in one game.
    (game,) = _games("samples")
    assert [h for h, _, _ in game] == ["top", "bottom"]
    assert game[0][2] >= 7925.0
