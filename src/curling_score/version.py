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
# 2026.10.06.3: a finished game with no board card and under 85% of its ends'
# rocks kept is marked practice (`timeline.is_practice`), and its summary says
# so; the service leaves it out of the catalogue. Of 127 games it marks the
# four practice sessions of the week to 10/06 and six one- or two-end scraps,
# and no real game.
# 2026.10.08.1: the side model's crossing needs the two samples either side of
# the hog row within 0.5 s of each other (`sidemodel.STRADDLE_GAP_MAX_S`): 10/06
# Super League sheet 1 e7 r15 lost its split because one stray red box past the
# line, 2.6 s after the stone left that column key, made the key a second
# "crosser". On the 11-video harness 19 such refusals become crossings, each
# checked on its frame, and no crossing already timed moves. The colour scan
# keeps no bound.
# 2026.10.08.2: a panel's speed at a hog line comes from two samples either
# side of it within 0.5 s (`split.SPEED_SPAN_MAX_S`), or there is none and
# `long_split` bounds the mean by the side view's speed instead: 10/06 Super
# League sheet 1 e7 r16 and e8 r12 lost their splits to near-line speeds read
# across 3.0 and 3.4 s gaps in the release track. On the 11-video harness 134
# such speeds drop, one split is gained and none lost.
# 2026.10.08.3: a panel's speed at a hog line is read over at least 0.2 s
# (`split.SPEED_SPAN_MIN_S`): a pair straddling the paint 0.1 s apart takes in
# a neighbour each side, so one box drawn long no longer doubles the rate and
# trips the speed tolerance. 5 fps pairs already spanned 0.2 s; arrivals have
# always been 10 fps.
# 2026.10.08.4: the throwing panel is read at 10 fps, not 5
# (`release.RELEASE_FPS`); a release track must still span 0.4 s
# (`MIN_SPAN_S`), and is cut at any gap over 1.0 s (`GAP_SPLIT_S`), each piece
# a candidate. On the 11-video harness: releases 1654 -> 1694 (+46/-6), splits
# 1659 -> 1663, tee crossings estimated 43 -> 17; a one-frame phase shift now
# moves 13 releases rather than 37, and 2 release times by more than 0.2 s
# rather than 20.
# 2026.10.10.1: an arrival paired with nothing, of a still-hogged release's
# colour, that came in 19-45 s after it and changed nothing is the hog being
# pushed down the sheet, and is dropped (`farfollow.PUSH_MAX_LAG_S`). 10/09
# Friday Evening S4 end 2 rock 12 was pushed in 32.7 s on, past
# `release.pair`'s 30 s, and the rules kept the push -- no release, no line --
# over the hog. Full-game A/B on the worker against 2026.10.08.4: that rock
# becomes the hog with its line and delivery, and 428 rocks in four games
# (41 ends, 10 hogged releases) are unchanged.
# 2026.10.11.1: every rock's spin, rpm at release, from the camera facing the
# thrower (`game/spintime.py`, model weights/spin1.pt); timeline schema 9.
# Nothing else moves: the pass is attach-only.
# 2026.10.11.2: a rock showing under a turn in view is timed with its handle's
# swing held (`spinfit.read_with_swing`: the end's own swing from its clean
# reads, else the colour's usual), as a range under half a turn; one still in
# view when its window ends is followed on 5 s (`spintime.follow_on`). Rocks
# with a turn or more in view read as before. Six replay windows: 16 of 22
# refused rocks were slow ones, 3.4-8.4 rpm.
# 2026.10.12.1: the delivery chart's path read at 30 fps, every frame, not 10
# (`linetime.DELIVERY_FPS`): ~160 dots a rock against ~60, the slide's first
# metre now every frame (`_collapse_rest` judges a stone still on a tenth of a
# second's grid, as before), the follow-back's speed over the same half
# second. A line's start and its offset at the hog line move ~1 mm on a few
# rocks an end; nothing else.
# 2026.10.12.2: a delivery path no longer steps where the read hands it to the
# band (`linetime._seam_offset`): the read runs 0.4 s past the band's first
# sample, the two are compared in the frames both saw, and the read's last
# half second is eased onto the band. The rock at rest and line.start are not
# moved; nothing outside line.delivery changes.
# 2026.10.12.3: the overhead tracker hands each frame's sightings to the
# tracks that expect them nearest, not oldest track first: a stone at rest,
# hidden as a shot slid past within its gate, no longer takes the shot's
# track -- the shot's rest, its house and its house_delta were the other
# stone's (s_0WTrSBTjUazkTDnZB e2 r16), and a shooter is followed past the
# stone it struck. Shot lists are unchanged on the 16 harness games.
# 2026.10.15.1: a hog farfollow took back from the arrival it was matched to
# (that arrival was the hog pushed down the sheet) keeps the match's standing
# in the fit (`farfollow.taken_back`, `fit.paired_ids`): as a bare hog it lost
# its slot to anything seen arriving, and Wednesday Womens 10/14 S2 game 2's
# rock 1 was a broom head carried past the sheet three minutes before play.
# 2026.10.16.1: the recording's first game drops an empty first end too, as
# later games already did, unless the recording joined it at its start
# (`analyze.leaves_off_head`): Wednesday Womens 10/14 S4's people in a house
# before the game were end 1, and every end after it was one too high.
# 2026.10.16.2: the activity profile's stones are counted by the detector the
# ends use, not the colour thresholds (`profile.count_frame`), which also count
# people: 10/15 Thursday Mens sheet 1 went out live as one fourteen-end game,
# and its game 2 end 2 lost its last five rocks. Over 132 cached videos 23
# segment differently, mostly scraps the thresholds made out of people; 13 full
# analyses: 2081 -> 2088 rocks, ends agreeing with the board 61/78 -> 67/81,
# three pre-game "ends" gone (_OGo), a missed end found (ontE game 2). One
# worse: last season's 5U Nationals sheet 5 (hOKZ), whose board cannot be read,
# splits 4 + 4 at its 315 s mid-game break.
# 2026.10.16.3: a recording that opens on an end played before it -- that
# end's rocks at rest in one house, brought back, and the first end shown
# beginning in the other house within a changeover of their clearing
# (`segment._played_before`) -- numbers its first game from end 2, so the
# board's scores land on the ends they were posted for. 10/15 Thursday Mens
# sheets 1-4, whose streams came up after end 1: board agreement 3/5, 1/7,
# 1/5, 2/7 -> 4/4, 4/6, 4/4, 5/6. Of 132 cached recordings, only those four.
# 2026.10.17.1: a game conceded part-way through its last end -- the team
# behind going into it had fewer rocks to come than points it needed to tie --
# lists the rocks after its last one seen as not thrown (shot_type
# not_thrown, source conceded), not missing (`timeline.concede`); blanks the
# filler put in a long pause give way so the rocks seen run on, and each blank
# now says what put it there (`shots.Unseen`). Recorded 120 s past the end, so
# a feed that died mid-end is not a concession. 10/15 Thursday Mens S2's 7 pm
# game: conceded after rock 13. Of 226 hosted charts, about six would read so.
# 2026.10.20.1: the end's last throw, placed by the house SETTLE_S on
# (release-add / release-remove) because its arrival was refused, rests
# where the far overhead saw it stop, or runs through, when the overhead saw
# it come in on time (`farfollow.HOUSE_SETTLED`): that house read falls in the
# clearing -- Tuesday Super 10/13 S1 e1 r16 (f_1XMj) was placed on a stone
# being pushed to the far side of the house. 23 of this season's 73 house
# placements move; 12 checked on video, all better. Shot lists unchanged on
# the harness (5 last throws moved in 111 ends).
# 2026.10.20.2: a stop the far overhead saw come under 0.5 m is the throw only
# if the long camera saw it stop too (`farfollow.ENTRY_REST_MIN_RUN_M`):
# players' feet at the far edge of its view read as a rock that came in and
# stopped -- KTDO 10/17 D1 S1 e4 r3 (f_1A7j) was a hog. Of this season's 16
# such rests, 6 change, 5 to hogs seen on video. And a long-camera path start
# where the rock comes into view must run 2 m down the sheet
# (`linetime.PATH_MIN_RUN_M`): rock 9 (f_0k34) followed a broom head. Of 437
# re-read paths, 56 change, 45 end nearer their rest, none further.
# 2026.10.21.1: three more from the 2026.10.20.2 reprocesses. A track that
# stops after one or two sightings in flight on a stone of its colour already
# parked there is that stone seen again (`delivery.BORROWED_REST_FLIGHT`) --
# KTDO 10/17 D1 S1 e4 r3 (f_1A7j), a hog. A long-camera path start where the
# rock comes into view must come 0.5 m down the sheet in its first second
# (`linetime._comes_on`) -- e4 r9 (f_0k34), a broom head carried across gaps.
# With all of an end's releases seen, nothing unreleased before the first or
# after the last was thrown (`fit.drop_clearing`) -- Monday Open 10/19 S1 e4
# (the clearing taken for rock 16, hammer flipped) and Wednesday Womens 10/07
# S5 e4 r16. A/B on 14 games: only those changes.
# 2026.10.22.1: a stone beyond a panel's painted sideline is on the next sheet,
# and is neither counted in the activity profile nor tracked
# (`geometry/sidepaint.py`). 10/19 Monday S4 live: a rock at rest on sheet 3
# helped hold the changeover under GAME_GAP_S, and two games were charted as
# one of 14 ends; a live replay with this splits them 7 + 6. Lines found on
# 801 of 808 panel sides of 202 cached recordings, none reaching the ice. A/B
# on 10 videos: no game or end count moves; rocks identical but for two
# arrival times, and BiG6 10/14 g2 e6 loses a phantom 17th delivery.
# 2026.10.22.2: a rock's line carries `release`, the point on its delivery path
# where the thrower's hand let go of the handle, when the hand model
# (weights/release1.pt, `game/releasetime.py`) sees it come off. Read from a
# strip of the lane kept from the frames the hog and delivery reads already
# decode, with no decode of its own. Nothing else moves: the pass is
# attach-only. A/B on 10/20 Tuesday S2's recording (dmljQzQr1xo, both games,
# 256 rocks), 7b7219fc against this, on the laptop's 3070: 251 rocks gain a
# release, and the rest is the known `_smooth` tie-break, upstream of the
# long-camera passes -- one end start moved 2.5 s, re-timing that end's third
# rock by 0.02 s, and one end boundary moved 2.5 s. The pass took 1.1-1.3 s an
# end and kept 309-317 MB of strips. On 20 rocks of 10/20 S5 the model never
# saw, marked by hand, 17 releases fall within 0.5 m of the mark.
# 2026.10.23.1: the board's card-digit reader (`game/digit_weights.npz`) is
# retrained with this season's cards beside 2025-26's: 13,196 card rows from
# 251 games on the worker's cached recordings
# (`datasets/board-cards-2627`, the shipped reader's labels where it read at
# 0.9999, a person's elsewhere). Held out by league night, at the unchanged
# MIN_CONFIDENCE it reads 96.3-97.2% of frames where the old weights read
# 91.1%, with 1-2 wrong against 3; replaying each game's board walk-back, ends
# read rise 1,118 -> ~1,170 of 250 games, and no wrong end is the digits'.
# The 10/21 Womens S3 game 2 "3" that kept ends 5-6 off the board is the kind
# it now reads. Board scores only: nothing upstream of the board moves.
# 2026.10.26.1: two touching stones of one colour stay two in the house. The
# detector dropped the weaker of two same-colour stones nearer than a stone's
# width (0.284 m), and touching stones read closer than that on the panels: on
# 10/22 Womens S2 e3 s13 ds17b boxed both of two touching yellows (0.94, 0.93),
# 0.273 m apart, and the house lost the guard. The detector now keeps them
# 0.20 m apart (`rocks.SAME_STONE_GAP_M`); the delivery tracking still keeps a
# stone's width (`rocks.ONE_STONE_M`), because moving, a stone pushing a stone
# or a jacket boxed twice reads the same distance apart and pulled two rests
# off course when the tracking took the closer gap too. Only the house read
# (`shots.from_deliveries`' house_frames) sees both. A/B (`scripts/sep_ab.py`,
# one rule-free detection pass, built both ways) on the 16 harness games: 21 of
# 108 ends differ, all in their houses -- no shot, colour, rest, release or
# missing rock moves -- and each end's first gained stone, 21 of 21 checked in
# the frames, is a real stone touching one of its own colour. s13's game: only
# e3 differs, and the guard stays in the house from s13 on. Detections re-infer:
# `rocks.py` is in the detection cache's key.
# 2026.10.28.1: a stone the house places after the shooter's track was lost
# (`release.settle`'s release-add) rests when it is first still in its place
# (`release._settled_at`: within 0.3 m, then 0.05 m for 1.5 s), not when the
# house is read SETTLE_S after the release; and a rock timed by its release
# (t_enter == t_release) keeps the long cameras in its clip
# (`game/clips._release_timed`) instead of cutting to an empty house. 10/20
# Tuesday S2 g2 e5 r11 (flag f_1EuZ): rest 11546 -> 11531.4, the guard's parked
# track having taken the shooter's path at the hit. On the 16 harness games
# board agreement stays 40/54; four release-add rests move 16-24 s earlier and
# three houses by cm, and 4RrN g1 e4 r10's earlier read sees a jacket at the
# house edge (now harvested as a false positive).
# 2026.10.29.1: a run of one house too short to be an end still finishes the
# end before it when it would have joined it (`segment.segment_games`), but
# never makes that end whole or counts toward `_held`. entTaSBFK3A e2: a red
# jacket over the other button cut the last 4 rocks off; they are back. On the
# 11 harness videos, both overhead models, 20 hash seeds: no end starts,
# merges, splits or renumbers; AEqLTgM25Tc g0's last end runs 70 s longer.
# 2026.10.29.2: the last rock cannot move a stone it never reached or was too
# slow to reach (`shots._unreachable`, `_put_back`): its track must come
# within 0.43 m of the stone, directly or through a stone it did reach, and
# its approach speed must carry the stone out of play or as far as it moved.
# Last rock only: the house read after it often lands in the clearing. On the
# 16 harness games board agreement 40 -> 41/54; 10 last rocks change, 7 right
# in the video, none worse.
# 2026.10.31.1: three lines at the broom (spec 2026-10-31-slide-release-final-
# lines): the final line is fitted over the first 9 ft past the hog line
# (`linetime.final_fit`; the 6.4-10 m fit stays the pass's own), and the slide
# and release lines (`game/aimlines.py`) from halfway to the back line to the
# release point and over 9 ft past it. The line check from behind the thrower
# is retired. Timeline schema 10.
PIPELINE_VERSION = "2026.10.31.1"


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


