"""Run the whole pipeline: a YouTube link in, a game timeline out."""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from curling_score import timeline, version
from curling_score import weights as weights_mod
from curling_score.detect import broommodel, delivery, release, sequence, sidemodel
from curling_score.game import (
    boardsplit,
    broomtime,
    endcheck,
    fartime,
    fit,
    hogtime,
    linetime,
    placement,
    profile,
    scoreboard as sb,
    secondpass,
    segment,
    shots as shots_mod,
    sidereleases,
    thinking,
)
from curling_score.game import format as format_mod
from curling_score.geometry import layout, sideview
from curling_score.ingest import cache, frames as F, proxy, source

log = logging.getLogger(__name__)

CALIB_FRAMES = 24
CALIB_STRIDE = 90  # keyframes apart, to spread samples across the whole video
SHOT_FPS = 10.0  # decoding dominates, so a high rate is nearly free

# The stages a caller can watch, in the order they run. A hosted worker turns
# these into a progress bar; the CLI ignores them.
# "scoreboard" sits ahead of "detect": the board is the score, so each game's
# board is read before any of its ends can be built.
PHASES = ("download", "proxy", "calibrate", "profile", "scoreboard", "detect",
          "rules")
# Each end is played into one house and thrown from behind the other.
OTHER_HOUSE = {"top": "bottom", "bottom": "top"}


def _with_lateral(plate, view, name, progress):
    """``view`` with its across-the-sheet calibration, or unchanged if the ring's
    sides could not be read -- which costs that view its brooms and lines and
    nothing else. The hog tripwire is depth alone and never waits on this.
    The painted centre line is then traced; a view whose line cannot be read
    keeps the ring's centre."""
    try:
        view = sideview.solve_lateral(plate, view, name=name)
    except sideview.SideViewError as exc:
        progress(f"{name} view has no lateral calibration, so no brooms from it: {exc}")
        return view
    try:
        return sideview.solve_centre_line(plate, view, name=name)
    except sideview.SideViewError as exc:
        progress(f"{name} view: no painted centre line, lateral figures from the ring's centre: {exc}")
        return view


def _side_calibration(sideviews) -> dict:
    out = {}
    for name, v in (sideviews or {}).items():
        d = {"rect": list(v.rect), "tee_row": round(v.tee_row, 2),
             "hog_row": round(v.hog_row, 2)}
        if v.has_lateral:
            d["centre_col"] = round(v.centre_col, 2)
            d["lat_px_per_m_at_tee"] = round(v.lat_px_per_m_at_tee, 3)
            if v.centre_line is not None:
                d["centre_line"] = [round(v.centre_line[0], 3), round(v.centre_line[1], 6)]
        out[name] = d
    return out


def calibration_block(setups, sideviews) -> dict:
    """The timeline's record of how each panel and side view was calibrated."""
    return {
        **{
            name: {
                "rect": list(s.rect),
                "px_per_m": round(s.calib.px_per_m, 3),
                "center_px": [round(v, 2) for v in s.calib.center_px],
                "residual_m": round(s.calib.residual_m, 5),
                "flipped": s.calib.flipped,
                "hog_line": None if s.hog_line is None else s.hog_line.to_json(),
                "hog_line_error": s.hog_line_error,
            }
            for name, s in setups.items()
        },
        **_side_calibration(sideviews),
    }


def _proxy_setups(setups, strip):
    """The same calibrations, with panel rects moved into proxy coordinates.

    A calibration is panel-relative -- its centre and scale describe the cropped
    panel, not the frame -- so only the crop rectangle changes.
    """
    return {
        name: profile.PanelSetup(
            rect=proxy.translate(s.rect, strip), calib=s.calib,
            # The line is in panel pixels, so moving the panel does not move it.
            hog_line=s.hog_line, hog_line_error=s.hog_line_error,
        )
        for name, s in setups.items()
    }


def _no_phase(name, fraction, message=None):
    return None


