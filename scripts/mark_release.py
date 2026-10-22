#!/usr/bin/env python
"""Mark, by eye, the frame the thrower's hand leaves the handle.

The pose release (the throwing wrist rising off the handle, read by a COCO pose
model on the long camera facing the thrower) needs a baseline it did not make
itself. This serves the same kind of scrubber as ``mark_hog.py``: the camera
that faces the thrower, cropped to follow the rock at one metric scale with the
handle in the middle, every frame from the tee to past the end of the delivery
path. A person steps to the first frame the hand is clearly off and marks it.
Nothing a model saw is drawn -- no wrist, no read, no distance -- so a mark is
the person's alone.

    # on the GPU box, where the video is (inside the worker image):
    python scripts/mark_release.py --timeline tl.json --video v.mp4 \\
        --rocks 1:5,2:7 --out /data/mark --extract-only
    # wherever it is served from:
    python scripts/mark_release.py --out /data/mark --prepared --host 0.0.0.0

Marks are written as they are made (``marks.json``, keyed by rock id), so it
can be closed and reopened. A mark is the frame's time from the release
(``t_release_s``), or "held" when the hand is still on at the last frame, or
null for can't tell.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FPS = 30.0
TEE_Y = 34.747
# hogtime.CAMERA_FOR[OTHER_HOUSE[house]]: the long camera at the far end, facing the thrower.
CAMERA = {"top": "right", "bottom": "left"}
# The crop, in metres around the rock's ice point at its own depth: wide enough
# for both hands and the handle, tall enough for the forearm.
ACROSS_M, UP_M, DOWN_M = 0.7, 1.0, 0.25
OUT_W = 560
# From the tee (nobody lets go behind it) to this long after the path's last
# sample, the rock carried on at its last speed, so a hand still on at hog
# +1.5 m can still be seen coming off.
FROM_YP = 0.0
AFTER_PATH_S = 1.2


def side_view(c):
    from curling_score.geometry.sideview import SideView
    return SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"],
                    centre_col=c["centre_col"], lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"],
                    centre_line=tuple(c["centre_line"]) if c.get("centre_line") else None)


def rock_at(dl, o):
    """(x, yp) of the rock at o seconds from the release: interpolated along the
    path, then carried on at the last half second's velocity past its end."""
    ts = [p[0] for p in dl]
    if o <= ts[-1]:
        import numpy as np
        return (float(np.interp(o, ts, [p[2] for p in dl])),
                TEE_Y - float(np.interp(o, ts, [p[1] for p in dl])))
    tail = [p for p in dl if p[0] >= ts[-1] - 0.5]
    a, b = tail[0], tail[-1]
    dt = max(1e-3, b[0] - a[0])
    vx, vy = (b[2] - a[2]) / dt, -(b[1] - a[1]) / dt
    return b[2] + vx * (o - b[0]), TEE_Y - b[1] + vy * (o - b[0])


