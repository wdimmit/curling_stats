"""Serve a stone page (red/yellow) with SAM behind it.

    ./.venv/bin/python scripts/ds13/serve.py ~/curling-work/ds13hack/wave1 \\
        --weights ~/curling-work/sam/sam2.1_b.pt

Serves on 127.0.0.1:8777. From another machine, forward the port:
ssh -L 8777:127.0.0.1:8777 <this box>. Serve over HTTP, never file:// --
the page keeps work in localStorage, and it saves through /save into the
wave's edits/ directory.
"""
import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()

    from curling_score.train import segserve

    d = Path(args.directory).expanduser()
    items = json.loads((d / "items.json").read_text())
    httpd = segserve.serve(d, items, str(Path(args.weights).expanduser()), port=args.port)
    print(f"http://127.0.0.1:{args.port}/  ({len(items)} frames)", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