def run_up_from(prev_end_s, start_s: float, *, crossed_games: bool = False) -> float:
    """Where an end's run-up begins: the moment the previous end closed.

    The segmenter starts an end when a stone first rests in its house, read
    from a keyframe sweep and smoothed, so the boundary lands after the first
    stone has settled -- and a first rock thrown through the house leaves
    nothing for it to see at all. Game 3 end 2 opened with a red into the top
    of the house at 986, fourteen seconds before the boundary; end 3 opened
    with a yellow through the house at 1903, thirty-seven seconds before.
    Both were detected and both were discarded as belonging to the run-up.

    Between the previous end closing and this one opening, this panel holds
    nothing but this end's stones (in doubles, its two placed stones and then
    its first deliveries). The previous end was played into the other house,
    and its stones are cleared toward the hack behind it, away from here. So
    everything from that close onward is this end's, bounded by the gap that
    would have split the games. The first end of a game has no
    previous end and gets the whole gap: game 5's opening yellow ran clean
    through the house 67 s before the segmenter saw the end begin, which is
    what a through-shot does -- it leaves nothing for the segmenter to see.
    The previous game's last rock still bounds it where there is one.

    "Closed" means the previous end's last rock came to rest, not when its
    house emptied: teams throw the next end's first rock while the far house
    is still being cleared. Game 4 end 2's hogged red left the hack at 1109,
    forty seconds after end 1's last rock stopped and a minute before the
    segmenter saw end 1's house empty.

    Within a game the close is the only bound a later end needs, because the
    previous end's rocks cannot lie before its own last rest -- so reaching
    back to it can never reach into it. This used to be clamped to
    ``GAME_GAP_S`` as well, which is really a second and shorter clock:
    ``GAME_GAP_S`` measures how long *both houses stay empty* before the sheet
    counts as reset, while a turnaround is scoring, clearing, walking down and
    setting up, all of which happen with stones still on the sheet. Game
    4RrNWSeNnMU end 4 took 443.8 s over that turnaround, and the 240 s clamp
    cut its window 19 s short of its first rock -- a red centre guard at rest
    at (+0.09, +2.51), which then cost the end its rock count and inverted its
    hammer.

    ``crossed_games`` is the case where the clamp earns its keep: the first end
    of a later game anchors to the *previous game's* last rock, and between
    games the sheet is open and players slide practice rocks. There the gap
    still bounds the reach. The first end of the video has nothing to anchor
    to and gets the gap alone.
    """
    from curling_score.detect.delivery import REQUIRED_LOOKBACK_S
    from curling_score.game.segment import GAME_GAP_S

    lookback = start_s - REQUIRED_LOOKBACK_S
    if prev_end_s is None:
        floor = start_s - GAME_GAP_S
    elif crossed_games:
        floor = max(prev_end_s, start_s - GAME_GAP_S)
    else:
        floor = prev_end_s
    return max(0.0, min(lookback, floor))


def read_board(path, game, progress=log.info):
    """One game's wall board, or None if it could not be read. Never raises.

    By the time this runs the job has already paid for the download, the
    proxy and the activity profile, and a hosted run will have paid for some
    of the detection too. Every other way the read can fail -- no board found,
    the frame occluded, the cards contradicting each other -- already returns
    None and leaves the game without a score, which is a designed outcome. A
    decode or geometry exception is no different in kind, so it degrades the
    same way instead of throwing the whole job away. Logged with its
    traceback, because "the board was not read" and "OpenCV fell over" want
    different responses from whoever reads the logs.
    """
    try:
        return sb.read_game_board(path, game.start_s, game.end_s, len(game.ends))
    except Exception:
        log.exception("game %s: the wall scoreboard pass failed", game.index + 1)
        progress(f"  game {game.index + 1}: board read failed, see the log")
        return None


def resolve_format(game_format: str | None, title: str | None):
    """The format to analyse as: the one asked for, else what the title says."""
    return format_mod.by_name(game_format or source.format_from_title(title))


@dataclass
class EndContext:
    """What building one end needs that stays the same for the whole video.

    ``path`` is the full-resolution original, which the side views are decoded
    from. ``read_path`` is what the overhead detection reads -- the strip proxy
    for a recording, the recording itself for a live stream -- and
    ``read_setups`` are the panels in its coordinates. ``use_cache`` is off for
    a file still being written: the detection cache keys on the file's size, so
    a growing file would never hit it and would store truncated spans.
    """

    path: object
    read_path: object
    read_setups: dict
    sideviews: dict | None
    detector: object
    broom_model: object
    line_model: object
    fmt: format_mod.GameFormat
    use_cache: bool = True
    shot_fps: float = SHOT_FPS
    progress: object = log.info
    # Decode the end's window once for both panels (sequence.detect_spans)
    # rather than once each: for a live recording, read at full resolution
    # with no proxy and no cache, the decode is most of an end's cost.
    one_pass: bool = False


