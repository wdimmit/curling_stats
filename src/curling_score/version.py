"""What produced a timeline, so a reader can tell two analyses apart.

A processed game is only reusable if we know it was made by the same pipeline
and the same model. Two strings capture that: the pipeline version, bumped by
hand whenever a change alters the output, and a model id derived from the
weights file itself so an upgraded detector cannot masquerade as the old one.
"""

import hashlib
from pathlib import Path

# Bump when a code change alters what the timeline says -- new fields, changed
# rules, different acceptance thresholds. Not for refactors that leave the
# document byte-identical.
# 2026.09.16: the side-view detector became the default crossing proposer, a
# release is no longer required for a hog-to-hog split, and the far crossing
# may be reached for within a 0.05-unit cap -- with `long_split_far_
# reach_u` added to say when it was. Different rules and a new field, so
# timelines from before this are not comparable and must not be reused.
# 2026.09.22: the destination hog line is placed per panel from its paint
# rather than the global 4.441, the far crossing may be reached for up to 0.20
# panel units, and BASELINE_M is the 21.843 m a stone's leading edge covers.
# Also covers the stage-1 release changes (db182cf), which altered releases,
# splits and thinking times without bumping this. Split values move by up to
# ~0.57 s on some panels; timelines from before this must not be reused.
# 2026.09.23: the shot-list rules no longer bend to a detector's small
# differences. Stones moved while the house is cleared are dropped
# (`fit.drop_clearing`), a rest must hold three seconds (`REST_CONFIRM_S`), a
# one-sample track's gate is capped (`BOOTSTRAP_MAX_M`), and an arrival paired
# with its release wins a tie (`fit.PAIRED_BONUS`). Shipped with ds15a, whose
# own model id changes processing_version too. 8 of 108 ends change on ds11a
# alone. See datasets/ds15/README.md.
# 2026.09.24: every shot carries `line`, the thrown line against the
# broom (schema 6); the side views measure across the sheet from the painted
# centre line, which moves `target_broom.x` by up to 3 cm; a stone split
# across a column key at the hog row is timed, not lost.
# 2026.09.25: the last rock's house is read only until the players start
# clearing it (`rest.until_disturbed`), and a house diff pairs stones so the
# pairing as a whole moves them least, with only the thrown rock allowed to
# arrive (`shots.house_delta`). On the 16 harness games the final house agrees
# with the scoreboard in 40 of 54 ends instead of 29, and diffs that add a
# stone of the colour that did not throw fall from 97 to 17.
# 2026.09.28: an end the rules leave short searches the long camera facing the
# thrower for a rock the overhead saw at neither end, and settles what it finds
# like any unaccounted release (`sidereleases.lost_rocks`); the end record gains
# `lost_rocks_found`. Also covers the long-camera release fill-in (23f6a6a,
# `release_source`) and the board splits (bd916d7, 0a08d57), which changed
# timelines without bumping this.
# 2026.09.29: every measured line carries `delivery`, the rock from its rest
# to 1.5 m past the throwing hog line every 0.1 s (schema 8), and its start is
# picked from that one 10 fps read instead of a 5 fps read of its own.
# 2026.09.29.1: a rock that stopped dead and was moved off before
# REST_CONFIRM_S -- the last rock, pushed in the clearing -- rests where it
# stopped (`delivery._stopped_dead_index`), so its house is read before the
# clearing instead of after it. Last rocks' rests and houses move; shot lists
# do not.
# 2026.09.30: whether a scoreboard slot holds a card is `slotmodel`'s, a small
# net trained on 10,892 labelled slots, not two brightness thresholds that
# missed "1" cards and with them every end-1 score (09/28's board agreed with
# the detected score in 36 of 40 ends instead of 28); `find_board` passes over
# a neighbouring sheet's board at the frame's edge (sheets 3 and 4 since the
# re-aim); and the live session splits a game where the board was cleared
# across a changeover, as analyze does. Also covers 2026.09.29.1, which was
# never deployed on its own.
# 2026.09.30.1: a recording's first end that it joined late -- opening within
# `analyze.JOIN_S` of the recording's start, short of a full end -- numbers its
# missing rocks first, so the thrower, rock-of-player and hammer of every rock
# seen come out right (09/29 Super League sheet 2's 7 pm end 1: rocks 6-16, red
# hammer, where it read 1-11 and yellow); the end records `joined_late`.
# 2026.10.01: the path from behind the thrower refuses, once the rock is lost,
# a stone of the other colour misread as the rock's -- read as both in one
# frame where it had been seen sitting (`linetime._on`). s_1PbxeFSujkOVgtmLS
# e3 r11's path ran into a red guard a yellow pad had made read yellow; on 361
# harness rocks one path loses one point.
# 2026.10.01.1: a rock nothing saw arrive -- hogged, or settled from the house
# by its release -- carries that release, and its tee crossing is read from
# the throw, not 16 s before it as if its t_enter were an arrival. Its broom,
# hog crossing, line and thinking time were all read at the wrong moment
# (09/29 Super League sheet 3 e2 r13, flagged: 103 of 7,401 hosted rocks).
# 2026.10.02: a shot's `t_video_s`, and so its `youtube_url`, is the lead-in
# before its release, or before its arrival less `RELEASE_TO_ARRIVAL_S` (15 s)
# when no release was seen, rather than the lead-in before its arrival; and
# that lead-in is 5 s, not 10.
# 2026.10.02.1: two ends of one house in a row, the first shorter than a whole
# end (`segment.WHOLE_END_S`), are one end: a takeout emptied the house and a
# stray stone in the other panel meanwhile cut it in two (10/01 Thursday
# Morning sheet 2 end 4; Mens sheet 5, flagged, three times in one game).
# Of 30 cached videos' profiles, only that sheet 2 game changes.
# 2026.10.02.2: a recording's board, read once per game, walks back to the
# end of the first end when the game's last five reads all fail, instead of
# giving the game no scores (`sb.read_game_board`'s ``back_to_s``). Of 22
# cached games with no board score, 9 gain a mid-game read this way (6-12
# reads), agreeing with the stones as often as published board scores do.
# 2026.10.02.3: the long camera times a big-weight hit's hog crossing: its
# speed bound tops out at 5.0 m/s, not 3.2, and its window opens 1.0 s after
# the release, not 2.0 (`longview`). 10/01 Mens sheet 2's flagged rock 16 was
# refused at 3.69 m/s and now splits 5.53 s. Of 35 hosted throws released at
# 3 m/s or more, 26 crossings were found before and 35 now; of 438 rocks in
# three games, 431 read the same, 7 gained one, none was lost or moved.
# 2026.10.02.4: a game's last end is not an end when it kept two rocks or
# fewer, or saw no release and kept half an end or less (`timeline.
# nothing_thrown`): 10/01 doubles sheet 5's eighth end, flagged twice, was a
# clean-up that offered eleven candidates. Of every hosted game, three change:
# that one and the 09/29 Supper games on sheets 4 and 5.
# 2026.10.03.1: two ends of one house in a row are one end, however long the
# first ran, when the house held stones in every keyframe between them
# (`segment._held`): 10/02 Friday sheet 5's end 6 was cut in two by a skip's
# red shoes at the far end, read as three stones. Of 32 cached profiles, only
# that one changes.
# 2026.10.03.2: a target broom is held still within 0.15 m across the sheet
# and 0.45 m along it, not a 0.15 m circle (`broomtime.CLUSTER_ALONG_M`): the
# long camera reads a held pad's depth with up to half a metre of wobble, and
# the circle split pads seen in every frame into pieces under half the window.
# Over 2,091 rocks in 23 cached games, 8 gained a broom, none lost or moved one.
# 2026.10.04.1: a throw the far overhead could not place is followed by the
# long camera facing that house (`game.farfollow`): one it saw stop in play is
# a rest there (release-rest), one the overhead saw enter within 19 s of its
# release or the camera saw run through ran through (release-through), and a
# late arrival that ran on is the hog pushed down the sheet. Of the 39 rocks
# the user reviewed on video, the decision matches 38; the last hit a guard out
# and now rests in play.
# 2026.10.06.1: green a row or two tall, with ice either side, is not a band of
# the ring (`sideview._without_slivers`): on 10/04 doubles sheet 1's left view a
# bright edge where the backboard meets the ice, 30 rows above the ring, was
# taken for the 12-ft ring's far edge, the tee came out 19 px high, and the view
# had no lateral fit -- no brooms or lines in that house all game. Of the cached
# videos' side views, only that one changes.
# 2026.10.06.2: two ends in a row played into one house are a gap the board
# decides, however short (`boardsplit.board_decides`), and a later game's first
# end in which nothing was thrown is left off before its next end is built, by
# analyze and the live session alike: 10/04 doubles sheet 4 parked its stones in
# the house it had just played to, 70 s after game 1's last end, and the board
# was cleared across them, but no gap was changeover-sized -- one thirteen-end
# game, with game 2's board on game 1's ends. Of 126 games, 09/27-10/05, three
# have such a pair; the other two were already decided or are a game's tail.
PIPELINE_VERSION = "2026.10.06.2"


