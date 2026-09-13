#!/usr/bin/env python
"""Serve every charting surface locally, against the real service code.

    python scripts/devserve.py [path/to/timeline.json]

Prints a URL for each of the four surfaces the viewer has to work on:

    /c/{slug}/   editing a chart you own
    /s/{share}/  the view-only link
    /g/{source}/ the public review page, which has no overrides at all
    /games       the catalogue, and the rest of the site pages

Why this rather than a bundler's dev server: those three surfaces differ only
in what `window.CHART` says and what `overrides.json` does, and both of those
live in `service/api.py`. A dev server serving index.html off disk injects
nothing, so it reproduces exactly one of them -- and teaching it to fake the
injection would be a second implementation of the one mechanism in this system
whose failure is silent. Here the injection, the merge protocol and the 403s
are the real ones. Pair it with `npm run watch` and reload.

Everything is in memory: nothing here touches Firestore, GCS or YouTube.
"""
import json
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_service_api import (  # noqa: E402
    CLUB, VID, Clock, FakeYouTube, Settings, T0, VideoMeta, submit, work_through,
)
from curling_score.service.api import create_app  # noqa: E402
from curling_score.service.repo import MemoryRepo  # noqa: E402
from curling_score.service.store import MemoryStore  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    where = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out_chart/timeline.json")
    if not where.is_file():
        raise SystemExit(
            f"no timeline at {where}\n"
            f"Run: curling-score analyze <url> --out {where.parent}\n"
            f"or:  python scripts/devserve.py path/to/timeline.json")
    doc = json.loads(where.read_text())

    repo, store, yt, clock = MemoryRepo(), MemoryStore(), FakeYouTube(), Clock()
    yt.add(VideoMeta(VID, "4/30 - Sheet 2 - Spring League", CLUB, 14392.0, "none", T0))
    port = free_port()
    settings = Settings(public_base_url=f"http://127.0.0.1:{port}",
                        # The tokens the imported work_through/submit helpers send.
                        worker_token="worker-secret", admin_token="admin-secret",
                        allowed_channels={CLUB}, model_id="m-abc",
                        max_queued=9, rate_hour=999, rate_day=9999)
    app = create_app(repo, store, yt, settings, now=clock)
    w = {"client": TestClient(app), "repo": repo, "store": store, "yt": yt,
         "clock": clock, "settings": settings}

    slug = submit(w).json()["slug"]
    work_through(w, doc=doc, games=len(doc["games"]))
    share = repo.get_chart(slug).share_slug
    games = [g for g in w["client"].get("/api/games").json()["games"] if g["source_id"]]
    source = games[0]["source_id"] if games else None

    base = f"http://127.0.0.1:{port}"
    say = lambda line: print(line, flush=True)
    say(f"\n  timeline   {where}  ({len(doc['games'])} game(s))")
    say(f"\n  edit       {base}/c/{slug}/")
    say(f"  view-only  {base}/s/{share}/")
    if source:
        say(f"  review     {base}/g/{source}/")
    say(f"  catalogue  {base}/games")
    say(f"  submit     {base}/\n")

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
