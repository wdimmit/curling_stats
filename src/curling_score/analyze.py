"""Run the whole pipeline: a YouTube link in, a game timeline out."""

import json
import logging
import os
import tempfile
from pathlib import Path

import numpy as np

from curling_score import timeline, version
from curling_score import weights as weights_mod
from curling_score.detect import broommodel, delivery, release, sequence, sidemodel
from curling_score.game import (
    broomtime,
    endcheck,
    fartime,
    fit,
    hogtime,
    linetime,
    profile,
    scoreboard as sb,
    secondpass,
    segment,
    shots as shots_mod,
    thinking,
)
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
    nothing but this end's first stones: the previous end was played into the
    other house, and its stones are cleared toward the hack behind it, away
    from here. So everything from that close onward is this end's, bounded by
    the gap that would have split the games. The first end of a game has no
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


def analyze(url, root=None, shot_fps=SHOT_FPS, progress=log.info,
            use_proxy: bool = True, weights=None, imgsz: int = 448,
            device=None, *, start_s=None, end_s=None, sheet=None,
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

    ``sheet`` overrides the number read from the title. ``skip_scoreboard``
    leaves out the wall-board pass, and ``skip_longview`` leaves out the side
    views that time the throwing end's hog crossing -- both are stages that
    need the full-resolution original, for a caller that does not keep it.
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
    progress(f"{info.title} ({info.duration_s / 3600:.2f} h, sheet {sheet})")

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

    phase("profile", 0.0, "finding games and ends")
    progress("building activity profile...")
    sweep = None
    if start_s is not None or end_s is not None:
        sweep = F.keyframe_sweep(read_path, start_s=start_s, end_s=end_s)
    samples = profile.build_profile(read_path, read_setups, sweep=sweep)
    games = segment.segment_games(samples)
    progress(f"{len(games)} game(s), {[len(g.ends) for g in games]} ends")
    phase("profile", 1.0, f"{len(games)} game(s)")

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
        board_block, scores = None, None
        if skip_scoreboard:
            phase("scoreboard", game.index / len(games), "skipped")
        else:
            phase("scoreboard", game.index / len(games),
                  f"reading the wall scoreboard for game {game.index + 1}")
            progress(f"  game {game.index + 1}: reading the wall scoreboard...")
            got = read_board(path, game, progress=progress)
            if got is None:
                progress(f"  game {game.index + 1}: board not read")
            else:
                scores = got.scores
                board_block = {
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
                }
                progress(
                    f"  game {game.index + 1}: board says {scores.final} "
                    f"in {got.reads} read(s)"
                    + (f", ends {list(scores.unread_ends)} not posted"
                       if scores.unread_ends else "")
                )

        out_ends = []
        for end in game.ends:
            setup = read_setups[end.house]
            progress(f"  game {game.index + 1} end {end.number} ({end.house})...")
            phase("detect", done_ends / total_ends,
                  f"game {game.index + 1} end {end.number}")
            from_s = run_up_from(prev_end_s, end.start_s,
                                 crossed_games=end is game.ends[0])
            seq = sequence.detect_end(read_path, setup, end, shot_fps,
                                      detector, from_s=from_s)
            # Anything thrown since the previous end closed is this end's;
            # anything earlier on this panel is not.
            deliveries = [
                d for d in delivery.find_deliveries(
                    seq, view_x_limit_m=setup.view_x_limit_m,
                    view_y_min_m=setup.view_y_min_m,
                )
                if d.t_enter >= from_s
            ]
            # The first pass has to be strict or sweepers count as stones. Once
            # it is in hand the rules say where the gaps are and what colour
            # belongs in them, so a second look can be far more permissive
            # without letting phantoms in everywhere else.
            gaps = secondpass.gaps_to_search(deliveries, end.start_s, end.end_s)
            recovered = secondpass.search(seq, gaps, deliveries)
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
            far = read_setups[OTHER_HOUSE[end.house]]
            far_seq = list(sequence.detect_span(
                read_path, far, from_s, end.end_s,
                release.RELEASE_FPS, detector))
            releases, thrown_by, unaccounted = release.find_and_pair(
                far_seq, far.view_y_min_m, deliveries, seq, since=from_s,
                view_x_limit_m=far.view_x_limit_m,
            )
            if unaccounted:
                deliveries = sorted(deliveries + unaccounted, key=lambda d: d.t_enter)
            # Audit what detection actually offered, before the rules trim
            # it -- that is the honest measure of how well detection did.
            audit = endcheck.check(deliveries)
            # An end holds sixteen deliveries thrown strictly in turn, so a
            # longer or doubled candidate list is provably wrong. Without this
            # an over-counted end does not merely score badly, it cannot be
            # built at all: shot 17 has no thrower.
            # Stones moved while the house is cleared after the last shot
            # look like deliveries in every way but one: nothing released them.
            kept = fit.fit_end(fit.drop_clearing(
                deliveries, seq, fit.released_ids(thrown_by, unaccounted)),
                paired=fit.paired_ids(thrown_by))
            dropped = len(deliveries) - len(kept)
            # The next end's run-up begins when this end's last rock stopped.
            prev_end_s = min(end.end_s, kept[-1].t_rest) if kept else end.end_s
            shots = shots_mod.from_deliveries(
                kept, seq, thrown_by={id(d): r for r, d in thrown_by.items()})
            # Only now that the rules have settled which rocks exist: the
            # clock wants a tee crossing for each of them, which is a far
            # weaker thing to ask of the same footage than a release was, and
            # cannot reach back into the shot list.
            thinking.time_shots(shots, far_seq, far.view_y_min_m)
            if sideviews is not None:
                hogtime.time_hog_crossings(
                    shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]])
                # The skip's target broom, from the camera that sees the
                # destination house -- the OTHER camera from hogtime's.
                broomtime.time_target_brooms(
                    shots, path, sideviews[hogtime.CAMERA_FOR[end.house]],
                    model=broom_model)
                # Where the rock's thrown line passed the skip's broom -- the
                # hog-crossing camera for the line, the destination camera for
                # where it went. Needs hogtime's crossing and broomtime's broom.
                n_lines = linetime.time_lines(
                    shots, path, sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]],
                    sideviews[hogtime.CAMERA_FOR[end.house]], model=line_model)
                progress(f"    end {end.number}: lines {n_lines}/{len(shots)}")
            # Stage 3, the destination hog line, from that panel's painted line.
            # In this block ``setup`` is the destination panel and ``far`` is
            # the THROWING panel -- far from the house being played to.
            fartime.time_far_crossings(shots, near_line=far.hog_line,
                                       far_line=setup.hog_line)
            built = timeline.build_end(
                end.number, end.house, end.start_s, end.end_s, shots,
                board_score=(None if scores is None
                             else scores.per_end.get(end.number)),
            )
            built["deliveries_seen"] = len(deliveries)
            built["releases_seen"] = len(releases)
            built["releases_unaccounted"] = len(unaccounted)
            built["hogged"] = sum(1 for d in unaccounted if d.reason == release.REASON)
            built["deliveries_recovered"] = len(recovered)
            built["deliveries_dropped"] = dropped
            built["thrown"] = audit.thrown
            built["complete"] = audit.complete
            built["problems"] = audit.problems
            built["missed_after"] = audit.missed_after
            built["detection_confidence"] = round(audit.confidence, 3)
            out_ends.append(built)
            done_ends += 1
            progress(
                f"    {len(kept)}/16 deliveries "
                f"(R{audit.thrown['red']} Y{audit.thrown['yellow']} offered"
                f"{f', +{len(recovered)} recovered' if recovered else ''}"
                f"{f', -{dropped} against the rules' if dropped else ''}), "
                + (f"board says {built['score']}" if built["score"] is not None
                   else f"board silent, detected {built['detected_score']}")
            )
        # The scores above were attached by *detected* end number, which is
        # the board's real end number only when nothing but the game was
        # detected. Now that the ends are built we can see the practice
        # signature -- a leading end short of sixteen rocks -- and ask whether
        # the board accounted for every block we found. Doubtful means the
        # scores come back off the ends: a chart that says the board could not
        # be placed is honest, and one end out of step is not. The block keeps
        # "per_end", so a start time typed later puts them back (trim_to_start).
        if board_block is not None and not timeline.settle_board_scores(
                out_ends, board_block, got.board.highest_end()):
            progress(f"  game {game.index + 1}: board scores withheld -- "
                     "leading practice, and the board is short of the ends")
            scores = None
        out_game = timeline.build_game(
            game.index, game.start_s, game.end_s, out_ends, board=scores
        )
        # The board's word against the detector's, which is the only thing
        # that can tell a misread board from a correct one. None while the
        # board left an end unread: there is nothing to compare.
        out_game["scoreboard"] = board_block
        if board_block is not None:
            final = board_block.get("final")
            agrees = (
                None if final is None
                else final == out_game["detected"]["final"]
            )
            out_game["scoreboard"]["agrees_with_detection"] = agrees
            for end in out_game["ends"]:
                end["scoreboard_agrees"] = agrees
        out_games.append(out_game)
    phase("detect", 1.0, "all ends detected")
    phase("scoreboard", 1.0, "skipped" if skip_scoreboard else "scoreboard read")
    phase("rules", 1.0, "timeline built")

    calibration = {
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
