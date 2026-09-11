"""Serve the local timeline viewer."""

import http.server
import shutil
import socketserver
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent


def serve(out_dir, port: int = 8000, open_browser: bool = True) -> None:
    """Serve ``timeline.json`` from ``out_dir`` alongside the viewer page."""
    out_dir = Path(out_dir)
    timeline = out_dir / "timeline.json"
    if not timeline.is_file():
        raise SystemExit(
            f"no timeline at {timeline}\nRun: curling-score analyze <url> --out {out_dir}"
        )
    shutil.copyfile(HERE / "index.html", out_dir / "index.html")

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(out_dir), **kw
    )
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/index.html"
        print(f"Curling timeline viewer at {url}   (ctrl-c to stop)")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