def processing_version(weights, side_weights=None, broom_weights=None, line=False,
                       spin_weights=None, release_weights=None) -> str:
    """The full identity of one analysis configuration.

    ``side_weights`` is folded in because the side-view detector changes the
    timeline as surely as the overhead one does -- it is what times every
    throwing-end hog crossing, and so every split. Leaving it out would let a
    timeline made with one side model, or none, be reused for one made with
    another, which is exactly the quiet mixing this string exists to prevent.

    Omitted entirely when there is no side model, so every timeline produced
    before there was one keeps the identity it was published with.

    ``broom_weights`` likewise: the broom model adds a field to every shot, so
    it is named when present and omitted when not, which keeps every timeline
    made without one at the identity it was published with.

    ``line`` is named only when the line pass ran AND a side model was named:
    without a side model there is no hog-line split to measure the line from,
    so the suffix would say nothing true. The line is measured per shot, so
    when it did run it changes the timeline, and is named to say so.

    ``spin_weights``, the handle-point model, gives every shot its spin, so a
    new one changes every rock's rpm. It is named after the line, and only
    with it: the spin pass reads the rock's boxes with the line's model.

    ``release_weights``, the hand model, puts the release on every rock's
    delivery path, so a new one moves those points. It is named after the
    spin, and only with the line, whose delivery path it reads along.
    """
    base = f"{PIPELINE_VERSION}+{model_id(weights)}"
    out = base if side_weights is None else f"{base}+side-{model_id(side_weights)}"
    out = out if broom_weights is None else f"{out}+broom-{model_id(broom_weights)}"
    if not (line and side_weights is not None):
        return out
    out = f"{out}+line"
    out = out if spin_weights is None else f"{out}+spin-{model_id(spin_weights)}"
    return out if release_weights is None else f"{out}+release-{model_id(release_weights)}"
