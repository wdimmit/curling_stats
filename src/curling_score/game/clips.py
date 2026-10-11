"""A claimed game rock's clip: the practice clip, cut from the game's video.

The clips worker (service/clipworker.py) is handed each rock as the game's
timeline has it (service/gameclips.player_rocks) and the long cameras as the
run calibrated them. A rock is turned into the practice throw record, so the
practice cutter plans its pieces -- the camera facing the thrower, then the one
behind -- and the practice overlay places the broom and lines in each.

A game's times are on its video's own clock, so the cut needs no offset. The
far hog line is the camera switch; timelines from before it was recorded
switch at the rock's arrival in the far panel instead, a little early.
"""

import logging
import subprocess
import time
from pathlib import Path

from curling_score.geometry.sideview import SideView
from curling_score.practice import clips as practice_clips
from curling_score.practice import overlay as practice_overlay

log = logging.getLogger(__name__)

SIDE_VIEWS = ("left", "right")


def throw_from_rock(rock: dict) -> dict:
    """``rock`` as the practice cutter and overlay read a throw."""
    line = rock.get("line") or {}
    broom = rock.get("target_broom")
    t_far = rock.get("t_far_s")
    if t_far is None:
        t_far = rock.get("t_enter_s")
    out_line = None
    if line:
        out_line = {"at_hog_x": (line.get("at_hog") or {}).get("x"),
                    "miss_m": (line.get("at_broom") or {}).get("miss_m"),
                    "aim_x": (line.get("at_tee") or {}).get("x"),
                    "start": line.get("start")}
    return {
        "id": rock["name"], "house": rock["house"],
        "t_release_s": rock.get("t_release_s"), "t_rest_s": rock.get("t_rest_s"),
        "arrived": rock.get("t_enter_s") is not None,
        "t_hog_s": rock.get("t_hog_s"), "v_hog_m_s": rock.get("v_hog_m_s"), "t_far_s": t_far,
        "broom": None if broom is None else {"x": broom["x"], "y": broom["y"]},
        "line": out_line,
    }


def sideviews_from_calibration(cal: dict | None) -> dict:
    """The long cameras as a run calibrated them -- the timeline's record of
    them (analyze._side_calibration) read back. A camera the record lacks is
    left out; one without a lateral fit can be cut from but not drawn on."""
    out = {}
    for name in SIDE_VIEWS:
        d = (cal or {}).get(name)
        if not d or "tee_row" not in d or "hog_row" not in d:
            continue
        line = d.get("centre_line")
        out[name] = SideView(
            rect=tuple(int(v) for v in d["rect"]), tee_row=float(d["tee_row"]),
            hog_row=float(d["hog_row"]), centre_col=d.get("centre_col"),
            lat_px_per_m_at_tee=d.get("lat_px_per_m_at_tee"),
            centre_line=None if line is None else (float(line[0]), float(line[1])))
    return out


def find_video(video_id: str, roots) -> Path | None:
    """The game's video in the first of ``roots`` (the main workers' caches)
    that has it whole: ``videos/<id>.mp4``, a downloaded VOD or a kept live
    recording. A partial recording under ``kept/`` is not used: where it
    starts on the stream's clock is not certain."""
    for root in roots:
        path = Path(root) / "videos" / f"{video_id}.mp4"
        if path.is_file():
            return path
    return None


def clip_rocks(rocks, video, sideviews, workdir, deliver, *, cut=practice_clips.cut,
               on_rock=None) -> dict:
    """Cut, deliver and overlay each rock's clip, one at a time.

    ``deliver(name, path) -> bool`` sends a cut clip on; the file is removed
    afterwards whatever happens. ``on_rock(name, result)`` hears of each rock
    as it is done -- the worker renews its lease there. A failed cut or
    upload costs that rock; an exception from ``deliver`` (the job is no
    longer ours) ends the lot."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    views = {name: (video, sv.rect) for name, sv in sideviews.items()}
    results = {}
    for rock in rocks:
        throw = throw_from_rock(rock)
        if throw["t_release_s"] is None:
            got = {"clip": None, "reason": "no_release"}
        else:
            plan = practice_clips.plan(throw, sideviews)
            got = ({"clip": None, "reason": "no_camera"} if plan is None
                   else _one(throw, plan, views, sideviews, workdir, deliver, cut))
        results[rock["name"]] = got
        if on_rock is not None:
            on_rock(rock["name"], got)
    return results


def _one(throw, plan, views, sideviews, workdir, deliver, cut) -> dict:
    out = workdir / f"{throw['id']}.mp4"
    began = time.monotonic()
    try:
        cut(plan, views, out)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("clip %s was not cut: %s", throw["id"], exc)
        out.unlink(missing_ok=True)
        return {"clip": None, "reason": "cut_failed"}
    try:
        sent = bool(deliver(throw["id"], out))
    finally:
        out.unlink(missing_ok=True)
    if not sent:
        return {"clip": None, "reason": "cut_failed"}
    log.info("clip %s: %.1f s of video, cut in %.1f s", throw["id"],
             plan.end - plan.pieces[0].t0, time.monotonic() - began)
    try:
        lines = practice_overlay.overlay(throw, plan, sideviews)
    except Exception as exc:  # noqa: BLE001 - the clip stands without its lines
        log.warning("overlay %s failed: %r", throw["id"], exc)
        lines = None
    return {"clip": True, "overlay": lines} if lines else {"clip": True}
