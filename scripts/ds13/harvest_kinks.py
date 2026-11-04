"""Kink wave: frames where a served delivery path kinks, for ds13e.

ds13d (the broom wave) steadied the band, but 30 fps delivery paths still kink:
on the ten games of 2026-10-12, 259 dots sit more than 1.5 cm across off a
smooth curve through their neighbours and 798 more than 15 cm along, on 352 of
1,023 deliveries. A probe of 48 of them (``~/curling-work/ds13e``) found the dot
at the box's own bottom-centre nearly every time, the box being what moved:

    hack   the read crop, behind the throwing tee: the thrower's hand on the
           handle, the slider foot, a broom resting beside the stone; the box
           takes the hand in and lets it go (width 5-30% off its neighbours),
           and its bottom edge -- the stone's distance -- wanders with it
    slide  the read crop, tee to +4 m: the same, the thrower behind the stone
    edge   the band crop's first metres (+1 to +4 m): the band's box jumps where
           the read crop's, with more of the thrower above it, holds still
    hog    the band crop at the release: the hand letting go, the slider shoe
           over the stone, sweepers' broom heads at it; now and then two boxes
           on one stone

For each game this recomputes the kinks from the served ``line.delivery``,
keeps the worst per rock in each of three depths (behind the tee, tee to +4 m,
past +4 m), cuts one short stream-copied clip per rock on the worker, and finds
which crop the kinked dot came from by matching it to a box's bottom-centre in
production's two crops (``linetime.read_delivery``'s and ``sideview.band_crop``).
That crop is what is labelled. A ``train`` game offers its worst kinks by depth
and crop, capped per game, plus ordinary confident frames so the set does not
learn only the failures; a ``heldout`` game its worst kinks and as many frames
picked at random along the delivery, never by the model's score.

Frames open with ds13d's boxes. Beyond ``MASK_HALF_M`` of the centre line the
frame is greyed out (the neighbouring sheets).

    PYTHONPATH=src ~/src/curling_score/.venv/bin/python scripts/ds13/harvest_kinks.py \\
        --games ~/curling-work/ds13e/games_kink1.json \\
        --timelines ~/curling-work/ds13e/timelines \\
        --out ~/curling-work/ds13e/wave1 \\
        --manifest datasets/ds13/manifest-kink1.json --scope ds13:kink1
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
from harvest_hack import detect, view_from  # noqa: E402
from mask_wave import inside, mask, window  # noqa: E402

from curling_score.analyze import OTHER_HOUSE
from curling_score.game import hogtime, linetime
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import band_crop

TEE_Y = C.TEE_TO_TEE_M
R = C.STONE_RADIUS_M
FPS = 30.0
KINK_ACROSS_M, KINK_ALONG_M, NEIGHBOURS = 0.015, 0.15, 3
CLIP_PAD_S = 0.5
PRELABEL_CONF = 0.20
MATCH_PX = 3.0                  # the dot is a box's bottom-centre, to ~0.1 px; eased read dots less so
DEPTHS = (("behind", -99.0, 0.0), ("near", 0.0, 4.0), ("far", 4.0, 99.0))
POOL_PER_DEPTH = 12             # kinks per game per depth sent for a clip (train)
HELDOUT_POOL_PER_DEPTH = 6
SURE_POOL = 4                   # ordinary frames per training game sent for a clip
SURE_CONF = 0.70
TRAIN_QUOTA = {"hack": 40, "slide": 15, "edge": 20, "hog": 35, "sure": 10}
TRAIN_CAP_PER_GAME = 8          # of any one region
HELDOUT_KINKS, HELDOUT_RANDOM = 10, 5    # per held-out game
MASK_HALF_M = {"read": 1.0, "band": 1.8}  # the hack wave's and the broom wave's

HELP = (
    '<p class="muted"><b>This wave is the delivery, where its dots jump.</b> The model\'s box round '
    "the thrown stone changes size from one frame to the next where something touches or covers the "
    "stone -- the thrower's hand on the handle, the slider foot, a broom resting beside it at the hack, "
    "the hand letting go at the release, a sweeper's broom head or shoe -- and the delivery chart steps "
    "sideways with it. <b>Box the stone alone</b>: its own edges, not the hand, the broom, the pad or the "
    "shoe, even where they touch or overlap it -- the handle is part of the stone, a hand on it is not; "
    "where the stone is hidden, box where it is. <b>The bottom edge matters most</b>: it is where the "
    "stone sits on the ice, and the chart reads the stone's distance from it. One stone, one box. "
    "The grey sides are masked (the neighbouring sheets); nothing in the grey is boxed or counted. "
    "Box every stone in the clear part. Drag a box to move it, a corner to resize, <b>Delete</b> to "
    "remove. <b>R</b>/<b>Y</b> arm red/yellow; click a stone and SAM finds its edges -- check SAM did "
    "not take the hand or broom with it. Mark each frame reviewed with <b>space</b>; only reviewed "
    "frames count. <b>N</b>/<b>P</b> move between frames; <b>Save to server</b> writes the session "
    "beside the images.</p>")


def kinks_of(delivery):
    """Each kinked dot of a served delivery as (t_rel, yp, dx_m, dy_m): off a
    quadratic through its NEIGHBOURS either side by more than KINK_ACROSS_M
    across or KINK_ALONG_M along, where those span half a second or less (the
    30 fps run, not the collapsed rest)."""
    a = np.array(delivery, float)
    if len(a) < 2 * NEIGHBOURS + 1:
        return []
    a = a[np.argsort(a[:, 0])]
    out = []
    for i in range(NEIGHBOURS, len(a) - NEIGHBOURS):
        nb = np.r_[a[i - NEIGHBOURS:i], a[i + 1:i + NEIGHBOURS + 1]]
        if np.ptp(nb[:, 0]) > 0.5:
            continue
        dx = a[i, 2] - np.polyval(np.polyfit(nb[:, 0], nb[:, 2], 2), a[i, 0])
        dy = a[i, 1] - np.polyval(np.polyfit(nb[:, 0], nb[:, 1], 2), a[i, 0])
        if abs(dx) > KINK_ACROSS_M or abs(dy) > KINK_ALONG_M:
            out.append((float(a[i, 0]), float(TEE_Y - a[i, 1]), float(dx), float(dy)))
    return out


def depth_of(yp):
    return next(name for name, lo, hi in DEPTHS if lo <= yp < hi)


def score(k):
    return max(abs(k[2]) / KINK_ACROSS_M, abs(k[3]) / KINK_ALONG_M)


def dot_pixel(view, yp, x):
    """The view pixel a served dot was read from: its box's bottom-centre."""
    row_c = view.row_for(yp)
    return (view.centre_col_at(row_c) - x * view.lateral_px_per_m(row_c), view.row_for(yp + R))


