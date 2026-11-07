"""A club whose streams are one game each (DFW): no second game, and nothing past the last end."""

from curling_score import analyze, clubs, timeline
from curling_score.game import format as format_mod
from curling_score.game.segment import EndSegment, GameSegment, one_game


def seg(index, ends, closed=True):
    return GameSegment(index=index, start_s=ends[0][0], end_s=ends[-1][1], closed=closed,
                       ends=[EndSegment(number=i + 1, house="top" if i % 2 else "bottom",
                                        start_s=a, end_s=b) for i, (a, b) in enumerate(ends)])


def test_one_game_joins_segments():
    games = [seg(0, [(0, 100), (100, 200)]), seg(1, [(400, 500)], closed=False)]
    (g,) = one_game(games)
    assert g.index == 0 and g.start_s == 0 and g.end_s == 500 and g.closed is False
    assert [e.number for e in g.ends] == [1, 2, 3]
    assert [e.start_s for e in g.ends] == [0, 100, 400]
    assert one_game([]) == []


def end(n, red, yellow, board=False):
    return {"number": n, "start_s": 100.0 * n, "end_s": 100.0 * n + 90,
            "detected_score": {"red": red, "yellow": yellow},
            "score": {"red": red, "yellow": yellow} if board else None}


def test_ends_past_eight_are_dropped():
    # 5-3 after eight; a ninth "end" is practice slides after the game
    ends = [end(i, 1 if i <= 5 else 0, 1 if i > 5 else 0) for i in range(1, 9)] + [end(9, 0, 1)]
    kept = timeline.past_scheduled(ends, 8)
    assert [e["number"] for e in kept] == list(range(1, 9))


def test_extra_end_kept_when_tied():
    ends = [end(i, 1 if i <= 4 else 0, 1 if i > 4 else 0) for i in range(1, 9)]
    ends += [end(9, 1, 0), end(10, 0, 0)]
    kept = timeline.past_scheduled(ends, 8)
    assert [e["number"] for e in kept] == list(range(1, 10))


def test_the_boards_score_decides_the_tie():
    # detected says 5-3, the board says 4-4: the board's word stands
    ends = [end(i, 1 if i <= 5 else 0, 1 if i > 5 else 0) for i in range(1, 9)]
    ends[4]["score"] = {"red": 0, "yellow": 1}
    for e in ends:
        if e["score"] is None:
            e["score"] = dict(e["detected_score"])
    ends.append(end(9, 0, 1))
    assert len(timeline.past_scheduled(ends, 8)) == 9


def test_short_games_are_untouched():
    ends = [end(i, 1, 0) for i in range(1, 7)]
    assert timeline.past_scheduled(ends, 8) == ends


def test_finish_game_drops_them(monkeypatch):
    ends = [end(i, 1 if i <= 5 else 0, 1 if i > 5 else 0) for i in range(1, 9)] + [end(9, 0, 1)]
    for e in ends:
        e.update(shots=[], house="top", hammer=None)
    monkeypatch.setattr(timeline, "build_game",
                        lambda index, s, e, out_ends, **kw: {"ends": out_ends, "end_s": e,
                                                             "detected": {"final": None}})
    out = analyze.finish_game(seg(0, [(e["start_s"], e["end_s"]) for e in ends]), ends, None,
                              format_mod.FOURS, scheduled_ends=8)
    assert len(out["ends"]) == 8 and out["end_s"] == ends[7]["end_s"]


def test_dallas_is_one_game_of_eight_and_granite_is_not():
    assert clubs.DALLAS.one_game_per_stream and clubs.DALLAS.scheduled_ends == 8
    assert not clubs.GRANITE.one_game_per_stream and clubs.GRANITE.scheduled_ends is None
