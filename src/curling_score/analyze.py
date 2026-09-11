"""Run the whole pipeline: a YouTube link in, a game timeline out."""

import json
import logging
from pathlib import Path

from curling_score import timeline
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


def analyze(url, root=None, shot_fps=SHOT_FPS, progress=log.info,
            use_proxy: bool = True, weights=None, imgsz: int = 448,
            device=None) -> dict:
    """Analyse a club VOD and return the timeline document."""
    info = source.fetch_info(url)
    progress(f"{info.title} ({info.duration_s / 3600:.2f} h, sheet {info.sheet})")

    path = cache.ensure_cached(url, root=root)
    progress(f"video at {path}")

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

    # Every later pass decodes only the overhead strip, which is about a
    # sixteenth of the frame. Analysis is decode-bound, so this is 3-4x cheaper
    # per pass; the one-off transcode pays for itself after the first run.
    read_path, read_setups = path, setups
    if use_proxy:
        strip = proxy.strip_rect(panels.top, panels.bottom)
        read_path = proxy.ensure_proxy(
            path, info.video_id, strip, root=root, progress=progress
        )
        read_setups = _proxy_setups(setups, strip)
        progress(f"reading from strip proxy {strip[2]}x{strip[3]}")

    detector = None
    if weights:
        from curling_score.detect import yolo

        detector = yolo.YoloDetector(weights, conf=0.30, device=device,
                                     imgsz=imgsz)
        detector.model.overrides["half"] = True
        progress(f"detecting with {weights} at imgsz={imgsz}")

    progress("building activity profile...")
    samples = profile.build_profile(read_path, read_setups)
    games = segment.segment_games(samples)
    progress(f"{len(games)} game(s), {[len(g.ends) for g in games]} ends")

    out_games = []
    for game in games:
        out_ends = []
        for end in game.ends:
            setup = read_setups[end.house]
            progress(f"  game {game.index + 1} end {end.number} ({end.house})...")
            seq = sequence.detect_end(read_path, setup, end, shot_fps,
                                      detector)
            # The run-up belongs to the previous end, so anything thrown in it
            # is not this end's.
            deliveries = [
                d for d in delivery.find_deliveries(
                    seq, view_x_limit_m=setup.view_x_limit_m
                )
                if d.t_enter >= end.start_s
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

    # --- independent check against the wall scoreboard -----------------
    # Never used for timing: the club often posts it several ends late. It is
    # read here only to say whether the computed scores are believable.
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
        sheet=info.sheet,
        duration_s=info.duration_s,
        calibration=calibration,
        games=out_games,
    )


def write(document: dict, out_dir) -> Path:
    """Write ``timeline.json``, folding in any hand corrections alongside it."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    overrides_path = out_dir / "overrides.json"
    if overrides_path.is_file():
        document = timeline.apply_overrides(
            document, json.loads(overrides_path.read_text())
        )
    path = out_dir / "timeline.json"
    path.write_text(json.dumps(document, indent=2))
    return path
