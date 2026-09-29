"""Hack wave: side-view frames between the hack and the tee, for ds13's next model.

Every frame ds13 was trained on is cropped to ``sidepool.band_crop`` -- the tee
to past the hog line -- so the model has never seen a stone behind the tee.
It finds one there anyway, but worse: on the 96 rocks of s_0N8Q2sB4vY8Hv4Ooq
(2026-09-28) it held 99-100% of frames past +2 m against 85-90% between the
hack and the tee, missed the stone at rest against the backboard outright on
three, and lost it at the push-off where a hand, knee or broom head sits beside
it. Production's own start is missing on 20-25% of rocks on this season's
re-aimed sheets, and on none of last season's.

For each game in ``--games`` this cuts one short clip per scanned rock on the
worker (stream copy: its GPU belongs to the live lane), follows the thrown rock
through the hog camera from the hack to past the tee, and keeps ONE frame per
delivery -- frames 0.1 s apart are the same moment twice.

A ``train`` game picks by what the model got wrong, which is where a label
changes it (harvest_wave2's reasoning), but not only that, or the set learns
only the failures:

    rest_miss     the stone at rest in front of the hack, model < 0.35 or blind
    slide_miss    moving, hack to +1.5 m, no box or < 0.35 (the box is then
                  placed from the frames either side)
    slide_unsure  moving, 0.35-0.70
    sure          >= 0.70, at rest or moving

A ``heldout`` game picks by depth alone (at rest / hack to -1 m / -1 m to
+1.5 m), never by confidence, so it measures the model rather than echoing it.

Frames open with the model's boxes (every stone >= 0.20, geometry-gated, and the
thrown rock's whatever its score). Only frames a reviewer marks reviewed count.
Everything beyond ``--mask-half-m`` of the centre line is greyed out and its
boxes dropped: the stones parked behind the far end sit there (``mask_wave.py``
says why masked, not cropped).

    ./.venv/bin/python scripts/ds13/harvest_hack.py \\
        --games ~/curling-work/ds13hack/games_hack1.json \\
        --timelines ~/curling-work/ds13hack/timelines \\
        --out ~/curling-work/ds13hack/wave1 \\
        --manifest datasets/ds13/manifest-hack1.json --scope ds13:hack1
"""
import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mask_wave import HELP_MASKED, inside, mask, window  # noqa: E402

from curling_score.analyze import OTHER_HOUSE
from curling_score.game import hogtime, linetime
from curling_score.geometry import constants as C
from curling_score.geometry import sideview

TEE_Y = C.TEE_TO_TEE_M
HACK = C.TEE_TO_HACKLINE_M
R = C.STONE_RADIUS_M
FPS = 10.0
WINDOW_S = (-3.5, 3.0)          # around t_release_s: at rest, the slide, past the tee
CROP_PAST_TEE_M = 2.0           # the frame kept for labelling ends here
FOLLOW_PAST_TEE_M = 3.8         # ...but the rock is followed to where hog_path starts
PRELABEL_CONF = 0.20
DETECT_CONF = 0.05
SURE, UNSURE = 0.70, 0.35
TRAIN_QUOTA = {"rest_miss": 3, "slide_miss": 3, "slide_unsure": 3, "sure": 3}
HELDOUT_PER_GAME = 10
REST_EPS_M = 0.15

HELP = (
    '<p class="muted"><b>This wave is the hack to just past the tee</b>, which the model '
    "was never trained on. Frames open with the model's boxes; correct them. "
    "<b>Box every stone in the frame</b>: the thrown rock, stones waiting by the hack, "
    "stones on the neighbouring sheets, and the stones stored behind the far end -- they "
    "look exactly like a stone at the hack, and leaving them unboxed would teach the model "
    "that a stone against the backboard is background. Drag a box to move it, a corner to "
    "resize, <b>Delete</b> to remove. <b>R</b>/<b>Y</b> arm red/yellow; click a stone and SAM "
    "finds its edges. A box on a stone the model missed was placed from the frames either "
    "side and may be a little off. Mark each frame reviewed with <b>space</b> -- only "
    "reviewed frames count. <b>N</b>/<b>P</b> move between frames; <b>Save to server</b> "
    "writes the session beside the images.</p>")


