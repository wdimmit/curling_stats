"""Serve the local charting viewer, and take back what the charter enters.

The page is static; the one thing the server does beyond handing out files is
accept ``overrides.json``. Corrections are written to that file rather than
into ``timeline.json`` so that re-running the analysis never destroys them --
``analyze`` layers the same file back over the fresh detection.
"""

import functools
import json
import http.server
import os
import shutil
import tempfile
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
ASSETS = ("index.html", "app.js", "style.css")

# The comment in index.html that the hosted service replaces with the page's
# configuration. A comment rather than the script tag: a tag is markup a tool
# might legitimately reshape, and this substitution failing is silent in the
# worst direction -- a view-only link that boots editable, or a review link
# that tries to save.
BOOT_ANCHOR = "<!--CHART-->"
OVERRIDES = "overrides.json"
# A whole game's charting is a few hundred small patches; a game with every
# stone placed by hand measures around 300 KB, so this is already generous.
# The hosted side keeps the same figure for a different reason: past 1 MiB
# Firestore refuses the document outright.
MAX_BODY_BYTES = 700_000


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


def boot_page(config: dict | None = None) -> str:
    """index.html with ``window.CHART`` injected, or as-is for the local server.

    ``config`` is what tells the page whether this link edits, views or
    reviews. The local ``curling-score serve`` passes None and sets nothing,
    which is what makes everything editable and unversioned there.

    A missing anchor raises rather than returning the page unchanged. The
    version this replaced used ``str.replace(..., 1)``, which silently does
    nothing when it does not match: a 500 on a chart page is recoverable, a
    quietly editable share link is not.
    """
    text = (HERE / "index.html").read_text()
    if BOOT_ANCHOR not in text:
        raise RuntimeError(
            f"{BOOT_ANCHOR} is missing from index.html, so window.CHART cannot "
            f"be injected and every link would boot as a local, editable one")
    if config is None:
        return text.replace(BOOT_ANCHOR, "", 1)
    return text.replace(
        BOOT_ANCHOR, f"<script>window.CHART={json.dumps(config)};</script>", 1)


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
