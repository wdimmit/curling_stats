#!/usr/bin/env python
"""Mark, by eye, the frame a delivery crosses its own hog line.

The overhead panels' metre scale is badly nonlinear away from the house
(`geometry/calibrate.py` fits one `px_per_m` from the rings), so a distance
measured up by the hog line is not trustworthy. But the split does not need
distances -- it needs two line crossings, and the hog line is *painted*. What
it needs from the panel is one number per panel: the apparent y at which that
paint sits. Every delivery that crosses it pins that number again.

The side cameras see the throwing end's hog line square on. This serves a
scrubber over the frames around each crossing so a person can say which frame
the stone touched the line, which is the one judgement here that a person makes
better than a detector. The line is *not* drawn on the frames: the point is to
read the paint, and a drawn guess would only be copied back.

    python scripts/mark_hog.py --events releases.json --panel top --out /tmp/mark

Then open the URL it prints. Marks are written as they are made, so it can be
closed and reopened.
"""

import argparse
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

FPS = 30.0
# Which side camera sees a given panel's hog line: the one at the other end,
# looking back down the sheet. The same alternation as ``OTHER_HOUSE``.
CAMERA_FOR = {"top": "left", "bottom": "right"}
CROPS = {"left": (470, 570, 330, 700), "right": (470, 570, 1320, 1740)}
# Measured on the club's feed: releases cross their hog line 3.3-5.0 s after
# the panel first sees them leave the hack.
WINDOW_S = (2.8, 5.8)


def _flatten(grey: np.ndarray, k: int = 31) -> np.ndarray:
    """Divide out the ice's own shading so the paint shows.

    A 31-pixel box is far wider than the ~4-pixel painted line, so subtracting
    it leaves the line and removes the gradient that hides it. Smoothing only
    along y would erase the line instead -- it *is* a horizontal feature.
    """
    pad = np.pad(grey, k, mode="edge")
    c = pad.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    h, w = grey.shape
    box = (c[2 * k:2 * k + h, 2 * k:2 * k + w] - c[0:h, 2 * k:2 * k + w]
           - c[2 * k:2 * k + h, 0:w] + c[0:h, 0:w]) / (2 * k) ** 2
    # A fixed scale, not a percentile stretch: the painted line is a dip of
    # roughly 25 levels, and a percentile stretch lets the sweepers' boots and
    # broom heads -- far darker than any paint -- set the range and wash it
    # out. Everything darker than the paint simply saturates, which is fine.
    return np.clip((grey - box) / 26.0 * 128 + 128, 0, 255).astype(np.uint8)


def extract(video, view, t0, t1, dest, stem):
    y0, y1, x0, x1 = CROPS[view]
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t0}", "-i", str(video),
         "-t", f"{t1 - t0 + 0.05}", "-vf", f"fps={FPS}", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 1080, 1920, 3)
    out = []
    for i, f in enumerate(fr):
        crop = f[y0:y1, x0:x1]
        flat = _flatten(crop.mean(axis=2).astype(np.float64))
        side = np.concatenate([crop, np.stack([flat] * 3, -1)], axis=0)
        big = np.repeat(np.repeat(side, 2, 0), 2, 1)
        name = f"{stem}_{i:03d}.jpg"
        Image.fromarray(big).save(dest / name, quality=86)
        out.append({"file": name, "t": round(t0 + i / FPS, 3)})
    return out