@dataclass
class BoardRead:
    """One game's wall board, and the block a timeline carries for it."""

    got: sb.GameBoard
    scores: sb.BoardScores
    block: dict


def calibrate_from(calib_frames, *, skip_longview: bool, progress=log.info):
    """Panels, their calibrations and the side views, from a few keyframes.

    Returns ``(panels, setups, sideviews)``; ``sideviews`` is None when they
    were skipped or could not be read, which costs the splits and nothing else.
    """
    panels = layout.detect_panels(calib_frames)
    setups = profile.calibrate_panels(calib_frames, panels)
    for name, setup in setups.items():
        progress(
            f"{name} panel {setup.rect} {setup.calib.px_per_m:.1f} px/m "
            f"(residual {setup.calib.residual_m * 100:.2f} cm)"
        )
    # The two wide side views. They watch the far end's hog line, which the
    # overhead panel loses on about 40% of throws, and they are read straight
    # from the original -- like the scoreboard, and for the same reason.
    sideviews = None
    if not skip_longview:
        h, w = calib_frames[0].shape[:2]
        plate = np.median(
            np.stack([f.astype("float32") for f in calib_frames]), axis=0)
        try:
            rects = sideview.locate(panels, width=w, height=h)
            sideviews = {n: sideview.solve(plate, r, name=n)
                         for n, r in rects.items()}
            sideviews = {n: _with_lateral(plate, v, n, progress)
                         for n, v in sideviews.items()}
        except sideview.SideViewError as exc:
            progress(f"side views unusable, so this video has no splits: {exc}")
    return panels, setups, sideviews


def load_models(weights, imgsz: int, device, *, skip_longview: bool,
                skip_line: bool, progress=log.info):
    """The overhead detector, the broom model and the line model: ``(detector,
    broom_model, line_model)``, each None where it is not wanted or not there."""
    detector = None
    if weights:
        from curling_score.detect import yolo

        detector = yolo.YoloDetector(weights, conf=0.30, device=device,
                                     imgsz=imgsz)
        detector.model.overrides["half"] = True
        progress(f"detecting with {weights} at imgsz={imgsz}")
    # The skip's target broom, read in the camera that sees the destination
    # house. None without a model -- no brooms, the same timeline otherwise.
    broom_model = None if skip_longview else broommodel.default_model()
    if broom_model is not None:
        progress(f"finding target brooms with {weights_mod.broom_path()}")
    # The rock's thrown line against the broom: the side model again, which
    # hogtime has already loaded (`sidemodel._load` is cached). No broom
    # model means no brooms, so there is nothing for a line to be measured
    # against either.
    line_model = (None if (skip_longview or skip_line or broom_model is None)
                  else sidemodel.default_model())
    return detector, broom_model, line_model


def board_block(got) -> BoardRead:
    """A board read as the timeline carries it: every card, and the scores."""
    scores = got.scores
    return BoardRead(got=got, scores=scores, block={
        "read_at_s": round(got.read_at_s, 2),
        "reads": got.reads,
        "unread_ends": list(scores.unread_ends),
        "final": scores.final,
        "cards": {
            color: [
                {"slot": c.slot, "end": c.end,
                 "confidence": round(c.confidence, 4)}
                for c in getattr(got.board, color)
            ]
            for color in sb.COLORS
        },
        "per_end": {str(k): v for k, v in sorted(scores.per_end.items())},
    })


def board_for_game(path, game, progress=log.info) -> BoardRead | None:
    """One game's wall board from the original, or None. Never raises."""
    progress(f"  game {game.index + 1}: reading the wall scoreboard...")
    got = read_board(path, game, progress=progress)
    if got is None:
        progress(f"  game {game.index + 1}: board not read")
        return None
    board = board_block(got)
    scores = board.scores
    progress(
        f"  game {game.index + 1}: board says {scores.final} "
        f"in {got.reads} read(s)"
        + (f", ends {list(scores.unread_ends)} not posted"
           if scores.unread_ends else "")
    )
    return board