def extract(video, tl, end_no, rock_no, dest, tag=""):
    import cv2
    import numpy as np
    from curling_score.detect import longview

    game = tl["games"][0]
    end = next(e for e in game["ends"] if e["number"] == end_no)
    shot = next(s for s in end["shots"] if s["number"] == rock_no)
    view = side_view(tl["calibration"][CAMERA[end["house"]]])
    dl = shot["line"]["delivery"]
    t_rel = shot["t_release_s"]
    o0 = next((p[0] for p in dl if TEE_Y - p[1] >= FROM_YP), dl[0][0])
    o1 = dl[-1][0] + AFTER_PATH_S
    frames, times = longview.decode(video, view.rect, t_rel + o0, t_rel + o1, FPS)
    stem = f"{tag}-e{end_no:02d}r{rock_no:02d}" if tag else f"e{end_no:02d}r{rock_no:02d}"
    out = []
    for i, (f, t) in enumerate(zip(frames, times)):
        o = float(t - t_rel)
        x, yp = rock_at(dl, o)
        sc, sr = view.to_image(-x, yp)
        ppm = view.lateral_px_per_m(view.row_for(yp))
        z = OUT_W / (2 * ACROSS_M * ppm)
        h = int(round((UP_M + DOWN_M) * OUT_W / (2 * ACROSS_M)))
        M = np.float32([[z, 0, -(sc - ACROSS_M * ppm) * z], [0, z, -(sr - UP_M * ppm) * z]])
        img = cv2.warpAffine(cv2.cvtColor(f, cv2.COLOR_RGB2BGR), M, (OUT_W, h),
                             flags=cv2.INTER_CUBIC, borderValue=(40, 40, 40))
        name = f"{stem}_{i:03d}.jpg"
        cv2.imwrite(str(dest / name), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        out.append({"file": name, "o": round(o, 4), "yp": round(yp, 3)})
    return {"id": stem, "tag": tag, "video_id": tl.get("source", {}).get("video_id"),
            "end": end_no, "rock": rock_no, "color": shot["color"],
            "position": shot.get("position"), "label": shot.get("label"),
            "t_release_s": t_rel, "frames": out}


PAGE = """<!doctype html><meta charset=utf-8><title>Mark the release</title>
<style>
 body{margin:0;background:#15171a;color:#e8e8e8;font:14px system-ui,sans-serif}
 header{padding:10px 16px;border-bottom:1px solid #2c2f35;display:flex;gap:18px;
        align-items:center;flex-wrap:wrap}
 #img{display:block;margin:14px auto;max-width:98vw}
 .k{background:#24272d;border-radius:5px;padding:2px 7px;font-family:ui-monospace}
 #done{color:#4ade80} #mine{color:#93c5fd}
 button{font:inherit;padding:7px 14px;border-radius:7px;border:1px solid #3a3f47;
        background:#24272d;color:#e8e8e8;cursor:pointer}
 button.go{background:#2b6cb0;border-color:transparent}
 input[type=range]{width:min(560px,96vw);display:block;margin:0 auto}
</style>
<header>
  <strong id=who></strong>
  <span id=pos></span>
  <span id=mine></span>
  <span id=done></span>
  <span style="margin-left:auto">
    <span class=k>&larr;</span> <span class=k>&rarr;</span> frame &nbsp;
    <span class=k>shift</span> x5 &nbsp; <span class=k>enter</span> off here &nbsp;
    <span class=k>h</span> still on at the end &nbsp; <span class=k>s</span> can't tell &nbsp;
    <span class=k>[</span> <span class=k>]</span> rock</span>
</header>
<img id=img>
<input id=bar type=range min=0 value=0 oninput="fi=+this.value;show()">
<div style="text-align:center;padding:12px 0 20px">
  <button onclick="go(-1)">&lsaquo; rock</button>
  <button onclick="step(-1)">&larr;</button>
  <button class=go onclick="mark()">Hand is off here</button>
  <button onclick="step(1)">&rarr;</button>
  <button onclick="save('held')">Still on at the end</button>
  <button onclick="save(null)">Can't tell</button>
  <button onclick="go(1)">rock &rsaquo;</button>
</div>
<p style="max-width:780px;margin:0 auto 40px;color:#9aa0a6">
 The camera at the far end, facing the thrower, following the rock. Step to the
 <em>first frame the throwing hand is clearly off the handle</em> -- not touching
 it any more -- and mark it. If they let go, take hold again and let go again,
 mark the last let-go. If the hand is still on at the last frame, press
 <span class=k>h</span>. Nothing the model saw is drawn.</p>
<script>
let D=[],di=0,fi=0,marks={};
const cache={};
async function load(){
  D=await (await fetch('index.json')).json();
  marks=await (await fetch('marks.json')).json().catch(()=>({}));
  while(di<D.length-1 && (D[di].id in marks)) di++;
  open_();
}
function open_(){
  fi=0;
  const d=D[di];
  if(d) d.frames.forEach(f=>{if(!cache[f.file]){const i=new Image();i.src=f.file;cache[f.file]=i;}});
  show();
}
function show(){
  const d=D[di];
  if(fi>=d.frames.length) fi=d.frames.length-1;
  document.getElementById('img').src=d.frames[fi].file;
  const bar=document.getElementById('bar'); bar.max=d.frames.length-1; bar.value=fi;
  document.getElementById('who').textContent=(d.tag?d.tag+' · ':'')+
    'end '+d.end+', rock '+d.rock+' · '+d.color+' '+(d.position||'');
  document.getElementById('pos').textContent=
    'rock '+(di+1)+' of '+D.length+'  ·  frame '+(fi+1)+'/'+d.frames.length;
  const m=marks[d.id];
  document.getElementById('mine').textContent= d.id in marks
    ? (m===null?'yours: can\\'t tell':m==='held'?'yours: still on at the end'
       :'yours: frame '+(d.frames.findIndex(f=>f.o===m)+1)) : '';
  const v=Object.values(marks);
  document.getElementById('done').textContent=v.length+' of '+D.length+' done';
}
function step(n){fi=Math.max(0,Math.min(D[di].frames.length-1,fi+n));show();}
function go(n){di=Math.max(0,Math.min(D.length-1,di+n));open_();}
async function save(v){
  marks[D[di].id]=v;
  await fetch('save',{method:'POST',body:JSON.stringify(marks)});
  if(di<D.length-1) go(1); else show();
}
const mark=()=>save(D[di].frames[fi].o);
addEventListener('keydown',e=>{
  if(e.key==='ArrowLeft')step(e.shiftKey?-5:-1);
  else if(e.key==='ArrowRight')step(e.shiftKey?5:1);
  else if(e.key==='Enter')mark(); else if(e.key==='h')save('held');
  else if(e.key==='s')save(null);
  else if(e.key==='[')go(-1); else if(e.key===']')go(1); else return;
  e.preventDefault();});
load();
</script>"""


def serve(root: Path, host: str, port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            name = self.path.lstrip("/") or "index.html"
            if name == "index.html":
                body, ctype = PAGE.encode(), "text/html"
            else:
                p = root / name
                if "/" in name or not p.is_file():
                    self.send_error(404)
                    return
                body = p.read_bytes()
                ctype = "application/json" if name.endswith(".json") else "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if name.endswith(".json") else "max-age=3600")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            json.loads(body)
            (root / "marks.json").write_bytes(body)
            self.send_response(204)
            self.end_headers()

    print(f"\n  open  http://{host}:{port}/   ({root})\n", flush=True)
    ThreadingHTTPServer((host, port), H).serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--timeline")
    ap.add_argument("--video")
    ap.add_argument("--rocks", help="END:ROCK,END:ROCK,... in the order to mark them")
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--prepared", action="store_true", help="skip extraction")
    ap.add_argument("--extract-only", action="store_true",
                    help="write the frames and exit without serving")
    ap.add_argument("--tag", default="",
                    help="a short name for this game, prefixed to every rock id, so several games "
                         "can share one session (with --append)")
    ap.add_argument("--append", action="store_true",
                    help="add these rocks to the session's index rather than replacing it")
    args = ap.parse_args()

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    if not args.prepared:
        tl = json.loads(Path(args.timeline).read_text())
        rocks = [tuple(int(v) for v in p.split(":")) for p in args.rocks.split(",") if p]
        index = json.loads((root / "index.json").read_text()) \
            if args.append and (root / "index.json").exists() else []
        for n, (e, r) in enumerate(rocks):
            print(f"  extracting {n + 1}/{len(rocks)}: end {e} rock {r}", flush=True)
            got = extract(args.video, tl, e, r, root, args.tag)
            index = [d for d in index if d["id"] != got["id"]] + [got]
        (root / "index.json").write_text(json.dumps(index, indent=1))
        if not (root / "marks.json").exists():
            (root / "marks.json").write_text("{}")
    if args.extract_only:
        print(f"  extracted to {root}; serve it with --prepared")
        return
    serve(root, args.host, args.port)


if __name__ == "__main__":
    main()