def model_id(weights) -> str:
    """A short, stable name for a weights file: its stem and a content hash.

    Two files with the same name but different training runs must not be
    confused, and a renamed copy of the same weights must not look new.
    """
    if weights is None:
        return "classical"
    path = Path(weights)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"{path.stem}-{h.hexdigest()[:8]}"


def processing_version(weights, side_weights=None, broom_weights=None, line=False) -> str:
    """The full identity of one analysis configuration.

    ``side_weights`` is folded in because the side-view detector changes the
    timeline as surely as the overhead one does -- it is what times every
    throwing-end hog crossing, and so every split. Leaving it out would let a
    timeline made with the colour scan be reused for one made with a trained
    detector, which is exactly the quiet mixing this string exists to prevent.

    Omitted entirely when there is no side model, so every timeline produced
    before there was one keeps the identity it was published with.

    ``broom_weights`` likewise: the broom model adds a field to every shot, so
    it is named when present and omitted when not, which keeps every timeline
    made without one at the identity it was published with.

    ``line`` is named only when the line pass ran AND a side model was named:
    without a side model there is no hog-line split to measure the line from,
    so the suffix would say nothing true. The line is measured per shot, so
    when it did run it changes the timeline, and is named to say so.
    """
    base = f"{PIPELINE_VERSION}+{model_id(weights)}"
    out = base if side_weights is None else f"{base}+side-{model_id(side_weights)}"
    out = out if broom_weights is None else f"{out}+broom-{model_id(broom_weights)}"
    return f"{out}+line" if line and side_weights is not None else out