def build_one_end(ctx: EndContext, game, end, prev_end_s, board_score):
    """One end, from detection to the timeline's record of it.

    Returns ``(built, closed_s)``: the end's timeline dict, and when its last
    rock came to rest, which is where the next end's run-up begins. Nothing
    else carries from one end to the next.
    """
    fmt, progress = ctx.fmt, ctx.progress
    path, read_path, read_setups = ctx.path, ctx.read_path, ctx.read_setups
    sideviews = ctx.sideviews
    setup = read_setups[end.house]
    progress(f"  game {game.index + 1} end {end.number} ({end.house})...")
    from_s = run_up_from(prev_end_s, end.start_s,
                         crossed_games=end is game.ends[0])
    far = read_setups[OTHER_HOUSE[end.house]]
    if ctx.one_pass:
        from curling_score.detect.delivery import REQUIRED_LOOKBACK_S

        # The spans detect_end and the far panel's detect_span would read.
        near_from = max(0.0, min(end.start_s - REQUIRED_LOOKBACK_S, from_s))
        seq, far_seq = sequence.detect_spans(
            read_path, [(setup, near_from, ctx.shot_fps),
                        (far, from_s, release.RELEASE_FPS)], end.end_s, ctx.detector)
    else:
        seq = sequence.detect_end(read_path, setup, end, ctx.shot_fps,
                                  ctx.detector, use_cache=ctx.use_cache,
                                  from_s=from_s)
    # Anything thrown since the previous end closed is this end's;
    # anything earlier on this panel is not.
    deliveries = [
        d for d in delivery.find_deliveries(
            seq, view_x_limit_m=setup.view_x_limit_m,
            view_y_min_m=setup.view_y_min_m,
        )
        if d.t_enter >= from_s
    ]
    # Doubles: every delivery of the end comes after its placement is
    # complete, so find that moment before anything is counted.
    placed = (placement.find(seq, from_s, end.end_s)
              if fmt.placed_per_team else None)
    deliveries, before_placement = placement.exclude(deliveries, placed)
    # The first pass has to be strict or sweepers count as stones. Once
    # it is in hand the rules say where the gaps are and what colour
    # belongs in them, so a second look can be far more permissive
    # without letting phantoms in everywhere else.
    gap_from = (end.start_s if placed is None
                else max(end.start_s, placed.t_s + placement.SETTLE_S))
    gaps = secondpass.gaps_to_search(
        deliveries, gap_from, end.end_s,
        per_end=fmt.delivered_per_end, per_team=fmt.delivered_per_team)
    recovered, recovered_early = placement.exclude(
        secondpass.search(seq, gaps, deliveries), placed)
    if recovered:
        deliveries = sorted(
            deliveries + recovered, key=lambda d: d.t_enter
        )
    # The other panel is the thrower's house, and every delivery
    # crosses it on the way out. A throw seen there with no arrival
    # here is a hogged rock -- the one kind of miss this house can
    # never show, and it puts every later thrower off by one. Paired
    # only now, after the second pass: an arrival the gap search
    # recovers is still an arrival, and a release standing in for it
    # would have hidden the very gap that finds it.
    if not ctx.one_pass:
        far_seq = list(sequence.detect_span(
            read_path, far, from_s, end.end_s,
            release.RELEASE_FPS, ctx.detector, use_cache=ctx.use_cache))
    since = from_s if placed is None else max(from_s, placed.t_s)
    arrivals = deliveries
    releases, thrown_by, unaccounted = release.find_and_pair(
        far_seq, far.view_y_min_m, arrivals, seq,
        since=since,
        view_x_limit_m=far.view_x_limit_m,
    )

    def by_the_rules(unaccounted):
        cands = (sorted(arrivals + unaccounted, key=lambda d: d.t_enter)
                 if unaccounted else arrivals)
        # An end holds its format's deliveries thrown strictly in turn, so
        # a longer or doubled candidate list is provably wrong. Without
        # this an over-counted end does not merely score badly, it cannot
        # be built at all: the last shot has no thrower.
        # Stones moved while the house is cleared after the last shot
        # look like deliveries in every way but one: nothing released them.
        return cands, fit.fit_end(fit.drop_clearing(
            cands, seq, fit.released_ids(thrown_by, unaccounted)),
            paired=fit.paired_ids(thrown_by),
            per_end=fmt.delivered_per_end, per_team=fmt.delivered_per_team)

    deliveries, kept = by_the_rules(unaccounted)
    # A rock seen at neither end: a hogged rock whose thrower also hid its
    # release from above leaves this end one short and nothing to say where,
    # and the blank then goes last -- which, for a missed rock 1, names every
    # thrower after it wrongly and hands the hammer to the other team. The
    # long camera facing the thrower still sees it slide out of the hack.
    lost = []
    short = fmt.delivered_per_end - len(kept)
    if sideviews is not None and 0 < short <= shots_mod.MAX_FILL:
        lost = sidereleases.lost_rocks(
            path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
            kept=kept, releases=releases, thrown_by=thrown_by, arrivals=arrivals,
            house_frames=seq, t0=since, t1=end.end_s)
        if lost:
            progress(f"    end {end.number}: {len(lost)} rock(s) the overhead saw at "
                     f"neither end, from the long camera")
            unaccounted = unaccounted + lost
            deliveries, kept = by_the_rules(unaccounted)
    # Audit what detection actually offered, before the rules trim
    # it -- that is the honest measure of how well detection did.
    audit = endcheck.check(deliveries, per_end=fmt.delivered_per_end,
                           per_team=fmt.delivered_per_team)
    dropped = len(deliveries) - len(kept)
    # The arrangement as it stood when rock 1 was on its way: a power
    # play set up in two steps shows its final shape here.
    if placed is not None and kept:
        placed = placement.read_before(seq, placed, kept[0].t_enter)
    # The next end's run-up begins when this end's last rock stopped.
    closed_s = min(end.end_s, kept[-1].t_rest) if kept else end.end_s
    shots = shots_mod.from_deliveries(
        kept, seq, thrown_by={id(d): r for r, d in thrown_by.items()},
        fmt=fmt,
        before=placed.seed if placed is not None else (),
        base=placement.fill_base(placed, fmt))
    # Only now that the rules have settled which rocks exist: the
    # clock wants a tee crossing for each of them, which is a far
    # weaker thing to ask of the same footage than a release was, and
    # cannot reach back into the shot list.
    thinking.time_shots(shots, far_seq, far.view_y_min_m)
    if sideviews is not None:
        hogtime.time_hog_crossings(
            shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]])
        # The releases the throwing panel lost -- a thrower whose body hides
        # the stone from above -- from hogtime's camera, which faces the
        # delivery. Before the brooms and lines: both read what it gives.
        n_side = sidereleases.time_side_releases(
            shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]])
        if n_side:
            progress(f"    end {end.number}: {n_side} release(s) from the long camera")
        # The skip's target broom, from the camera that sees the
        # destination house -- the OTHER camera from hogtime's.
        broomtime.time_target_brooms(
            shots, path, sideviews[hogtime.CAMERA_FOR[end.house]],
            model=ctx.broom_model)
        # Where the rock's thrown line passed the skip's broom -- the
        # hog-crossing camera for the line, the destination camera for
        # where it went. Needs hogtime's crossing and broomtime's broom.
        # In doubles, a rock nobody held a broom for is measured too.
        n_lines = linetime.time_lines(
            shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
            sideviews[hogtime.CAMERA_FOR[end.house]], model=ctx.line_model,
            without_broom=fmt.line_without_broom)
        progress(f"    end {end.number}: lines {n_lines}/{len(shots)}")
    # Stage 3, the destination hog line, from that panel's painted line.
    # In this block ``setup`` is the destination panel and ``far`` is
    # the THROWING panel -- far from the house being played to.
    fartime.time_far_crossings(shots, near_line=far.hog_line,
                               far_line=setup.hog_line)
    built = timeline.build_end(
        end.number, end.house, end.start_s, end.end_s, shots,
        board_score=board_score,
        fmt=fmt,
        placement=placed,
    )
    if built.get("placement") is not None:
        built["placement"]["candidates_dropped"] = (
            len(before_placement) + len(recovered_early))
    built["deliveries_seen"] = len(deliveries)
    built["releases_seen"] = len(releases)
    built["releases_unaccounted"] = len(unaccounted)
    built["hogged"] = sum(1 for d in unaccounted if d.reason == release.REASON)
    built["lost_rocks_found"] = len(lost)
    built["deliveries_recovered"] = len(recovered)
    built["deliveries_dropped"] = dropped
    built["thrown"] = audit.thrown
    built["complete"] = audit.complete
    built["problems"] = audit.problems
    if fmt.placed_per_team and (placed is None or not placed.complete):
        # Say so when the placement stage had to fall back: the hammer
        # then comes from rock 1, or rock 1's fill base is one short.
        built["problems"] = list(audit.problems) + [
            "no placement found; hammer read from the first rock"
            if placed is None else "the placement's guard was not seen"]
    built["missed_after"] = audit.missed_after
    built["detection_confidence"] = round(audit.confidence, 3)
    p = built.get("placement")
    placed_note = "" if not fmt.placed_per_team else (
        "no placement, " if not p else
        f"placement {p['hammer']}"
        + (f" power play {p['power_play']}" if p["power_play"] else "") + ", ")
    n_before = (p or {}).get("candidates_dropped", 0)
    progress(
        f"    {len(kept)}/{fmt.delivered_per_end} deliveries "
        f"(R{audit.thrown['red']} Y{audit.thrown['yellow']} offered"
        f"{f', +{len(recovered)} recovered' if recovered else ''}"
        f"{f', -{dropped} against the rules' if dropped else ''}"
        f"{f', -{n_before} before placement' if n_before else ''}), "
        + placed_note
        + (f"board says {built['score']}" if built["score"] is not None
           else f"board silent, detected {built['detected_score']}")
    )
    return built, closed_s


