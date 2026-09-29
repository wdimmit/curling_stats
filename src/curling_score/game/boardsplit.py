"""Two games the empty-sheet rule ran together, told apart by the wall scoreboard.

``segment.segment_games`` ends a game when both houses sit empty for
``GAME_GAP_S``. A changeover with stones still in view -- being reset, or
walked through -- never reads empty that long, and the next game's ends are
appended to the last one: doubles sheet 4 on 2026-09-27 came out as one
twelve-end game, where the sheet played two of six.

Two cues settle it, and a split needs both:

* **A changeover-sized gap between ends.** Within a game one end's segment
  runs into the next -- 5-10 s apart on every game of 2026-09-27 -- because the
  house stays active until the other house's stones arrive. The merged
  changeover left 400 s.
* **The board cleared.** The club clears the wall scoreboard promptly after a
  game, and a game is at least ``MIN_ENDS_BEFORE`` ends in. Blank after showing cards, for two readable samples in a row, is a
  game that has finished. Blank before any card is only a game that has not
  posted yet, and one blank read between two readings of cards is a misread
  (7pm sheet 3 read one mid-game). Measured on six videos, 2026-09-28: the
  runs mark every changeover and nothing else.

The same two cues work the other way round (:func:`join_games`). The
empty-sheet rule also fires on a long pause in the middle of a game: Monday
sheet 5 on 2026-09-28 sat empty for 310 s after the late game's second end,
and came out as a two-end game and a five-end one. The board said otherwise
-- the five-end game's board scores were ends 1-5 of the whole game -- because
nobody clears the board in the middle of a game. So a pause no longer than a
pause is one game when the board shows cards before it, still shows them
after, and never went blank in between.

The board is read only around a gap that qualifies, which most games never
have. Nothing here adds or removes an end; it only says which game each is in.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace

from curling_score.game.segment import GameSegment

CHANGEOVER_MIN_GAP_S = 120.0
BLANK_RUN = 2
# A board is cleared after a game that was played, and no game is over in
# fewer ends than this. Early on, with one or two cards up, the board flickers
# between cards and blank: jgZ9wlxGYHM read blank runs after end 2 of a real
# game, at a 2-minute gap.
MIN_ENDS_BEFORE = 4
# The board around a gap: from before the last end finished, for the last
# score it posted, to well into the next game, in case it was cleared late.
LOOK_BEFORE_S = 300.0
LOOK_AFTER_S = 600.0
# Cleared as the last end was being counted is still after it.
CLEAR_LEAD_S = 60.0
# The board changes once an end; a reading every half-minute, each the median
# of the keyframes either side of it to remove anyone walking past.
STEP_S = 30.0
HALF_S = 20.0
MIN_FRAMES = 3
# No gap between two games longer than this is a pause in one. The changeovers
# of 2026-09-27 and -28 were 400-480 s (doubles) and 520-1955 s (fours) apart;
# the longest pause the empty-sheet rule split was 310 s. Up to here, the board
# decides.
JOIN_MAX_GAP_S = 600.0


def board_cleared(states, t_last_end_s: float) -> bool:
    """Whether ``states`` -- (t, "cards" | "blank" | None), in time order --
    show the board cleared after the game whose last end finished at
    ``t_last_end_s``: blank for ``BLANK_RUN`` readable samples in a row, after
    showing cards, the first of them no earlier than the end less
    ``CLEAR_LEAD_S``. Unreadable samples neither count nor break a run."""
    seen_cards, run, run_start = False, 0, None
    for t, state in states:
        if state is None:
            continue
        if state == "cards":
            seen_cards, run = True, 0
            continue
        if not seen_cards:
            continue
        if run == 0:
            run_start = t
        run += 1
        if run >= BLANK_RUN and run_start >= t_last_end_s - CLEAR_LEAD_S:
            return True
    return False


def board_kept(states, t_last_end_s: float, t_next_start_s: float) -> bool:
    """Whether ``states`` show the board still up across a gap: cards before
    the next end began, cards for ``BLANK_RUN`` readable samples after it, and
    never cleared (:func:`board_cleared`) after the last end. A board that was
    not read, or showed no card yet, says nothing, and nothing is joined."""
    before = any(s == "cards" for t, s in states if t <= t_next_start_s)
    after = sum(1 for t, s in states if t >= t_next_start_s and s == "cards")
    return before and after >= BLANK_RUN and not board_cleared(states, t_last_end_s)


def join_games(games, read_board) -> list[GameSegment]:
    """The games, with each pair the empty-sheet rule parted across a pause
    rejoined where the board stayed up through it; renumbered, the input left
    as it was. ``read_board`` is as for :func:`split_games`."""
    out: list[GameSegment] = []
    for g in games:
        prev = out[-1] if out else None
        if (prev is not None and prev.ends and g.ends
                and g.start_s - prev.end_s <= JOIN_MAX_GAP_S
                and board_kept(read_board(prev.end_s - LOOK_BEFORE_S,
                                          g.start_s + LOOK_AFTER_S),
                               prev.end_s, g.start_s)):
            ends = prev.ends + [replace(e, number=len(prev.ends) + i)
                                for i, e in enumerate(g.ends, start=1)]
            out[-1] = GameSegment(index=prev.index, start_s=prev.start_s,
                                  end_s=g.end_s, ends=ends, closed=g.closed)
            continue
        out.append(GameSegment(index=len(out), start_s=g.start_s, end_s=g.end_s,
                               ends=list(g.ends), closed=g.closed))
    return out


def split_games(games, read_board) -> list[GameSegment]:
    """The games, split wherever a changeover-sized gap between two ends has
    the board cleared across it; renumbered, the input left as it was.

    ``read_board(t0, t1)`` gives the board's states over a window, as
    :func:`board_states` does.
    """
    out: list[GameSegment] = []
    for g in games:
        pieces = [[g.ends[0]]] if g.ends else []
        for a, b in zip(g.ends, g.ends[1:]):
            if (len(pieces[-1]) >= MIN_ENDS_BEFORE
                    and b.start_s - a.end_s >= CHANGEOVER_MIN_GAP_S
                    and board_cleared(read_board(a.end_s - LOOK_BEFORE_S,
                                                 b.start_s + LOOK_AFTER_S), a.end_s)):
                pieces.append([b])
            else:
                pieces[-1].append(b)
        for k, piece in enumerate(pieces):
            ends = [replace(e, number=i) for i, e in enumerate(piece, start=1)]
            out.append(GameSegment(index=len(out), start_s=ends[0].start_s,
                                   end_s=ends[-1].end_s, ends=ends,
                                   closed=g.closed if k == len(pieces) - 1 else True))
    return out


def board_states(video, t0: float, t1: float, *, step_s: float = STEP_S,
                 half_s: float = HALF_S) -> list:
    """The wall scoreboard every ``step_s`` over ``t0..t1``: (t, "cards",
    "blank" or None when it cannot be read), each from the median of the
    keyframes within ``half_s``."""
    from curling_score.game import scoreboard as SB
    from curling_score.ingest import frames as F

    def read(t, buf):
        imgs = [img for ft, img in buf if t - half_s <= ft <= t + half_s]
        if len(imgs) < MIN_FRAMES:
            return (t, None)
        image = SB.median_frame(imgs)
        geom = SB.find_board(image)
        if geom is None or not SB.is_readable(image, geom):
            return (t, None)
        return (t, "blank" if SB.read_slots(image, geom).is_blank() else "cards")

    out, buf, t = [], deque(), t0
    for ft, img in F.keyframe_sweep(video, start_s=max(0.0, t0 - half_s), end_s=t1 + half_s):
        buf.append((ft, img))
        while t <= t1 and ft > t + half_s:
            out.append(read(t, buf))
            t += step_s
        while buf and buf[0][0] < t - half_s:
            buf.popleft()
    while t <= t1:
        out.append(read(t, buf))
        t += step_s
    return out