def view_from(entry: dict) -> sideview.SideView:
    return sideview.SideView(
        rect=tuple(entry["rect"]), tee_row=entry["tee_row"], hog_row=entry["hog_row"],
        centre_col=entry.get("centre_col"), lat_px_per_m_at_tee=entry.get("lat_px_per_m_at_tee"),
        centre_line=tuple(entry["centre_line"]) if entry.get("centre_line") else None)


def rows_for(view):
    top = int(view.row_for(-(HACK + 1.0))) - 30
    crop_bot = int(view.row_for(CROP_PAST_TEE_M + R)) + 30
    follow_bot = int(view.row_for(FOLLOW_PAST_TEE_M + R)) + 30
    return max(0, top), crop_bot, follow_bot


def cut_clips(worker, remote_dir, video_id, rocks, local_dir):
    """One stream-copied clip per rock, cut at the last keyframe before its
    window so the clip's offset is exact. Returns {name: offset}."""
    lines = []
    for name, t_rel in rocks:
        want = t_rel + WINDOW_S[0] - 0.5
        dur = (t_rel + WINDOW_S[1] + 0.5) - want + 6.0          # keyframe slack
        lines.append(f"{name} {want:.2f} {dur:.1f}")
    script = f"""set -e; mkdir -p {remote_dir}/{video_id}; cd {remote_dir}/{video_id}
V=/data/wdd/curling-cache/videos/{video_id}.mp4
while read name want dur; do
  lo=$(awk -v w=$want 'BEGIN{{printf "%.2f", (w>9?w-9:0)}}')
  kf=$(ffprobe -v error -select_streams v:0 -skip_frame nokey -show_entries frame=pts_time \\
       -read_intervals ${{lo}}%${{want}} -of csv=p=0 $V < /dev/null | awk -v w=$want '$1<=w+0.001' | tail -1)
  nice -n 19 ffmpeg -nostdin -hide_banner -loglevel error -y -ss $kf -i $V -t $dur -c copy -an clip_$name.mp4
  echo "$name $kf"
done"""
    out = subprocess.run(["ssh", worker, script], input="\n".join(lines) + "\n",
                         capture_output=True, text=True, check=True).stdout
    offsets = {ln.split()[0]: float(ln.split()[1]) for ln in out.splitlines() if ln.strip()}
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", f"{worker}:{remote_dir}/{video_id}/", str(local_dir) + "/"], check=True)
    subprocess.run(["ssh", worker, f"rm -rf {remote_dir}/{video_id}"], check=True)
    return offsets


def detect(model, crops, top, view):
    """Every box, both colours, as dicts in view pixels plus its ice position."""
    from curling_score.detect import longview
    out = []
    for i in range(0, len(crops), 16):
        for res in model.predict([c[:, :, ::-1].copy() for c in crops[i:i + 16]], imgsz=800,
                                 conf=DETECT_CONF, verbose=False):
            per = []
            if res.boxes is not None:
                for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy(),
                                    res.boxes.conf.cpu().numpy()):
                    x0, y0, x1, y1 = (float(v) for v in b)
                    y0 += top; y1 += top
                    expect = view.stone_width_at(y1, longview.STONE_WIDTH_AT_HOG_PX)
                    if not (expect > 1 and 0.5 <= (x1 - x0) / expect <= 2.0):
                        continue
                    x, _y, yp = linetime.to_destination(view, (x0 + x1) / 2, y1)
                    per.append(dict(x0=x0, y0=y0, x1=x1, y1=y1, cls=int(c), conf=float(cf), x=x, yp=yp))
            out.append(per)
    return out


def follow(per, times, cls, anchor_i, anchor, vel, direction):
    """The thrown rock frame to frame from an anchor, by position and speed, with
    capped gates -- an open-ended gate reaches the stones parked at the side."""
    got, cur, last = {}, anchor, anchor_i
    rng = range(anchor_i + direction, len(per) if direction > 0 else -1, direction)
    for i in rng:
        gap = abs(i - last)
        if gap > 12:
            break
        dt = times[i] - times[last]
        pred = cur["yp"] + vel * dt
        ok = [q for q in per[i] if q["cls"] == cls
              and abs(q["x"] - cur["x"]) <= min(0.06 + 0.04 * gap, 0.20)
              and abs(q["yp"] - pred) <= min(0.30 + 0.25 * gap, 0.90)]
        if not ok:
            continue
        nxt = min(ok, key=lambda q: 3 * abs(q["x"] - cur["x"]) + abs(q["yp"] - pred))
        vel = 0.6 * vel + 0.4 * (nxt["yp"] - cur["yp"]) / dt
        cur, last = nxt, i
        got[i] = nxt
    return got