def finish_game(game, out_ends, board: BoardRead | None, fmt,
                progress=log.info) -> dict:
    """A game's timeline dict from its built ends and its board, if read."""
    scores = board.scores if board is not None else None
    board_block_ = board.block if board is not None else None
    # The scores on the ends were attached by *detected* end number, which is
    # the board's real end number only when nothing but the game was
    # detected. Now that the ends are built we can see the practice
    # signature -- a leading end short of sixteen rocks -- and ask whether
    # the board accounted for every block we found. Doubtful means the
    # scores come back off the ends: a chart that says the board could not
    # be placed is honest, and one end out of step is not. The block keeps
    # "per_end", so a start time typed later puts them back (trim_to_start).
    if board_block_ is not None and not timeline.settle_board_scores(
            out_ends, board_block_, board.got.board.highest_end(), fmt=fmt):
        progress(f"  game {game.index + 1}: board scores withheld -- "
                 "leading practice, and the board is short of the ends")
        scores = None
    out_game = timeline.build_game(
        game.index, game.start_s, game.end_s, out_ends, board=scores,
        fmt=fmt,
    )
    # The board's word against the detector's, which is the only thing
    # that can tell a misread board from a correct one. None while the
    # board left an end unread: there is nothing to compare.
    out_game["scoreboard"] = board_block_
    if board_block_ is not None:
        final = board_block_.get("final")
        agrees = (
            None if final is None
            else final == out_game["detected"]["final"]
        )
        out_game["scoreboard"]["agrees_with_detection"] = agrees
        for end in out_game["ends"]:
            end["scoreboard_agrees"] = agrees
    return out_game


