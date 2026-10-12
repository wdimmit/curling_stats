"""Broom wave: band frames where a broom head or a shoe touches the thrown rock.

ds13c draws its box around the stone AND whatever touches it -- a sweeper's
broom head, a shoe -- for three to five frames at a time: the box widens 4-6
px over the stone's ~51, its centre stalls, and snaps back 2-4 px (1.5-2 cm)
when it lets go. At 30 fps every such frame is a dot in the delivery chart,
which then steps sideways (lJREEPDFNbE e5 r15 at 3.1-3.2 s, e6 r6 at 2.8 s;
see the 2026-10-12 investigation). The cure is boxes drawn round the stone
alone in exactly those frames.

For each game this cuts one stream-copied clip per scanned rock on the worker,
follows the thrown rock through the band (the tee to past the hog line, as
``sidepool.band_crop`` -- the crop hogtime reads) at 30 fps, and measures each
frame's box against the rock's own running median size. A frame whose box is
WIDE_RATIO or more over it, in width or height, is a widening; one frame per
widening episode is offered, the widest.

A ``train`` game takes up to TRAIN_WIDE widened frames (one a delivery) and
TRAIN_SURE ordinary confident ones, so the set does not learn only the
failures. A ``heldout`` game takes HELDOUT_WIDE widened frames and as many
picked at random along the band, never by the model's score, so it measures
the model rather than echoing ds13c.

Frames open with the model's boxes. Everything beyond ``--mask-half-m`` of the
centre line is greyed out (the neighbouring sheets): the rock and its sweepers
are inside it.

    ./.venv/bin/python scripts/ds13/harvest_broom.py \\
        --games ~/curling-work/ds13broom/games_broom1.json \\
        --timelines ~/curling-work/ds13broom/timelines \\
        --out ~/curling-work/ds13broom/wave1 \\
        --manifest datasets/ds13/manifest-broom1.json --scope ds13:broom1
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harvest_hack import detect, follow, iou, jpeg, view_from  # noqa: E402
from mask_wave import inside, mask, window  # noqa: E402

from curling_score.analyze import OTHER_HOUSE
from curling_score.game import hogtime
from curling_score.geometry import constants as C
from curling_score.harvest import sidepool

TEE_Y = C.TEE_TO_TEE_M
FPS = 30.0
WINDOW_S = (0.5, 5.0)           # after t_release_s: the band, the tee to past the hog line
PRELABEL_CONF = 0.20
MEDIAN_HALF = 9                 # frames either side for the rock's own size (0.3 s)
WIDE_RATIO = 1.08
EPISODE_GAP = 2                 # frames apart that are still one widening
SURE_CONF, SURE_RATIO = 0.70, 1.02
TRAIN_WIDE, TRAIN_SURE = 9, 3
HELDOUT_WIDE, HELDOUT_RANDOM = 5, 5

HELP = (
    '<p class="muted"><b>This wave is the band, with sweepers at the rock.</b> The model draws '
    "its box round the stone <i>and</i> a broom head or shoe touching it, which moves the stone "
    "sideways in the delivery chart. <b>Box the stone alone</b>: its own edges, not the broom, the "
    "pad, the shoe or the thrower's hand, even where they touch or overlap it -- the handle is part "
    "of the stone, a hand on it is not; where the stone is hidden, box where it is. The grey sides are masked (the neighbouring sheets); nothing in the grey is boxed or "
    "counted. Box every stone in the clear part. Drag a box to move it, a corner to resize, "
    "<b>Delete</b> to remove. <b>R</b>/<b>Y</b> arm red/yellow; click a stone and SAM finds its "
    "edges -- check SAM did not take the broom with it. Mark each frame reviewed with "
    "<b>space</b>; only reviewed frames count. <b>N</b>/<b>P</b> move between frames; "
    "<b>Save to server</b> writes the session beside the images.</p>")


def cut_clips(worker, remote_dir, video_id, rocks, local_dir):
    """One stream-copied clip per rock, cut at the last keyframe before its
    window so the clip's offset is exact. Returns {name: offset}. The video is
    in whichever worker cache holds it; the cutting runs in a throwaway worker
    container, the only place the GPU box has ffmpeg."""
    lines = []
    for name, t_rel in rocks:
        want = t_rel + WINDOW_S[0] - 0.5
        dur = (t_rel + WINDOW_S[1] + 0.5) - want + 6.0          # keyframe slack
        lines.append(f"{name} {want:.2f} {dur:.1f}")
    rocks_in = "\n".join(lines)
    script = f"""set -e; mkdir -p {remote_dir}/{video_id}; cd {remote_dir}/{video_id}