def track_rock(per, times, shot, cls):
    """{frame index: detection} for the thrown rock, anchored on production's
    hog_path (certainly the thrown rock) and followed back to the hack; or, with
    no hog_path, forward from where it sat."""
    hp = (shot.get("line") or {}).get("hog_path") or []
    if hp:
        ay, ax = hp[0]
        ayp = TEE_Y - ay
        cands = [(i, q) for i, p in enumerate(per) for q in p if q["cls"] == cls
                 and abs(q["yp"] - ayp) < 0.5 and abs(q["x"] - ax) < 0.08]
        if cands:
            i0, a = min(cands, key=lambda iq: abs(iq[1]["yp"] - ayp) + 3 * abs(iq[1]["x"] - ax))
            back = follow(per, times, cls, i0, a, 2.0, -1)
            fwd = follow(per, times, cls, i0, a, 2.0, +1)
            return {**back, i0: a, **fwd}, "hog_path"
    st = (shot.get("line") or {}).get("start")
    if st:
        sx, syp = st["x"], TEE_Y - st["y"]
        cands = [(i, q) for i, p in enumerate(per[:15]) for q in p if q["cls"] == cls
                 and abs(q["x"] - sx) < 0.10 and abs(q["yp"] - syp) < 0.5]
        if cands:
            i0, a = min(cands, key=lambda iq: iq[0])
            return {i0: a, **follow(per, times, cls, i0, a, 0.0, +1)}, "start"
    return {}, None


def box_for(view, x, yp, top):
    """A geometric box for a stone centred at (x, yp): the page's own sizing."""
    from curling_score.detect import longview
    from curling_score.train.boxedit import HEIGHT_RATIO
    col, _row = view.to_image(-x, yp)
    y1 = view.row_for(yp + R)
    w = view.stone_width_at(y1, longview.STONE_WIDTH_AT_HOG_PX)
    return dict(x0=col - w / 2, x1=col + w / 2, y0=y1 - HEIGHT_RATIO * w, y1=y1)


def iou(a, b):
    ix = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    iy = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    inter = ix * iy
    ua = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / ua if ua > 0 else 0.0


def frames_of(track, per, times, view, top, shot, cls):
    """Each frame of this delivery as a candidate: where the thrown rock is, how
    sure the model was, and whether it is at rest or moving.

    The frames that matter most are the ones the follow never reaches: a stone
    the model is blind to at rest has no track there at all. Frames before the
    first sighting and before the push are offered at rest, placed where
    production says the stone sat -- or, with no start, behind the first
    sighting at the usual 3.25 m behind the tee -- for the reviewer to box."""
    if len(track) < 5:
        return []
    idx = sorted(track)
    ypk = np.array([track[i]["yp"] for i in idx]); xk = np.array([track[i]["x"] for i in idx])
    first_at_rest = float(np.ptp(ypk[:5])) < 0.10
    st = (shot.get("line") or {}).get("start")
    if first_at_rest:
        rest_x, rest_yp = float(np.median(xk[:5])), float(np.median(ypk[:5]))
        moving_from = next((i for i in idx if track[i]["yp"] > rest_yp + REST_EPS_M), idx[-1])
    else:
        rest_x, rest_yp = (st["x"], TEE_Y - st["y"]) if st else (float(xk[0]), -3.25)
        moving_from = idx[0]
    # before the push: t_release_s is the slide seen starting, and the stone is
    # still until about half a second before that
    t_still = min(times[moving_from] - 0.7, shot["t_release_s"] - 0.8)
    out = []
    for i in range(len(per)):
        if i < idx[0]:
            if times[i] > t_still or times[i] < times[0] + 0.5:
                continue
            near = [q for q in per[i] if q["cls"] == cls and abs(q["x"] - rest_x) < 0.15
                    and abs(q["yp"] - rest_yp) < 0.4]
            if near:
                q = max(near, key=lambda q: q["conf"])
                out.append(dict(i=i, t=times[i], x=q["x"], yp=q["yp"], conf=q["conf"], placed=False, state="rest"))
            else:
                out.append(dict(i=i, t=times[i], x=rest_x, yp=rest_yp, conf=0.0, placed=True, state="rest",
                                guessed=not first_at_rest and not st))
            continue
        if track.get(i):
            q = track[i]; conf = q["conf"]; x, yp = q["x"], q["yp"]; placed = False
        elif idx[0] < i < idx[-1]:
            x = float(np.interp(i, idx, xk)); yp = float(np.interp(i, idx, ypk)); conf = 0.0; placed = True
            # the model may have scored it under the follower's cut: take that box
            near = [q for q in per[i] if abs(q["x"] - x) < 0.08 and abs(q["yp"] - yp) < 0.3]
            if near:
                q = max(near, key=lambda q: q["conf"]); conf = q["conf"]; x, yp = q["x"], q["yp"]; placed = False
        else:
            continue
        state = "rest" if i < moving_from else "moving"
        if state == "moving" and not (-HACK - 0.5 <= yp <= 1.5):
            continue
        out.append(dict(i=i, t=times[i], x=x, yp=yp, conf=conf, placed=placed, state=state))
    return out