def played(game, out_ends):
    """The game without the ends at its tail in which nothing was thrown
    (:func:`timeline.nothing_thrown`), and its built ends to match; None when
    nothing was thrown in any of it. The game itself is left as it was."""
    keep = len(out_ends)
    while keep and timeline.nothing_thrown(out_ends[keep - 1]):
        keep -= 1
    if keep == len(out_ends):
        return game, out_ends
    if not keep:
        return None, []
    ends = game.ends[:keep]
    return replace(game, ends=ends, end_s=ends[-1].end_s), out_ends[:keep]


def put_board_scores(out_ends, board: BoardRead | None) -> None:
    """Each built end's score from ``board``, or none: what build_end does
    with the board score it is handed, done again after the fact."""
    for end in out_ends:
        score = None if board is None else board.scores.per_end.get(end["number"])
        end["score"] = None if score is None else dict(score)
        end["score_source"] = None if score is None else "board"


def build_games(ctx: EndContext, games, *, read_board=None,
                phase=_no_phase) -> list[dict]:
    """Every game's ends built in order and each game finished with its board.

    ``read_board(game)`` is the game's :class:`BoardRead` or None; left out,
    the board is skipped and no game has a score. Ends at a game's tail in
    which nothing was thrown are left off, and a game of nothing but those is
    no game: the rest are numbered from 0 as they stand.
    """
    progress = ctx.progress
    total_ends = sum(len(g.ends) for g in games) or 1
    done_ends = 0
    out_games = []
    prev_end_s = None
    for game in games:
        # --- the wall scoreboard, read before a single end is built -------
        # The board is the score, so it has to be in hand before build_end
        # can be handed one. Never used for timing: the club often posts it
        # several ends late. It is the one stage that reads the
        # full-resolution original, so a caller that does not want to keep
        # that file can leave it out -- and then the game simply has no
        # score, which is the designed outcome and not an error.
        board = None
        if read_board is None:
            phase("scoreboard", game.index / len(games), "skipped")
        else:
            phase("scoreboard", game.index / len(games),
                  f"reading the wall scoreboard for game {game.index + 1}")
            board = read_board(game)

        out_ends = []
        for end in game.ends:
            phase("detect", done_ends / total_ends,
                  f"game {game.index + 1} end {end.number}")
            built, prev_end_s = build_one_end(
                ctx, game, end, prev_end_s,
                board_score=(None if board is None
                             else board.scores.per_end.get(end.number)))
            out_ends.append(built)
            done_ends += 1
        kept, out_ends = played(game, out_ends)
        if kept is None:
            progress(f"  game {game.index + 1}: nothing thrown in it, so no game")
            continue
        if kept is not game:
            progress(f"  game {game.index + 1}: nothing thrown after end "
                     f"{len(kept.ends)}, {len(game.ends) - len(kept.ends)} end(s) left off")
            # The board was read at the old last end, which is often after the
            # club had cleared it. Read it at the game's own.
            if read_board is not None:
                board = read_board(kept)
                put_board_scores(out_ends, board)
        out_games.append(finish_game(replace(kept, index=len(out_games)), out_ends,
                                     board, ctx.fmt, progress))
    return out_games