PAGE = """<!doctype html><meta charset=utf-8><title>Mark the hog line</title>
<style>
 body{margin:0;background:#15171a;color:#e8e8e8;font:14px system-ui,sans-serif}
 header{padding:10px 16px;border-bottom:1px solid #2c2f35;display:flex;gap:18px;
        align-items:center;flex-wrap:wrap}
 #img{display:block;margin:14px auto;image-rendering:pixelated;max-width:98vw}
 .k{background:#24272d;border-radius:5px;padding:2px 7px;font-family:ui-monospace}
 #done{color:#4ade80} #skip{color:#fbbf24}
 button{font:inherit;padding:7px 14px;border-radius:7px;border:1px solid #3a3f47;
        background:#24272d;color:#e8e8e8;cursor:pointer}
 button.go{background:#2b6cb0;border-color:transparent}
</style>
<header>
  <strong id=who></strong>
  <span id=pos></span>
  <span id=done></span><span id=skip></span>
  <span style="margin-left:auto">
    <span class=k>&larr;</span> <span class=k>&rarr;</span> step &nbsp;
    <span class=k>shift</span> x5 &nbsp; <span class=k>enter</span> mark &nbsp;
    <span class=k>s</span> skip</span>
</header>
<img id=img>
<div style="text-align:center;padding-bottom:20px">
  <button onclick="step(-1)">&larr;</button>
  <button class=go onclick="mark()">Crossing is here</button>
  <button onclick="step(1)">&rarr;</button>
  <button onclick="skip()">Can't tell</button>
</div>
<p style="max-width:780px;margin:0 auto 40px;color:#9aa0a6">
 Top half is the frame as shot; bottom half is the same frame with the ice's
 shading divided out, which is where the painted hog line shows. Step until the
 <em>leading edge of the stone</em> first touches the near edge of the paint,
 then mark. The line is deliberately not drawn for you.</p>
<script>
let D=[],di=0,fi=0,marks={};
async function load(){
  D=await (await fetch('index.json')).json();
  marks=await (await fetch('marks.json')).json().catch(()=>({}));
  while(di<D.length && (D[di].id in marks)) di++;
  fi=D[di]?Math.floor(D[di].frames.length/2):0;
  show();
}
function show(){
  if(di>=D.length){document.getElementById('who').textContent='All done.';
    document.getElementById('img').style.display='none';return;}
  const d=D[di];
  if(fi>=d.frames.length) fi=d.frames.length-1;
  document.getElementById('img').src=d.frames[fi].file;
  document.getElementById('who').textContent=
    d.color+' delivery, released '+d.release.toFixed(1)+' s';
  document.getElementById('pos').textContent=
    'delivery '+(di+1)+' of '+D.length+'  ·  frame '+(fi+1)+'/'+d.frames.length+
    '  ·  t = '+d.frames[fi].t.toFixed(3)+' s';
  const m=Object.values(marks);
  document.getElementById('done').textContent=
    m.filter(v=>v!==null).length+' marked';
  document.getElementById('skip').textContent=
    m.filter(v=>v===null).length?('  ·  '+m.filter(v=>v===null).length+' skipped'):'';
}
function step(n){fi=Math.max(0,Math.min(D[di].frames.length-1,fi+n));show();}
async function save(v){
  marks[D[di].id]=v;
  await fetch('save',{method:'POST',body:JSON.stringify(marks)});
  di++;fi=Math.floor(D[di]?D[di].frames.length/2:0);show();
}
const mark=()=>save(D[di].frames[fi].t), skip=()=>save(null);
addEventListener('keydown',e=>{
  if(e.key==='ArrowLeft')step(e.shiftKey?-5:-1);
  else if(e.key==='ArrowRight')step(e.shiftKey?5:1);
  else if(e.key==='Enter')mark(); else if(e.key==='s')skip(); else return;
  e.preventDefault();});
load();
</script>"""


def serve(root: Path, port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            name = self.path.lstrip("/") or "index.html"
            if name == "index.html":
                body, ctype = PAGE.encode(), "text/html"
            else:
                p = root / name
                if not p.is_file():
                    self.send_error(404)
                    return
                body = p.read_bytes()
                ctype = "application/json" if name.endswith(".json") else "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            (root / "marks.json").write_bytes(self.rfile.read(n))
            self.send_response(204)
            self.end_headers()

    print(f"\n  open  http://localhost:{port}/   ({root})\n")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--events", required=True,
                    help="JSON list of releases: color, t (first sighting)")
    ap.add_argument("--panel", required=True, choices=("top", "bottom"),
                    help="the panel the stones are thrown from")
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--prepared", action="store_true", help="skip extraction")
    args = ap.parse_args()

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    view = CAMERA_FOR[args.panel]
    if not args.prepared:
        events = json.loads(Path(args.events).read_text())
        index = []
        for n, e in enumerate(events):
            t0, t1 = e["t"] + WINDOW_S[0], e["t"] + WINDOW_S[1]
            print(f"  extracting {n + 1}/{len(events)}: {e['color']} at {e['t']:.1f}")
            frames = extract(args.video, view, t0, t1, root, f"d{n:02d}")
            index.append({"id": f"d{n:02d}", "color": e["color"],
                          "release": e["t"], "view": view, "frames": frames})
        (root / "index.json").write_text(json.dumps(index, indent=1))
        if not (root / "marks.json").exists():
            (root / "marks.json").write_text("{}")
    serve(root, args.port)


if __name__ == "__main__":
    main()