def crops_for(view, height):
    top = max(0, int(view.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30)
    bot = min(height, int(view.row_for(linetime.DELIVERY_TO_M + R)) + 30)
    btop, bbot = band_crop(view)
    return {"read": (top, bot), "band": (max(0, btop), min(height, bbot))}


def cut_clips(worker, remote_dir, video_id, spans, local_dir, tag):
    """One stream-copied clip per span (name, lo, hi), cut at the last keyframe
    before ``lo`` so the clip's offset is exact; {name: offset}. Cut in a
    throwaway worker container, the only place the GPU box has ffmpeg."""
    rocks_in = "\n".join(f"{n} {lo:.2f} {hi - lo + 6.0:.1f}" for n, lo, hi in spans)
    script = f"""set -e; mkdir -p {remote_dir}/{tag}; cd {remote_dir}/{tag}
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
    subprocess.run(["rsync", "-a", f"{worker}:{remote_dir}/{tag}/", str(local_dir) + "/"], check=True)
    subprocess.run(["ssh", worker, "docker run --rm -v /data/wdd:/data/wdd --entrypoint rm "
                    f"curling-worker:local -rf {remote_dir}/{tag}"], check=True)
    return offsets


def candidates(doc, role, rng):
    """[(name, end, shot, kind, t_rel, yp, x, dx, dy)]: per rock, its worst kink
    at each depth (all kinks ranked, capped per depth); for a training game a
    few ordinary dots from rocks with none, for a held-out one random dots."""
    kinked, plain, every = [], [], []
    for gm in doc["games"]:
        for e in gm["ends"]:
            for s in e["shots"]:
                dl = (s.get("line") or {}).get("delivery") or []
                if s.get("missing") or s.get("t_release_s") is None or len(dl) < 2 * NEIGHBOURS + 1:
                    continue
                name = f"e{e['number']}_r{s['number']:02d}"
                xs = {round(p[0], 3): p[2] for p in dl}
                ks = kinks_of(dl)
                best = {}
                for k in ks:
                    d = depth_of(k[1])
                    if d not in best or score(k) > score(best[d]):
                        best[d] = k
                for d, k in best.items():
                    kinked.append((score(k), d, (name, e, s, "kink", k[0], k[1], xs[round(k[0], 3)], k[2], k[3])))
                run = [p for p in dl[NEIGHBOURS:-NEIGHBOURS]]
                pick = rng.choice(run)
                row = (name, e, s, "plain", pick[0], TEE_Y - pick[1], pick[2], 0.0, 0.0)
                every.append(row)
                if not ks:
                    plain.append(row)
    per = POOL_PER_DEPTH if role == "train" else HELDOUT_POOL_PER_DEPTH
    out = []
    for d, _lo, _hi in DEPTHS:
        ranked = sorted((c for c in kinked if c[1] == d), key=lambda c: -c[0])
        out += [c[2] for c in ranked[:per]]
    rng.shuffle(plain); rng.shuffle(every)
    out += plain[:SURE_POOL] if role == "train" else every[:HELDOUT_RANDOM * 2]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--timelines", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--weights", default="weights/ds13d.pt")
    ap.add_argument("--worker", default="curling@100.85.240.114")
    ap.add_argument("--remote-dir", default="/data/wdd/scratch/ds13kink")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    from ultralytics import YOLO
    from curling_score.detect import longview
    from curling_score.harvest.candidates import stem_for
    from curling_score.train import boxedit

    out = Path(args.out).expanduser(); (out / "images").mkdir(parents=True, exist_ok=True)
    work = out / "_clips"
    model = YOLO(str(Path(args.weights).expanduser()))
    games = json.loads(Path(args.games).expanduser().read_text())
    found = []                       # every candidate placed in a crop, with its frame
    for g in games:
        vid, role, sid = g["video_id"], g["role"], g["source_id"]
        doc = json.loads((Path(args.timelines).expanduser() / f"{sid}.json").read_text())
        views = {n: view_from(doc["calibration"][n]) for n in ("left", "right")}
        rng = random.Random(f"{args.seed}:{sid}")
        cands = candidates(doc, role, rng)
        spans = {}
        for c in cands:
            t = c[2]["t_release_s"] + c[4]
            lo, hi = spans.get(c[0], (t, t))
            spans[c[0]] = (min(lo, t), max(hi, t))
        offsets = cut_clips(args.worker, args.remote_dir, vid,
                            [(n, lo - CLIP_PAD_S, hi + CLIP_PAD_S) for n, (lo, hi) in spans.items()],
                            work / sid, sid) if spans else {}
        n_placed = 0
        for name, e, s, kind, t_rel, yp, x, dx, dy in cands:
            clip = work / sid / f"clip_{name}.mp4"
            if name not in offsets or not clip.exists():
                continue
            vname = hogtime.CAMERA_FOR[OTHER_HOUSE[e["house"]]]
            view = views[vname]
            t = s["t_release_s"] + t_rel
            off = offsets[name]
            fr, ts = longview.decode(str(clip), view.rect, t - 0.1 - off, t + 0.1 - off, FPS)
            if not len(fr):
                continue
            ts = [q + off for q in ts]
            col, row = dot_pixel(view, yp, x)
            cls = 0 if s["color"] == "red" else 1
            best = None              # (distance px, frame index, crop, (top, bot), rock box, boxes)
            for crop, (top, bot) in crops_for(view, fr.shape[1]).items():
                if not top <= row <= bot:
                    continue
                per = detect(model, [f[top:bot] for f in fr], top, view)
                for i, boxes in enumerate(per):
                    if abs(ts[i] - t) > 1 / 20:
                        continue
                    for q in boxes:
                        if q["cls"] != cls:
                            continue
                        dist = float(np.hypot((q["x0"] + q["x1"]) / 2 - col, q["y1"] - row))
                        # both crops place the dot: production read it from the read crop until the band
                        tie = 0.0 if (crop == "band") == (yp >= 4.0) else 0.01
                        if best is None or dist + tie < best[0]:
                            best = (dist + tie, i, crop, (top, bot), q, boxes)
            if best is None or best[0] > MATCH_PX:
                continue
            dist, i, crop, (top, bot), rock, boxes = best
            if kind == "kink":
                region = ("hack" if yp < 0 else "slide") if crop == "read" else ("edge" if yp < 4.0 else "hog")
            else:
                region = "sure" if role == "train" else "random"
                if role == "train" and rock["conf"] < SURE_CONF:
                    continue
            ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(fr[i][top:bot], cv2.COLOR_RGB2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, 92])
            assert ok
            found.append(dict(g=g, e=e, s=s, name=name, region=region, crop=crop, view=view, vname=vname,
                              top=top, H=bot - top, W=fr.shape[2], t=ts[i], yp=yp, x=x, dx=dx, dy=dy,
                              dist=dist, rock=rock, boxes=boxes, jpg=jpg.tobytes(),
                              score=max(abs(dx) / KINK_ACROSS_M, abs(dy) / KINK_ALONG_M)))
            n_placed += 1
            del fr
            print(f"\r{sid}: {name} {region:6s} ({n_placed} placed)      ", end="", flush=True)
        shutil.rmtree(work / sid, ignore_errors=True)
        print(f"\r{sid} ({role}): {len(cands)} candidates, {n_placed} placed in a crop          ", flush=True)
    shutil.rmtree(work, ignore_errors=True)

    # the picks: train by region quota, capped per game; held-out per game
    rng = random.Random(args.seed)
    chosen = []
    train = [f for f in found if f["g"]["role"] == "train"]
    short = 0
    for region, quota in TRAIN_QUOTA.items():
        pool = [f for f in train if f["region"] == region]
        if region == "sure":
            rng.shuffle(pool)
        else:
            pool.sort(key=lambda f: -f["score"])
        per_game, got = {}, []
        for f in pool:
            sid = f["g"]["source_id"]
            if per_game.get(sid, 0) >= TRAIN_CAP_PER_GAME:
                continue
            per_game[sid] = per_game.get(sid, 0) + 1
            got.append(f)
            if len(got) == quota:
                break
        short += quota - len(got)
        chosen += got
    if short:                        # a region that ran out: the next worst kinks, any region
        used = {id(f) for f in chosen}
        rest = sorted((f for f in train if id(f) not in used and f["region"] != "sure"), key=lambda f: -f["score"])
        chosen += rest[:short]
    for g in games:
        if g["role"] != "heldout":
            continue
        mine = [f for f in found if f["g"] is g]
        kinks = sorted((f for f in mine if f["region"] != "random"), key=lambda f: -f["score"])
        rnd = [f for f in mine if f["region"] == "random"]
        rng.shuffle(rnd)
        chosen += kinks[:HELDOUT_KINKS] + rnd[:HELDOUT_RANDOM]

    items, manifest, seen = [], [], set()
    for f in chosen:
        view, top, H, W = f["view"], f["top"], f["H"], f["W"]
        stem = stem_for(f["g"]["video_id"], f["vname"], round(f["t"], 3))
        if stem in seen:             # one rock's two kinks in one frame
            continue
        seen.add(stem)
        cls = 0 if f["s"]["color"] == "red" else 1
        boxes = [q for q in f["boxes"] if q["conf"] >= PRELABEL_CONF]
        if not any(q is f["rock"] for q in boxes):
            boxes.append({**f["rock"], "cls": cls})
        rows = []
        for q in boxes:
            x0, x1 = max(0.0, q["x0"]), min(W, q["x1"]); y0, y1 = max(0.0, q["y0"] - top), min(H, q["y1"] - top)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            rows.append([int(q["cls"]), round((x0 + x1) / 2 / W, 6), round((y0 + y1) / 2 / H, 6),
                         round((x1 - x0) / W, 6), round((y1 - y0) / H, 6)])
        half_m = MASK_HALF_M[f["crop"]]
        lo, hi = window(view, top, H, W, half_m)
        img = cv2.imdecode(np.frombuffer(f["jpg"], np.uint8), cv2.IMREAD_COLOR)
        jpg = cv2.imencode(".jpg", mask(img, lo, hi), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        rows = [b for b in rows if inside(b, lo, hi, W, H)]
        (out / "images" / f"{stem}.jpg").write_bytes(jpg)
        items.append({"stem": stem, "image": f"images/{stem}.jpg", "width": W, "height": H, "boxes": rows,
                      "geom": boxedit.frame_geometry(view, longview.STONE_WIDTH_AT_HOG_PX, row_offset=top)})
        e, s, q = f["e"], f["s"], f["rock"]
        manifest.append({"stem": stem, "video_id": f["g"]["video_id"], "source_id": f["g"]["source_id"],
                         "role": f["g"]["role"], "pick": f["region"], "crop": f["crop"], "view": f["vname"],
                         "t_abs": round(f["t"], 3), "t_release": s["t_release_s"], "end": e["number"],
                         "shot": s["number"], "color": s["color"], "yp": round(f["yp"], 3),
                         "dot_x": round(f["x"], 4), "kink_dx_cm": round(f["dx"] * 100, 2),
                         "kink_dy_cm": round(f["dy"] * 100, 1), "dot_to_box_px": round(f["dist"], 2),
                         "rock_conf": round(q["conf"], 3),
                         "rock_box": [round(q[k], 2) for k in ("x0", "y0", "x1", "y1")],
                         "crop_top": top, "width": W, "height": H,
                         "rect": list(view.rect), "tee_row": view.tee_row, "hog_row": view.hog_row,
                         "centre_col": view.centre_col, "lat_px_per_m_at_tee": view.lat_px_per_m_at_tee,
                         "centre_line": list(view.centre_line) if view.centre_line else None,
                         "proposed": True, "mask_half_m": half_m})
    (out / "items.json").write_text(json.dumps(items))
    Path(args.manifest).write_text(json.dumps(manifest, indent=1) + "\n")
    page = boxedit.render(items, out, scope=args.scope, proposals=True,
                          title="The delivery, where its dots jump")
    html, k = re.subn(r'<p class="muted">.*?</p>', HELP, page.read_text(), count=1, flags=re.S)
    assert k == 1
    page.write_text(html)
    tally = {}
    for m in manifest:
        key = f"{m['role']}:{m['pick']}"
        tally[key] = tally.get(key, 0) + 1
    print(f"\n{len(items)} frames {dict(sorted(tally.items()))}; page {page}; manifest {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
