#!/usr/bin/env python
"""Cut the release model's training crops from the hand marks, through the
production crop code.

The spike trained on crops cut from the marking tool's JPEGs, which were
resampled differently from what the pipeline reads. This cuts every marked
rock the way the release pass will: its span of the long camera facing the
thrower decoded by `longview.decode` at 30 fps under `longview.keeping`, the
`LaneKeeper`'s grey strips, and `releasetime.hand_crops` along the timeline's
delivery path. A model trained on these sees exactly what it will be served.

    # on the GPU box, inside the worker image, with the repo mounted:
    python scripts/release/cut.py datasets/releasemarks/s_*.json \\
        --timelines /data/wdd/scratch/body/tl --videos /data/wdd/scratch/body/videos \\
        --out /data/wdd/scratch/release/crops

Per marks file, one ``<tag>.npz`` in ``--out``:

- ``crops`` (N, 107, 160) uint8, every rock's crops end to end
- ``o``, ``yp`` (N,) float32: each crop's seconds from the release and the
  rock's metres past the throwing tee
- ``label`` (N,) int8: 0 hand on, 1 off, -1 left out (see `labels`)
- ``offs`` (rocks + 1,): rock ``i`` is ``offs[i]:offs[i + 1]``
- ``meta``: a JSON list, per rock ``id``, ``game`` (the tag), ``end``,
  ``rock``, ``color``, ``position``, ``mark``, ``mark_o``, ``mark_yp``,
  ``t_release_s`` and ``delivery`` (the path, so training can score a read
  through `releasetime.release_at` as the pipeline does)
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from curling_score.detect import lanekeep, longview
from curling_score.game import clips, hogtime, linetime, releasetime

FPS = lanekeep.SOURCE_FPS
TEE_Y = linetime.TEE_Y
OTHER_HOUSE = {"top": "bottom", "bottom": "top"}

# Frames this close to the mark are left out of training. mark_release.py
# labels its frames t0 + i/30 from the decode's start, while the keeper keys
# each by the source frame it is (the first at or after t0, then every 1/30 s),
# so a mark's o can sit up to a frame before the frame it names; two frames
# either side covers that and the person's own frame or so of doubt.
MARGIN_S = 2 / FPS + 1e-6

# The decode runs from this long before the first crop to this long after the
# path's last sample: slack, so ffmpeg's first frame is never one the crops need.
BEFORE_S = 0.3
AFTER_S = 0.1

# The 10/20 game was marked alone, before marks carried a tag.
UNTAGGED = {"s_1TH0dJNrbHeKLARuj": "T1020"}


def tag_of(marks) -> str:
    """The game's name in the folds: the marks file's tag, else a fixed name
    for the one untagged game, else the start of its source id."""
    sid = marks["source_id"]
    return marks.get("tag") or UNTAGGED.get(sid) or sid[:6]


def labels(o, mark, mark_o=None) -> np.ndarray:
    """Per crop at ``o``: 0 before ``mark_o``, 1 from it, -1 within MARGIN_S
    of it. A "held" rock is on throughout. Can't tell (None) has no labels."""
    o = np.asarray(o, float)
    if mark == "held":
        return np.zeros(len(o), np.int8)
    if mark != "off" or mark_o is None:
        raise ValueError(f"no labels for mark {mark!r}")
    lab = (o >= mark_o).astype(np.int8)
    lab[np.abs(o - mark_o) <= MARGIN_S] = -1
    return lab


def shot_of(tl, end_no, rock_no):
    """``(end, shot)`` of a rock: every marked timeline is one game."""
    end = next((e for e in tl["games"][0]["ends"] if e["number"] == end_no), None)
    shot = None if end is None else next((s for s in end["shots"] if s["number"] == rock_no), None)
    if shot is None:
        raise LookupError(f"no end {end_no} rock {rock_no} in the timeline's first game")
    return end, shot


def window(delivery, t_release) -> tuple[float, float]:
    """The source seconds to decode for one rock: from the first path sample
    no further behind the tee than the crops start, to the path's end."""
    o_from = next((t for t, y, _x in delivery if TEE_Y - y >= releasetime.FROM_M), delivery[0][0])
    return t_release + o_from - BEFORE_S, t_release + delivery[-1][0] + AFTER_S