def jpeg(frame_rgb, top, bot):
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame_rgb[top:bot], cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return buf.tobytes()


def category(f):
    if f["state"] == "rest":
        return "rest_miss" if f["conf"] < UNSURE else "sure" if f["conf"] >= SURE else "rest_unsure"
    if f["conf"] < UNSURE:
        return "slide_miss"
    return "slide_unsure" if f["conf"] < SURE else "sure"


def depth_stratum(f):
    if f["state"] == "rest":
        return "rest"
    return "early" if f["yp"] < -1.0 else "late"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--timelines", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--weights", default="weights/ds13b.pt")
    ap.add_argument("--worker", default="administrator@10.0.0.182")
    ap.add_argument("--remote-dir", default="/data/wdd/scratch/ds13hack")
    ap.add_argument("--scan-train", type=int, default=48, help="rocks scanned per training game")
    ap.add_argument("--scan-heldout", type=int, default=20)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--mask-half-m", type=float, default=1.0,
                    help="grey out beyond this many metres of the centre line (0: no mask)")
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
                 if not s.get("missing") and s.get("t_release_s") is not None]
        rng = random.Random(f"{args.seed}:{vid}")
        rng.shuffle(rocks)
        rocks = sorted(rocks[:args.scan_train if role == "train" else args.scan_heldout],
                       key=lambda es: es[1]["t_release_s"])
        names = {f"e{e['number']}_r{s['number']:02d}": (e, s) for e, s in rocks}
        offsets = cut_clips(args.worker, args.remote_dir, vid,
                            [(n, s["t_release_s"]) for n, (e, s) in names.items()], work / vid)
        cands = []                      # (delivery name, frame candidate, context)
        for n, (e, s) in names.items():
            vname = hogtime.CAMERA_FOR[OTHER_HOUSE[e["house"]]]
            view = views[vname]
            top, crop_bot, follow_bot = rows_for(view)
            t0 = s["t_release_s"] + WINDOW_S[0]; t1 = s["t_release_s"] + WINDOW_S[1]
            clip = work / vid / f"clip_{n}.mp4"
            fr, ts = longview.decode(str(clip), view.rect, t0 - offsets[n], t1 - offsets[n], FPS)
            if not len(fr):
                continue
            ts = [t + offsets[n] for t in ts]
            per = detect(model, [f[top:follow_bot] for f in fr], top, view)
            cls = 0 if s["color"] == "red" else 1
            track, how = track_rock(per, ts, s, cls)
            H = min(fr.shape[1], crop_bot) - top
            for f in frames_of(track, per, ts, view, top, s, cls):
                # the frame as it will be written, not the frame: 65 full frames
                # a rock over 48 rocks would be 8 GB
                cands.append((n, f, dict(e=e, s=s, view=view, vname=vname, top=top, crop_bot=top + H,
                                         jpg=jpeg(fr[f["i"]], top, top + H), W=fr.shape[2], H=H,
                                         per=per[f["i"]], how=how)))
            del fr
            print(f"\r{vid}: {n} ({len(track)} frames followed{'' if how else ', not found'})    ",
                  end="", flush=True)
        # choose one frame per delivery
        chosen, used = [], set()
        by_cat = {}
        for c in cands:
            key = category(c[1]) if role == "train" else depth_stratum(c[1])
            by_cat.setdefault(key, []).append(c)
        if role == "train":
            order = list(TRAIN_QUOTA.items())
            spill = []
            for cat, want in order:
                pool = by_cat.get(cat, []); rng.shuffle(pool); got = 0
                for c in pool:
                    if c[0] in used:
                        continue
                    chosen.append((cat, c)); used.add(c[0]); got += 1
                    if got >= want:
                        break
                spill.append(want - got)
            # a short category is made up from the other hard ones, then from any
            short = sum(spill)
            for cat in ("slide_miss", "rest_miss", "rest_unsure", "slide_unsure", "sure"):
                pool = by_cat.get(cat, []); rng.shuffle(pool)
                for c in pool:
                    if short <= 0:
                        break
                    if c[0] not in used:
                        chosen.append((cat, c)); used.add(c[0]); short -= 1
        else:
            strata = ["rest", "early", "late"]
            for k in range(HELDOUT_PER_GAME):
                cat = strata[k % 3]
                pool = [c for c in by_cat.get(cat, []) if c[0] not in used]
                if not pool:
                    pool = [c for c in cands if c[0] not in used]
                if not pool:
                    break
                c = rng.choice(pool); chosen.append((cat, c)); used.add(c[0])
        for cat, (n, f, ctx) in chosen:
            view, top, crop_bot = ctx["view"], ctx["top"], ctx["crop_bot"]
            H, W = ctx["H"], ctx["W"]
            boxes = [q for q in ctx["per"] if q["conf"] >= PRELABEL_CONF and q["y1"] <= crop_bot + 5]
            rock = box_for(view, f["x"], f["yp"], top) if f["placed"] else \
                min(ctx["per"], key=lambda q: abs(q["x"] - f["x"]) + abs(q["yp"] - f["yp"]))
            rock = {**rock, "cls": 0 if ctx["s"]["color"] == "red" else 1}
            if not any(iou(rock, q) > 0.4 for q in boxes):
                boxes.append(rock)
            rows = []
            for q in boxes:
                x0, x1 = max(0.0, q["x0"]), min(W, q["x1"]); y0, y1 = max(0.0, q["y0"] - top), min(H, q["y1"] - top)
                if x1 - x0 < 2 or y1 - y0 < 2:
                    continue
                rows.append([int(q["cls"]), round((x0 + x1) / 2 / W, 6), round((y0 + y1) / 2 / H, 6),
                             round((x1 - x0) / W, 6), round((y1 - y0) / H, 6)])
            t_abs = round(f["t"], 2)
            stem = stem_for(vid, ctx["vname"], t_abs)
            jpg = ctx["jpg"]
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
                             "state": f["state"], "rock_x": round(f["x"], 4), "rock_yp": round(f["yp"], 3),
                             "rock_conf": round(f["conf"], 3), "rock_box_placed": f["placed"],
                             "rock_position_guessed": bool(f.get("guessed")),
                             "found_by": ctx["how"], "crop_top": top, "width": W, "height": H,
                             "rect": list(view.rect), "tee_row": view.tee_row, "hog_row": view.hog_row,
                             "centre_col": view.centre_col, "lat_px_per_m_at_tee": view.lat_px_per_m_at_tee,
                             "centre_line": list(view.centre_line) if view.centre_line else None,
                             "proposed": True, "mask_half_m": args.mask_half_m or None})
        shutil.rmtree(work / vid, ignore_errors=True)
        cats = {}
        for cat, _ in chosen:
            cats[cat] = cats.get(cat, 0) + 1
        print(f"\r{vid} ({role}): {len(names)} rocks scanned, {len({c[0] for c in cands})} followed, "
              f"kept {len(chosen)}: {cats}                ", flush=True)

    shutil.rmtree(work, ignore_errors=True)
    (out / "items.json").write_text(json.dumps(items))
    Path(args.manifest).write_text(json.dumps(manifest, indent=1) + "\n")
    page = boxedit.render(items, out, scope=args.scope, proposals=True,
                          title="Stones between the hack and the tee")
    html = page.read_text()
    import re
    html, k = re.subn(r'<p class="muted">.*?</p>', HELP_MASKED if args.mask_half_m > 0 else HELP,
                      html, count=1, flags=re.S)
    assert k == 1
    page.write_text(html)
    roles = {}
    for m in manifest:
        roles[m["role"]] = roles.get(m["role"], 0) + 1
    print(f"\n{len(items)} frames {roles}; page {page}; manifest {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
