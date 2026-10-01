#!/usr/bin/env python
"""Serve every charting surface locally, against the real service code.

    python scripts/devserve.py [path/to/timeline.json] [--live SECONDS] [--overrides overrides.json]

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

With ``--live SECONDS`` the same game is also served as a live one: a live
run publishes its first game one more end every SECONDS, through the real
claim, publish and complete routes, and ends complete -- for watching the
page fill in while it is open.

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


def serve_live(w, doc, every_s: float) -> str | None:
    """Publish ``doc``'s first game as a live run, one more end every
    ``every_s``, then complete it. Returns the live game's source id."""
    import copy
    import threading
    import time

    from curling_score.service.records import Job, Run

    live_vid, repo, c = "liveVid0001", w["repo"], w["client"]
    worker = {"Authorization": "Bearer worker-secret"}
    w["yt"].add(VideoMeta(live_vid, "Tonight - Sheet 3", CLUB, 0.0, "live", T0))
    repo.put_run(Run(id="r_live", video_id=live_vid, status="queued", created_at=T0,
                     processing_version=w["settings"].processing_version, kind="live",
                     title="Tonight - Sheet 3", format=(doc.get("format") or {}).get("name")))
    repo.put_job(Job(id="j_live", run_id="r_live", state="queued", created_at=T0,
                     run_after=T0, kind="live"))
    job = c.post("/api/worker/claim", headers=worker,
                 json={"worker_id": "dev", "model_id": "m-abc", "kinds": ["live"]}).json()["job"]
    ends = doc["games"][0]["ends"]

    def publish(k):
        final = k == len(ends)
        d = copy.deepcopy(doc)
        game = d["games"][0]
        d["games"], game["ends"] = [game], game["ends"][:k]
        game["end_s"], game["in_progress"] = game["ends"][-1]["end_s"], not final
        d["source"]["video_id"] = live_vid
        w["clock"].advance(1)
        d["live"] = {"in_progress": not final, "recorded_s": game["end_s"] + 300.0,
                     "updated_at": w["clock"]().isoformat()}
        plan = c.post(f"/api/worker/jobs/{job['id']}/artifacts", headers=worker,
                      json={"worker_id": "dev", "files": [{"name": "timeline.json",
                                                           "bytes": 1}]}).json()
        w["store"].put_bytes(plan["uploads"][0]["key"], json.dumps(d).encode())
        games = [{"index": 0, "start_s": game["start_s"], "end_s": game["end_s"], "ends": k}]
        c.post(f"/api/worker/jobs/{job['id']}/publish", headers=worker,
               json={"worker_id": "dev", "games": games, "title": "Tonight - Sheet 3"})
        if final:
            c.post(f"/api/worker/jobs/{job['id']}/complete", headers=worker,
                   json={"worker_id": "dev", "games": games, "title": "Tonight - Sheet 3",
                         "format": (doc.get("format") or {}).get("name") or "fours"})
        print(f"  live: published end {k} of {len(ends)}" + (" -- final" if final else ""),
              flush=True)

    publish(1)

    def rest():
        for k in range(2, len(ends) + 1):
            time.sleep(every_s)
            publish(k)

    threading.Thread(target=rest, daemon=True).start()
    src = next(iter(repo.sources_for_video(live_vid)), None)
    return src.id if src else None


def main() -> None:
    args = [a for a in sys.argv[1:]]
    live_every = None
    if "--live" in args:
        i = args.index("--live")
        live_every = float(args[i + 1])
        del args[i:i + 2]
    overrides = None
    if "--overrides" in args:
        i = args.index("--overrides")
        overrides = json.loads(pathlib.Path(args[i + 1]).read_text())
        del args[i:i + 2]
    where = pathlib.Path(args[0] if args else "out_chart/timeline.json")
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
    if overrides is not None:
        # A chart's grading, so the report has percentages to show.
        repo.update_chart(slug, overrides=overrides, overrides_version=1)
    # A second video, left queued, so the status page is reachable too -- it is
    # a surface like any other and is otherwise only visible for the half hour
    # a real run takes.
    yt.add(VideoMeta("QUEUEDvid01", "5/1 - Sheet 3 - Spring League", CLUB, 7200.0,
                     "none", T0))
    waiting = submit(w, url="https://youtu.be/QUEUEDvid01").json()["slug"]
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
    say(f"  waiting    {base}/c/{waiting}/   (status page)")
    if live_every is not None:
        live_source = serve_live(w, doc, live_every)
        say(f"  live       {base}/g/{live_source}/   (one more end every {live_every:g} s)")
    say(f"  catalogue  {base}/games")
    say(f"  submit     {base}/\n")

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