def cut_rock(video, tl, end_no, rock_no):
    """``(crops, o, yp, shot)`` for one rock, as the release pass cuts them."""
    end, shot = shot_of(tl, end_no, rock_no)
    camera = hogtime.CAMERA_FOR[OTHER_HOUSE[end["house"]]]
    view = clips.sideviews_from_calibration(tl["calibration"])[camera]
    delivery = shot["line"]["delivery"]
    t_rel = shot["t_release_s"]
    t0, t1 = window(delivery, t_rel)
    keeper = lanekeep.LaneKeeper(view, [(t0, t1)], every=1)
    with longview.keeping(keeper):
        longview.decode(video, view.rect, t0, t1, FPS)
    strips, times = keeper.frames_between(t0, t1)
    crops, o, yp = releasetime.hand_crops(strips, times, keeper.origin, view, delivery, t_rel)
    return crops, o, yp, shot


def cut_game(marks, tl, video):
    """The arrays of one ``<tag>.npz``, printing a line per rock."""
    tag = tag_of(marks)
    crops, os_, yps, labs, meta, offs = [], [], [], [], [], [0]
    for rock in marks["rocks"]:
        mark = rock.get("mark")
        if mark is None:
            print(f"  {tag} {rock['id']}: can't tell, left out", flush=True)
            continue
        c, o, yp, shot = cut_rock(video, tl, rock["end"], rock["rock"])
        if not len(c):
            print(f"  {tag} {rock['id']}: no frames on the path, left out", flush=True)
            continue
        mark_o = None
        if mark == "off":
            # The mark's time in the video, then in this timeline's release
            # clock: the same number unless the timeline was rerun since.
            mark_o = rock["t_release_s"] + rock["o"] - shot["t_release_s"]
        lab = labels(o, mark, mark_o)
        crops.append(c)
        os_.append(o)
        yps.append(yp)
        labs.append(lab)
        offs.append(offs[-1] + len(c))
        meta.append({"id": rock["id"], "game": tag, "end": rock["end"], "rock": rock["rock"],
                     "color": rock["color"], "position": rock.get("position"), "mark": mark,
                     "mark_o": mark_o, "mark_yp": rock.get("yp") if mark == "off" else None,
                     "t_release_s": shot["t_release_s"], "delivery": shot["line"]["delivery"]})
        what = f"off at {mark_o:.3f} s, {rock.get('yp')} m" if mark == "off" else mark
        line = (f"  {tag} {rock['id']}: {what}; {len(c)} frames {yp[0]:+.2f}..{yp[-1]:+.2f} m, "
                f"on {(lab == 0).sum()} off {(lab == 1).sum()} out {(lab == -1).sum()}")
        moved = shot["t_release_s"] - rock["t_release_s"]
        if abs(moved) > 1e-3:
            # Its mark_yp is then along the path as it was when it was marked.
            line += f"  (the timeline's release moved {moved:+.3f} s since the mark)"
        print(line, flush=True)
    if not meta:
        return None
    return {"crops": np.concatenate(crops), "o": np.concatenate(os_).astype(np.float32),
            "yp": np.concatenate(yps).astype(np.float32), "label": np.concatenate(labs),
            "offs": np.array(offs), "meta": json.dumps(meta)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("marks", nargs="+", help="datasets/releasemarks/<source>.json files")
    ap.add_argument("--timelines", required=True, help="a directory of <source_id>.json")
    ap.add_argument("--videos", required=True, help="a directory of <video_id>.mp4")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    total_rocks = total_frames = 0
    for path in args.marks:
        marks = json.loads(Path(path).read_text())
        if "source_id" not in marks:
            # remarks-*.json: the same rocks marked again, to measure the marks.
            print(f"{path}: not one game's marks, skipped", flush=True)
            continue
        tl = json.loads((Path(args.timelines) / f"{marks['source_id']}.json").read_text())
        video = Path(args.videos) / f"{marks['video_id']}.mp4"
        tag = tag_of(marks)
        print(f"{tag}: {path}, {len(marks['rocks'])} rocks", flush=True)
        got = cut_game(marks, tl, video)
        if got is None:
            print(f"{tag}: nothing to write", flush=True)
            continue
        np.savez_compressed(out / f"{tag}.npz", **got)
        n_rocks, n_frames = len(got["offs"]) - 1, len(got["crops"])
        total_rocks += n_rocks
        total_frames += n_frames
        print(f"{tag}: {n_rocks} rocks, {n_frames} frames -> {out / f'{tag}.npz'}", flush=True)
    print(f"{len(args.marks)} games, {total_rocks} rocks, {total_frames} frames", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