V=$(ls /data/wdd/curling-cache*/videos/{video_id}.mp4 | head -1)
while read name want dur; do
  lo=$(awk -v w=$want 'BEGIN{{printf "%.2f", (w>9?w-9:0)}}')
  kf=$(ffprobe -v error -select_streams v:0 -skip_frame nokey -show_entries frame=pts_time \\
       -read_intervals ${{lo}}%${{want}} -of csv=p=0 $V < /dev/null | awk -v w=$want '$1<=w+0.001' | tail -1)
  nice -n 19 ffmpeg -nostdin -hide_banner -loglevel error -y -ss $kf -i $V -t $dur -c copy -an clip_$name.mp4
  echo "$name $kf"
done <<'ROCKS'
{rocks_in}
ROCKS
"""
    container = ["docker", "run", "--rm", "-i", "-v", "/data/wdd:/data/wdd", "--entrypoint", "bash",
                 "curling-worker:local", "-s"]
    out = subprocess.run(["ssh", worker, " ".join(container)], input=script,
                         capture_output=True, text=True, check=True).stdout
    offsets = {ln.split()[0]: float(ln.split()[1]) for ln in out.splitlines() if ln.strip()}
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", f"{worker}:{remote_dir}/{video_id}/", str(local_dir) + "/"], check=True)
    subprocess.run(["ssh", worker, "docker run --rm -v /data/wdd:/data/wdd --entrypoint rm "
                    f"curling-worker:local -rf {remote_dir}/{video_id}"], check=True)
    return offsets


def track_band(per, times, shot, cls):
    """{frame index: detection} for the thrown rock through the band, anchored
    on production's hog_path -- certainly the thrown rock -- and followed both
    ways; {} without one."""
    hp = (shot.get("line") or {}).get("hog_path") or []
    if not hp:
        return {}
    ay, ax = hp[0]
    ayp = TEE_Y - ay
    cands = [(i, q) for i, p in enumerate(per) for q in p if q["cls"] == cls
             and abs(q["yp"] - ayp) < 0.5 and abs(q["x"] - ax) < 0.08]
    if not cands:
        return {}
    i0, a = min(cands, key=lambda iq: abs(iq[1]["yp"] - ayp) + 3 * abs(iq[1]["x"] - ax))
    return {**follow(per, times, cls, i0, a, 2.0, -1), i0: a, **follow(per, times, cls, i0, a, 2.0, +1)}


def frames_of(track, times):
    """Each followed frame as a candidate, with how far its box is over the
    rock's own running median size: ``ratio`` = the larger of width and
    height against theirs."""
    idx = sorted(track)
    if len(idx) < 2 * MEDIAN_HALF:
        return []
    w = np.array([track[i]["x1"] - track[i]["x0"] for i in idx])
    h = np.array([track[i]["y1"] - track[i]["y0"] for i in idx])
    out = []
    for k, i in enumerate(idx):
        lo, hi = max(0, k - MEDIAN_HALF), min(len(idx), k + MEDIAN_HALF + 1)
        rw, rh = w[k] / np.median(w[lo:hi]), h[k] / np.median(h[lo:hi])
        q = track[i]
        out.append(dict(i=i, t=times[i], x=q["x"], yp=q["yp"], conf=q["conf"],
                        ratio=float(max(rw, rh)), ratio_w=float(rw), ratio_h=float(rh), box=q))
    return out


def widest_of_episodes(cands):
    """One candidate per run of widened frames: the widest."""
    wide = [c for c in cands if c["ratio"] >= WIDE_RATIO]
    episodes, cur = [], []
    for c in wide:
        if cur and c["i"] - cur[-1]["i"] > EPISODE_GAP:
            episodes.append(cur)
            cur = []
        cur.append(c)
    if cur:
        episodes.append(cur)
    return [max(e, key=lambda c: c["ratio"]) for e in episodes]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--timelines", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--weights", default="weights/ds13c.pt")
    ap.add_argument("--worker", default="curling@100.85.240.114")
    ap.add_argument("--remote-dir", default="/data/wdd/scratch/ds13broom")
    ap.add_argument("--scan-train", type=int, default=32, help="rocks scanned per training game")
    ap.add_argument("--scan-heldout", type=int, default=24)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--mask-half-m", type=float, default=1.8)
    args = ap.parse_args()

    from ultralytics import YOLO
    from curling_score.detect import longview
    from curling_score.harvest.sideframes import stem_for
    from curling_score.train import boxedit

    out = Path(args.out).expanduser(); (out / "images").mkdir(parents=True, exist_ok=True)
    work = out / "_clips"
    model = YOLO(str(Path(args.weights).expanduser()))
    games = json.loads(Path(args.games).expanduser().read_text())
    items, manifest = [], []
    for g in games:
        vid, role = g["video_id"], g["role"]
        doc = json.loads((Path(args.timelines).expanduser() / f"{g['source_id']}.json").read_text())
        cal = doc["calibration"]
        views = {n: view_from(cal[n]) for n in ("left", "right")}
        rocks = [(e, s) for gm in doc["games"] for e in gm["ends"] for s in e["shots"]
                 if not s.get("missing") and s.get("t_release_s") is not None
                 and (s.get("line") or {}).get("hog_path")]
        rng = random.Random(f"{args.seed}:{vid}")
        rng.shuffle(rocks)
        rocks = sorted(rocks[:args.scan_train if role == "train" else args.scan_heldout],
                       key=lambda es: es[1]["t_release_s"])
        names = {f"e{e['number']}_r{s['number']:02d}": (e, s) for e, s in rocks}
        offsets = cut_clips(args.worker, args.remote_dir, vid,
                            [(n, s["t_release_s"]) for n, (e, s) in names.items()], work / vid)
        per_delivery = {}              # name -> (candidates, context)
        for n, (e, s) in names.items():
            vname = hogtime.CAMERA_FOR[OTHER_HOUSE[e["house"]]]
            view = views[vname]
            top, bot = sidepool.band_crop(view)
            t0 = s["t_release_s"] + WINDOW_S[0]; t1 = s["t_release_s"] + WINDOW_S[1]
            clip = work / vid / f"clip_{n}.mp4"
            if n not in offsets or not clip.exists():
                continue
            fr, ts = longview.decode(str(clip), view.rect, t0 - offsets[n], t1 - offsets[n], FPS)
            if not len(fr):
                continue
            ts = [t + offsets[n] for t in ts]
            bot = min(fr.shape[1], bot)
            per = detect(model, [f[top:bot] for f in fr], top, view)
            cls = 0 if s["color"] == "red" else 1
            track = track_band(per, ts, s, cls)
            cands = frames_of(track, ts)
            ctx = dict(e=e, s=s, view=view, vname=vname, top=top, H=bot - top, W=fr.shape[2])
            # keep only what a pick could need: the frames as jpgs, not the decoded clip
            keep = {c["i"] for c in widest_of_episodes(cands)} | {c["i"] for c in cands
                                                                    if c["ratio"] <= SURE_RATIO}
            ctx["jpg"] = {i: jpeg(fr[i], top, bot) for i in keep}
            ctx["per"] = {i: per[i] for i in keep}
            per_delivery[n] = (cands, ctx)
            del fr
            print(f"\r{vid}: {n} ({len(track)} frames followed, "
                  f"{len(widest_of_episodes(cands))} widenings)    ", end="", flush=True)
        # the picks: one frame per delivery
        chosen, used = [], set()
        wide = [(n, c) for n, (cs, _) in per_delivery.items() for c in widest_of_episodes(cs)]
        wide.sort(key=lambda nc: -nc[1]["ratio"])
        want_wide = TRAIN_WIDE if role == "train" else HELDOUT_WIDE
        for n, c in wide:
            if n in used:
                continue
            chosen.append(("wide", n, c)); used.add(n)
            if len([x for x in chosen if x[0] == "wide"]) >= want_wide:
                break
        if role == "train":
            pool = [(n, c) for n, (cs, _) in per_delivery.items() for c in cs
                    if n not in used and c["conf"] >= SURE_CONF and c["ratio"] <= SURE_RATIO]
            rng.shuffle(pool)
            for n, c in pool:
                if n in used:
                    continue
                chosen.append(("sure", n, c)); used.add(n)
                if len([x for x in chosen if x[0] == "sure"]) >= TRAIN_SURE:
                    break
        else:
            pool = [(n, c) for n, (cs, ctx) in per_delivery.items() for c in cs
                    if n not in used and c["i"] in ctx["jpg"]]
            rng.shuffle(pool)
            for n, c in pool:
                if n in used:
                    continue
                chosen.append(("random", n, c)); used.add(n)
                if len([x for x in chosen if x[0] == "random"]) >= HELDOUT_RANDOM:
                    break
        for cat, n, f in chosen:
            ctx = per_delivery[n][1]
            view, top, H, W = ctx["view"], ctx["top"], ctx["H"], ctx["W"]
            boxes = [q for q in ctx["per"][f["i"]] if q["conf"] >= PRELABEL_CONF]
            rock = {**f["box"], "cls": 0 if ctx["s"]["color"] == "red" else 1}
            if not any(iou(rock, q) > 0.4 for q in boxes):
                boxes.append(rock)
            rows = []
            for q in boxes:
                x0, x1 = max(0.0, q["x0"]), min(W, q["x1"]); y0, y1 = max(0.0, q["y0"] - top), min(H, q["y1"] - top)
                if x1 - x0 < 2 or y1 - y0 < 2:
                    continue
                rows.append([int(q["cls"]), round((x0 + x1) / 2 / W, 6), round((y0 + y1) / 2 / H, 6),
                             round((x1 - x0) / W, 6), round((y1 - y0) / H, 6)])
            t_abs = round(f["t"], 3)
            stem = stem_for(vid, ctx["vname"], t_abs)
            jpg = ctx["jpg"][f["i"]]
            if args.mask_half_m > 0:
                lo, hi = window(view, top, H, W, args.mask_half_m)
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                jpg = cv2.imencode(".jpg", mask(img, lo, hi), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
                rows = [b for b in rows if inside(b, lo, hi, W, H)]
            (out / "images" / f"{stem}.jpg").write_bytes(jpg)
            items.append({"stem": stem, "image": f"images/{stem}.jpg", "width": W, "height": H,
                          "boxes": rows,
                          "geom": boxedit.frame_geometry(view, longview.STONE_WIDTH_AT_HOG_PX, row_offset=top)})
            e, s = ctx["e"], ctx["s"]
            manifest.append({"stem": stem, "video_id": vid, "source_id": g["source_id"], "role": role,
                             "pick": cat, "view": ctx["vname"], "t_abs": t_abs, "t_release": s["t_release_s"],
                             "end": e["number"], "shot": s["number"], "color": s["color"],
                             "rock_x": round(f["x"], 4), "rock_yp": round(f["yp"], 3),
                             "rock_conf": round(f["conf"], 3), "box_ratio": round(f["ratio"], 3),
                             "box_ratio_w": round(f["ratio_w"], 3), "box_ratio_h": round(f["ratio_h"], 3),
                             "rock_box": [round(f["box"][k], 2) for k in ("x0", "y0", "x1", "y1")],
                             "crop_top": top, "width": W, "height": H,
                             "rect": list(view.rect), "tee_row": view.tee_row, "hog_row": view.hog_row,
                             "centre_col": view.centre_col, "lat_px_per_m_at_tee": view.lat_px_per_m_at_tee,
                             "centre_line": list(view.centre_line) if view.centre_line else None,
                             "proposed": True, "mask_half_m": args.mask_half_m or None})
        shutil.rmtree(work / vid, ignore_errors=True)
        cats = {}
        for cat, _n, _f in chosen:
            cats[cat] = cats.get(cat, 0) + 1
        print(f"\r{vid} ({role}): {len(names)} rocks scanned, {len(per_delivery)} followed, "
              f"{len(wide)} widenings, kept {len(chosen)}: {cats}                ", flush=True)

    shutil.rmtree(work, ignore_errors=True)
    (out / "items.json").write_text(json.dumps(items))
    Path(args.manifest).write_text(json.dumps(manifest, indent=1) + "\n")
    page = boxedit.render(items, out, scope=args.scope, proposals=True,
                          title="Stones in the band, sweepers at the rock")
    html, k = re.subn(r'<p class="muted">.*?</p>', HELP, page.read_text(), count=1, flags=re.S)
    assert k == 1
    page.write_text(html)
    roles = {}
    for m in manifest:
        roles[m["role"]] = roles.get(m["role"], 0) + 1
    print(f"\n{len(items)} frames {roles}; page {page}; manifest {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
