"""Serve the local charting viewer, and take back what the charter enters.

The page is static; the one thing the server does beyond handing out files is
accept ``overrides.json``. Corrections are written to that file rather than
into ``timeline.json`` so that re-running the analysis never destroys them --
``analyze`` layers the same file back over the fresh detection.
"""

import functools
import http.server
import json
import os
import shutil
import tempfile
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
ASSETS = ("index.html", "app.js", "style.css")
OVERRIDES = "overrides.json"
# A whole game's charting is a few hundred small patches; anything approaching
# this is a bug or a bad actor, not a game.
MAX_BODY_BYTES = 10_000_000


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    """Static files, plus a POST that saves the charter's corrections."""

    _lock = threading.Lock()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802  (stdlib naming)
        # The server binds to loopback, but a POST writes to disk, so it says
        # so itself rather than relying on how it happened to be started.
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self._json(403, {"ok": False, "error": "loopback only"})
        if self.path.rstrip("/").lstrip("/") != OVERRIDES:
            return self._json(404, {"ok": False, "error": "unknown path"})

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(400, {"ok": False, "error": "no length"})
        if not 0 < length <= MAX_BODY_BYTES:
            return self._json(400, {"ok": False, "error": "bad length"})

        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            return self._json(400, {"ok": False, "error": f"invalid JSON: {err}"})
        if not isinstance(data, dict) or not all(
            isinstance(v, dict) for v in data.values()
        ):
            return self._json(400, {"ok": False, "error": "expected {key: patch}"})

        try:
            self._write(data)
        except OSError as err:
            return self._json(500, {"ok": False, "error": str(err)})
        return self._json(200, {"ok": True, "shots": len(data)})

    def _write(self, data: dict) -> None:
        """Replace overrides.json in one step.

        Written to a temporary file and renamed, so a save interrupted halfway
        leaves the previous corrections intact rather than a truncated file.
        """
        directory = Path(self.directory)
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, directory / OVERRIDES)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

    def log_message(self, fmt, *args):  # quieter: one line per request is noise
        if self.command == "POST":
            super().log_message(fmt, *args)


def make_server(out_dir, port: int = 8000):
    """A viewer server for ``out_dir``. Port 0 picks a free one, for tests."""
    out_dir = Path(out_dir)
    timeline = out_dir / "timeline.json"
    if not timeline.is_file():
        raise SystemExit(
            f"no timeline at {timeline}\n"
            f"Run: curling-score analyze <url> --out {out_dir}"
        )
    for name in ASSETS:
        shutil.copyfile(HERE / name, out_dir / name)

    handler = functools.partial(ViewerHandler, directory=str(out_dir))
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(out_dir, port: int = 8000, open_browser: bool = True) -> None:
    """Serve ``timeline.json`` from ``out_dir`` alongside the viewer page."""
    with make_server(out_dir, port) as httpd:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
        print(f"Curling chart viewer at {url}   (ctrl-c to stop)")
        print(f"Corrections are saved to {Path(out_dir) / OVERRIDES}")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