def analyze(url, root=None, shot_fps=SHOT_FPS, progress=log.info,
            use_proxy: bool = True, weights=None, imgsz: int = 448,
            device=None, *, start_s=None, end_s=None, sheet=None,
            game_format: str | None = None,
            skip_scoreboard: bool = False, skip_longview: bool = False,
            skip_line: bool = False,
            on_phase=None, info=None,
            download_attempts=None) -> dict:
    """Analyse a club VOD and return the timeline document.

    ``start_s``/``end_s`` bound the part of the stream that is *analysed*: the
    activity profile and every end within it. The download, calibration and
    proxy still cover the whole video -- a cropped-in-time proxy would shift
    every timestamp and change the detection cache's keys -- so this saves the
    detection time, which is what dominates, not the download.

    ``sheet`` overrides the number read from the title. ``game_format`` is
    ``"fours"`` or ``"doubles"``; ``None`` reads it from the title instead
    (see :func:`resolve_format`). ``skip_scoreboard`` leaves out the
    wall-board pass, and ``skip_longview`` leaves out the side views that time
    the throwing end's hog crossing -- both are stages that need the
    full-resolution original, for a caller that does not keep it.
    ``skip_line`` leaves out measuring each rock's thrown line against the
    broom, which otherwise runs whenever ``skip_longview`` did not already
    rule it out.
    ``on_phase(name, fraction, message)`` is called as
    the stages run, for a caller that wants to show progress. ``info`` lets a
    caller that already fetched the metadata pass it in rather than ask
    YouTube twice.
    """
    phase = on_phase or _no_phase
    info = info or source.fetch_info(url)
    sheet = sheet if sheet is not None else info.sheet
    fmt = resolve_format(game_format, info.title)
    progress(f"{info.title} ({info.duration_s / 3600:.2f} h, sheet {sheet}, {fmt.name})")

    phase("download", 0.0, "downloading video")
    download_kwargs = {}
    if download_attempts is not None:
        download_kwargs["attempts"] = download_attempts
    path = cache.ensure_cached(
        url, root=root,
        # A caller watching phases gets the fraction through on_phase, so
        # yt-dlp's own progress bar is redundant -- and it rewrites its line
        # thousands of times, which in a container makes the log unreadable.
        progress=on_phase is None,
        progress_hook=lambda f, m: phase("download", f, m),
        **download_kwargs,
    )
    phase("download", 1.0, "video ready")
    progress(f"video at {path}")

    phase("calibrate", 0.0, "sampling keyframes")
    progress("sampling keyframes for layout and calibration...")
    calib_frames = F.sample_keyframes(path, count=CALIB_FRAMES, stride=CALIB_STRIDE)
    progress(f"{len(calib_frames)} calibration frames")
    panels, setups, sideviews = calibrate_from(
        calib_frames, skip_longview=skip_longview, progress=progress)
    phase("calibrate", 1.0, "calibrated")

    # Every later pass decodes only the overhead strip, which is about a
    # sixteenth of the frame. Analysis is decode-bound, so this is 3-4x cheaper
    # per pass; the one-off transcode pays for itself after the first run.
    read_path, read_setups = path, setups
    if use_proxy:
        strip = proxy.strip_rect(panels.top, panels.bottom)
        phase("proxy", 0.0, "building strip proxy")
        read_path = proxy.ensure_proxy(
            path, info.video_id, strip, root=root, progress=progress
        )
        phase("proxy", 1.0, "proxy ready")
        read_setups = _proxy_setups(setups, strip)
        progress(f"reading from strip proxy {strip[2]}x{strip[3]}")
    else:
        phase("proxy", 1.0, "reading the full frame")

    detector, broom_model, line_model = load_models(
        weights, imgsz, device, skip_longview=skip_longview,
        skip_line=skip_line, progress=progress)

    phase("profile", 0.0, "finding games and ends")
    progress("building activity profile...")
    sweep = None
    if start_s is not None or end_s is not None:
        sweep = F.keyframe_sweep(read_path, start_s=start_s, end_s=end_s)
    samples = profile.build_profile(read_path, read_setups, sweep=sweep)
    games = segment.segment_games(
        samples, min_end_s=fmt.delivered_per_end * segment.MIN_DELIVERY_GAP_S)
    if not skip_scoreboard:
        # The empty sheet is a changeover's cue, and the board settles it both
        # ways. A pause in a game can sit empty as long, and the board, still
        # up through it, joins the game back together; a changeover with
        # stones still in view never reads empty long enough to end a game,
        # and the board, cleared after it, splits it. Read from the original:
        # the proxy holds only the houses.
        def states(t0, t1):
            return boardsplit.board_states(path, t0, t1)

        n_before = len(games)
        games = boardsplit.join_games(games, states)
        if len(games) < n_before:
            progress(f"the scoreboard joined {n_before - len(games)} pause(s) "
                     "the empty sheet had called a new game")
        n_before = len(games)
        games = boardsplit.split_games(games, states)
        if len(games) > n_before:
            progress(f"the scoreboard split {len(games) - n_before} changeover(s) "
                     "the empty sheet did not")
    progress(f"{len(games)} game(s), {[len(g.ends) for g in games]} ends")
    phase("profile", 1.0, f"{len(games)} game(s)")

    ctx = EndContext(
        path=path, read_path=read_path, read_setups=read_setups,
        sideviews=sideviews, detector=detector, broom_model=broom_model,
        line_model=line_model, fmt=fmt, shot_fps=shot_fps, progress=progress,
    )
    out_games = build_games(
        ctx, games, phase=phase,
        read_board=(None if skip_scoreboard
                    else lambda game: board_for_game(path, game, progress=progress)))
    phase("detect", 1.0, "all ends detected")
    phase("scoreboard", 1.0, "skipped" if skip_scoreboard else "scoreboard read")
    phase("rules", 1.0, "timeline built")

    calibration = calibration_block(setups, sideviews)
    check = timeline.format_check(out_games, fmt)
    return timeline.build_document(
        video_id=info.video_id,
        url=source.canonical_url(url),
        sheet=sheet,
        duration_s=info.duration_s,
        calibration=calibration,
        games=out_games,
        window=(start_s, end_s),
        # The side detector is folded in: it times every throwing-end hog
        # crossing, so it changes the timeline as surely as the overhead one.
        # Resolved here rather than passed, because nothing upstream chooses
        # it -- `hogtime` takes the same default.
        processing_version=version.processing_version(
            weights, weights_mod.side_path(), weights_mod.broom_path(),
            line=line_model is not None and sideviews is not None),
        fmt=fmt,
        check=check,
    )


def write(document: dict, out_dir) -> Path:
    """Write ``timeline.json``, folding in any hand corrections alongside it.

    Written to a temporary file and renamed, so a crash mid-write leaves the
    previous timeline in place rather than a truncated one that fails to parse.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = out_dir / "overrides.json"
    if overrides_path.is_file():
        document = timeline.apply_overrides(
            document, json.loads(overrides_path.read_text())
        )
    path = out_dir / "timeline.json"
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".timeline-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(document, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
