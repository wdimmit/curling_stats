"""Serve a handle-point wave and keep what is labelled on it.

    ./.venv/bin/python scripts/spin/serve.py ~/curling-work/spin/wave1 [--port 8780]

Open http://127.0.0.1:8780/ (from another machine, ssh -L 8780:127.0.0.1:8780).
Over HTTP, never file:// -- the page saves through POST /save, which writes
OUT/edits/<scope>-<sitting>.json: one file per sitting, rewritten as it goes.
Copy edits/ into datasets/spin/edits/ and commit after every sitting.
"""
import argparse
import json
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def save_edits(directory, payload: dict) -> dict:
    """Write one sitting's labels beside the frames. The sitting id comes from
    the page, so saving again in the same sitting replaces its own file and
    never another sitting's."""
    points, flags = payload.get("points") or {}, payload.get("flags") or {}
    if not points and not flags:
        return {"ok": False, "reason": "empty: no frame has two points or a flag"}
    for stem, p in points.items():
        if not (isinstance(p, dict) and len(p.get("neck") or []) == 2 and len(p.get("grip") or []) == 2):
            return {"ok": False, "reason": f"{stem}: a frame needs both the neck and the grip end"}
    scope = re.sub(r"[^A-Za-z0-9]+", "-", str(payload.get("scope") or "unscoped")).strip("-") or "unscoped"
    sitting = re.sub(r"[^A-Za-z0-9]+", "", str(payload.get("sitting") or "")) or "nositting"
    out_dir = Path(directory) / "edits"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{scope}-{sitting}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1) + "\n")
    tmp.replace(path)
    return {"ok": True, "path": str(path), "points": len(points), "flags": len(flags)}


def saved_labels(directory) -> dict:
    """Every saved sitting merged, later sittings (by name) winning per frame:
    what the page treats as done, whatever its browser remembers."""
    points, flags = {}, {}
    for f in sorted(Path(directory, "edits").glob("*.json")):
        d = json.loads(f.read_text())
        for stem, pt in (d.get("points") or {}).items():
            points[stem] = pt
            flags.pop(stem, None)
        for stem, fl in (d.get("flags") or {}).items():
            flags[stem] = fl
            points.pop(stem, None)
    return {"points": points, "flags": flags}


def make_handler(directory):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0] != "/labels":
                return super().do_GET()
            body = json.dumps(saved_labels(directory)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self):
            # The page and its frame list change when a wave is re-ordered; a
            # cached copy would show the old order.
            if self.path.split("?")[0] in ("/", "/index.html", "/items.json"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_POST(self):
            if self.path != "/save":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                out = save_edits(directory, json.loads(self.rfile.read(n) or b"{}"))
            except Exception as e:  # report it to the page rather than drop the request
                out = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return partial(Handler, directory=str(directory))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    d = Path(args.directory).expanduser()
    if not (d / "index.html").is_file():
        print(f"no index.html in {d}; build the wave with scripts/spin/wave.py", file=sys.stderr)
        return 1
    items = json.loads((d / "items.json").read_text())
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(d))
    print(f"http://{args.host}:{args.port}/  ({len(items)} frames)", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
