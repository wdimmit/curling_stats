"""Run the whole pipeline: a YouTube link in, a game timeline out."""

import json
import logging
import os
import tempfile
from pathlib import Path

from curling_score import timeline, version
from curling_score.detect import delivery, sequence
from curling_score.game import (
    endcheck,
    fit,
    profile,
    scoreboard as sb,
    secondpass,
    segment,
    shots as shots_mod,
)
from curling_score.geometry import layout
from curling_score.ingest import cache, frames as F, proxy, source

log = logging.getLogger(__name__)

CALIB_FRAMES = 24
CALIB_STRIDE = 90  # keyframes apart, to spread samples across the whole video
SHOT_FPS = 10.0  # decoding dominates, so a high rate is nearly free
BOARD_INTERVAL_S = 450.0  # how often to read the wall scoreboard

# The stages a caller can watch, in the order they run. A hosted worker turns
# these into a progress bar; the CLI ignores them.
PHASES = ("download", "proxy", "calibrate", "profile", "detect", "rules",
          "scoreboard")


def _proxy_setups(setups, strip):
    """The same calibrations, with panel rects moved into proxy coordinates.

    A calibration is panel-relative -- its centre and scale describe the cropped
    panel, not the frame -- so only the crop rectangle changes.
    """
    return {
        name: profile.PanelSetup(
            rect=proxy.translate(s.rect, strip), calib=s.calib
        )
        for name, s in setups.items()
    }


def _no_phase(name, fraction, message=None):
    return None


def run_up_from(prev_end_s, start_s: float) -> float:
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
    from here. So everything from that close onward is this end's. The first
    end of a game keeps the old half-minute, since what precedes it on this
    panel is another game's clearing.
    """
    from curling_score.detect.delivery import REQUIRED_LOOKBACK_S
    from curling_score.game.segment import GAME_GAP_S

    lookback = start_s - REQUIRED_LOOKBACK_S
    if prev_end_s is None:
        return max(0.0, lookback)
    return max(0.0, min(lookback, max(prev_end_s, start_s - GAME_GAP_S)))


def analyze(url, root=None, shot_fps=SHOT_FPS, progress=log.info,
            use_proxy: bool = True, weights=None, imgsz: int = 448,
            device=None, *, start_s=None, end_s=None, sheet=None,
            skip_scoreboard: bool = False, on_phase=None, info=None,
            download_attempts=None) -> dict:
    """Analyse a club VOD and return the timeline document.

    ``start_s``/``end_s`` bound the part of the stream that is *analysed*: the
    activity profile and every end within it. The download, calibration and
    proxy still cover the whole video -- a cropped-in-time proxy would shift
    every timestamp and change the detection cache's keys -- so this saves the
    detection time, which is what dominates, not the download.

    ``sheet`` overrides the number read from the title. ``skip_scoreboard``
    leaves out the wall-board pass, which is the only stage that needs the
    full-resolution original. ``on_phase(name, fraction, message)`` is called as
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
    for game in games:
        out_ends = []
        prev_end_s = None
        for end in game.ends:
            setup = read_setups[end.house]
            progress(f"  game {game.index + 1} end {end.number} ({end.house})...")
            phase("detect", done_ends / total_ends,
                  f"game {game.index + 1} end {end.number}")
            from_s = run_up_from(prev_end_s, end.start_s)
            prev_end_s = end.end_s
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
            # Audit what detection actually offered, before the rules trim
            # it -- that is the honest measure of how well detection did.
            audit = endcheck.check(deliveries)
            # An end holds sixteen deliveries thrown strictly in turn, so a
            # longer or doubled candidate list is provably wrong. Without this
            # an over-counted end does not merely score badly, it cannot be
            # built at all: shot 17 has no thrower.
            kept = fit.fit_end(deliveries)
            dropped = len(deliveries) - len(kept)
            shots = shots_mod.from_deliveries(kept, seq)
            built = timeline.build_end(
                end.number, end.house, end.start_s, end.end_s, shots
            )
            built["deliveries_seen"] = len(deliveries)
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
                f"score {built['score']}"
            )
        out_games.append(
            timeline.build_game(game.index, game.start_s, game.end_s, out_ends)
        )
    phase("detect", 1.0, "all ends detected")
    phase("rules", 1.0, "timeline built")

    # --- independent check against the wall scoreboard -----------------
    # Never used for timing: the club often posts it several ends late. It is
    # read here only to say whether the computed scores are believable -- and
    # it is the one stage that reads the full-resolution original, so a caller
    # that does not want to keep that file can leave it out.
    if skip_scoreboard:
        for out_game in out_games:
            out_game["scoreboard"] = None
        phase("scoreboard", 1.0, "skipped")
    else:
        phase("scoreboard", 0.0, "reading the wall scoreboard")
        progress("reading the wall scoreboard...")
        for game, out_game in zip(games, out_games):
            readings = []
            t = game.start_s
            while t <= game.end_s:
                r = sb.read_board_at(path, t)
                if r is not None and not r.is_blank():
                    readings.append(r)
                t += BOARD_INTERVAL_S
            if not readings:
                out_game["scoreboard"] = None
                continue
            consolidated = sb.consolidate(readings)
            final = sb.cumulative(consolidated[-1])
            try:
                per_end = sb.per_end_scores(consolidated)
            except sb.ScoreboardError:
                per_end = None
            agrees = final == out_game["final"]
            out_game["scoreboard"] = {
                "final": final,
                "per_end": per_end,
                "agrees_with_detection": agrees,
            }
            for end in out_game["ends"]:
                end["scoreboard_agrees"] = agrees
            progress(
                f"  game {game.index + 1}: board says {final}, "
                f"detection says {out_game['final']}"
                f"{'' if agrees else '  <-- DISAGREE'}"
            )
        phase("scoreboard", 1.0, "scoreboard read")

    calibration = {
        name: {
            "rect": list(s.rect),
            "px_per_m": round(s.calib.px_per_m, 3),
            "center_px": [round(v, 2) for v in s.calib.center_px],
            "residual_m": round(s.calib.residual_m, 5),
            "flipped": s.calib.flipped,
        }
        for name, s in setups.items()
    }
    return timeline.build_document(
        video_id=info.video_id,
        url=source.canonical_url(url),
        sheet=sheet,
        duration_s=info.duration_s,
        calibration=calibration,
        games=out_games,
        window=(start_s, end_s),
        processing_version=version.processing_version(weights),
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
