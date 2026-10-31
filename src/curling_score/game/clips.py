"""A claimed game rock's clip: the practice clip, cut from the game's video.

The clips worker (service/clipworker.py) is handed each rock as the game's
timeline has it (service/gameclips.player_rocks) and the cameras as the run
calibrated them. A rock is turned into the practice throw record, so the
practice cutter plans its pieces -- the camera facing the thrower, then the one
behind -- and the practice overlay places the broom and lines in each, which
game/clipburn.py burns into the picture with the delivery's dots. A game clip
then trades the camera behind the thrower for the overhead camera above the
house the rock was thrown to (`plan`): the rock coming in, and every stone it
meets, from above.

A game's times are on its video's own clock, so the cut needs no offset. The
far hog line is the camera switch; timelines from before it was recorded
switch at the rock's arrival in the far panel instead, a little early.
"""

import logging
import subprocess
import time
from pathlib import Path

from curling_score.game import clipburn
from curling_score.game.hogtime import CAMERA_FOR
from curling_score.geometry.sideview import SideView
from curling_score.practice import clips as practice_clips
from curling_score.practice.enrich import OTHER_HOUSE

log = logging.getLogger(__name__)

SIDE_VIEWS = ("left", "right")
PANELS = ("top", "bottom")
# The piece from the overhead camera, as a plan names it.
OVERHEAD = "overhead"
# It begins this long before the rock enters the panel, so it is seen coming in.
OVERHEAD_LEAD_S = 0.5
# Shorter than this from the overhead camera is not worth the cut to it.
OVERHEAD_MIN_S = 1.0
# How a clip is cut, as its result says: 2, the arrival from overhead. The
# API asks again for a clip older than its own word on this
# (service/gameclips.CLIP_STYLE, which must agree).
CLIP_STYLE = 2


def _release_timed(rock: dict) -> bool:
    """A rock the overhead never followed in (detect.release.RELEASE_REASONS)
    is timed by its release: its t_enter is that, and no arrival."""
    enter = rock.get("t_enter_s")
    return enter is not None and enter == rock.get("t_release_s")


def throw_from_rock(rock: dict) -> dict:
    """``rock`` as the practice cutter and overlay read a throw."""
    line = rock.get("line") or {}
    broom = rock.get("target_broom")
    t_far = rock.get("t_far_s")
    if t_far is None and not _release_timed(rock):
        t_far = rock.get("t_enter_s")
    out_line = None
    if line:
        out_line = {"at_hog_x": (line.get("at_hog") or {}).get("x"),
                    "miss_m": (line.get("at_broom") or {}).get("miss_m"),
                    "aim_x": (line.get("at_tee") or {}).get("x"),
                    "start": line.get("start"), "delivery": line.get("delivery"),
                    # [t, y, x]: where the hand let go; null when it was not read.
                    "release": line.get("release")}
    return {
        "id": rock["name"], "house": rock["house"],
        "t_release_s": rock.get("t_release_s"), "t_rest_s": rock.get("t_rest_s"),
        "arrived": rock.get("t_enter_s") is not None,
        "t_hog_s": rock.get("t_hog_s"), "v_hog_m_s": rock.get("v_hog_m_s"), "t_far_s": t_far,
        "t_enter_s": rock.get("t_enter_s"),
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


def panels_from_calibration(cal: dict | None) -> dict:
    """The overhead panels, by house, as a run calibrated them; a house the
    record lacks is left out, and its clips keep the camera behind."""
    out = {}
    for house in PANELS:
        panel = clipburn.Panel.from_calibration((cal or {}).get(house))
        if panel is not None:
            out[house] = panel
    return out


def plan(throw: dict, sideviews, panel) -> practice_clips.ClipPlan | None:
    """The practice plan, with the rock's arrival seen from ``panel``, the
    overhead camera above the house it was thrown to, in place of the camera
    behind the thrower: from just before the rock enters it (or crosses the
    far hog line, whichever is first) until the practice plan ends. The
    camera facing the thrower keeps the delivery, to where it lost the rock.
    The practice plan as it is where there is no panel, the rock never
    arrived, or no camera faces the thrower."""
    base = practice_clips.plan(throw, sideviews)
    if base is None or panel is None or not throw.get("arrived"):
        return base
    first = base.pieces[0]
    if first.view != CAMERA_FOR[OTHER_HOUSE[throw["house"]]]:
        return base
    # Cut to the panel at a release-timed t_enter and the clip watches an empty
    # house while the rock is still on its way (`_release_timed`).
    enter = None if _release_timed(throw) else throw.get("t_enter_s")
    times = [t for t in (enter, throw.get("t_far_s")) if t is not None]
    if not times:
        return base
    switch = max(first.t0, min(times) - OVERHEAD_LEAD_S)
    if base.end - switch < OVERHEAD_MIN_S:
        return base
    pieces = [practice_clips.Piece(first.view, first.t0, min(first.t1, switch))] \
        if switch > first.t0 else []
    pieces.append(practice_clips.Piece(OVERHEAD, switch, base.end))
    return practice_clips.ClipPlan(tuple(pieces))


def find_video(video_id: str, roots) -> Path | None:
    """The game's video in the first of ``roots`` (the main workers' caches)
    that has it whole: ``videos/<id>.mp4``, a downloaded VOD or a kept live
    recording. Failing that, while its stream is still on, the recording a
    main worker is writing: ``live/<id>/rec.<n>.ts``, the longest of them.
    Every one of those starts from the stream's first segment (live/recorder),
    so each is on the timeline's clock, and a rock in an end already
    published is all there. A partial recording under ``kept/`` is not used:
    where it starts on the stream's clock is not certain."""
    for root in roots:
        path = Path(root) / "videos" / f"{video_id}.mp4"
        if path.is_file():
            return path
    live = []
    for root in roots:
        for path in (Path(root) / "live" / video_id).glob("rec.*.ts"):
            try:
                live.append((path.stat().st_size, path))
            except FileNotFoundError:     # the stream ended and it was filed just now
                continue
    return max(live)[1] if live else None


def clip_rocks(rocks, video, sideviews, workdir, deliver, *, panels=None, cut=clipburn.cut,
               on_rock=None) -> dict:
    """Cut, mark and deliver each rock's clip, one at a time; ``panels`` are
    the overhead cameras by house (`panels_from_calibration`).

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
            # The overhead camera over the house this rock was thrown to.
            panel = (panels or {}).get(throw["house"])
            overhead = {} if panel is None else {OVERHEAD: panel}
            cams = views if panel is None else {**views, OVERHEAD: (video, panel.rect)}
            plan_ = plan(throw, sideviews, panel)
            got = ({"clip": None, "reason": "no_camera"} if plan_ is None
                   else _one(throw, plan_, cams, sideviews, overhead, workdir, deliver, cut))
        results[rock["name"]] = got
        if on_rock is not None:
            on_rock(rock["name"], got)
    return results


def _one(throw, plan, views, sideviews, panels, workdir, deliver, cut) -> dict:
    out = workdir / f"{throw['id']}.mp4"
    began = time.monotonic()
    try:
        cut(plan, views, out, throw=throw, sideviews=sideviews, panels=panels)
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
    # Its marks are in its pixels: the page draws nothing over it.
    return {"clip": True, "burned": True, "style": CLIP_STYLE}
