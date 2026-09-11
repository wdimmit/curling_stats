"""The viewer's server contract, held by both servers.

The page fetches bare ``timeline.json`` and ``overrides.json`` and POSTs the
whole overrides map back. ``curling-score serve`` answers that from a directory
on loopback; the hosted API answers it under ``/c/{slug}/``. One set of tests
runs against each, over real HTTP, so the two cannot drift apart -- the few
checks that are about files on disk or the stdlib server's start-up run only
against the local one.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from curling_score import timeline, viewer


def _document():
    shot = {"number": 1, "color": "red", "stones": [], "state_known": False,
            "missing": False, "shot_type": "unknown"}
    return {
        "schema_version": timeline.SCHEMA_VERSION,
        "source": {"video_id": "abc", "url": "u", "sheet": 2, "duration_s": 1.0},
        "games": [{"index": 0, "start_s": 0.0, "end_s": 1.0,
                   "ends": [{"number": 3, "shots": [shot]}]}],
    }


@dataclass
class Served:
    kind: str          # "local" or "service"
    base: str          # ...such that base + "/overrides.json" is the page's URL
    out: object        # the out dir (local) or the repo (service)
    slug: str | None = None

    def saved(self):
        """Whatever the server has persisted as this chart's overrides."""
        if self.kind == "local":
            path = self.out / "overrides.json"
            return json.loads(path.read_text()) if path.exists() else None
        return self.out.get_chart(self.slug).overrides


def _local(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps(_document()))
    httpd = viewer.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield Served("local", f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _service():
    import uvicorn

    from curling_score.service import slug as slug_mod
    from curling_score.service.api import Settings, create_app
    from curling_score.service.records import Chart, Run
    from curling_score.service.repo import MemoryRepo
    from curling_score.service.store import MemoryStore, timeline_key
    from curling_score.service.youtube import FakeYouTube

    repo, store = MemoryRepo(), MemoryStore()
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    run = Run(id="r_test", video_id="abc", processing_version="p+m", status="ready",
              created_at=now, games=[{"index": 0, "start_s": 0.0, "end_s": 1.0, "ends": 1}],
              timeline_key=timeline_key("abc", "r_test"))
    store.put_bytes(run.timeline_key, json.dumps(_document()).encode())
    repo.put_run(run)
    slug = slug_mod.new_slug()
    repo.put_chart(Chart(id=slug, share_slug=slug_mod.new_slug(), video_id="abc",
                         run_id="r_test", created_at=now, updated_at=now, game_index=0))
    app = create_app(repo, store, FakeYouTube(), Settings(worker_token="w", admin_token="a"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield Served("service", f"http://127.0.0.1:{port}/c/{slug}", repo, slug)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(params=["local", "service"])
def served(request, tmp_path):
    yield from (_local(tmp_path) if request.param == "local" else _service())


def local_only(served):
    if served.kind != "local":
        pytest.skip("about the local server's files")


def post(base, payload, path="/overrides.json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, {"ok": False, "error": raw.decode(errors="replace")}


def get(base, path):
    with urllib.request.urlopen(base + path) as res:
        return res.status, res.read()


class TestSavingCorrections:
    def test_a_posted_patch_is_persisted(self, served):
        patch = {"0.3.1": {"user_score": 3, "shot_type": "hit_roll"}}
        status, body = post(served.base, patch)
        assert status == 200 and body["ok"] is True and body["shots"] == 1
        assert served.saved() == patch

    def test_saving_twice_replaces_rather_than_merges(self, served):
        post(served.base, {"0.3.1": {"user_score": 1}})
        post(served.base, {"0.3.1": {"user_score": 4}})
        assert served.saved() == {"0.3.1": {"user_score": 4}}

    def test_it_leaves_no_temporary_files_behind(self, served):
        local_only(served)
        post(served.base, {"0.3.1": {"note": "wide"}})
        assert list(served.out.glob("*.tmp")) == []

    def test_the_page_can_read_its_own_edits_back(self, served):
        post(served.base, {"0.3.1": {"note": "heavy"}})
        status, body = get(served.base, "/overrides.json")
        assert status == 200
        assert json.loads(body) == {"0.3.1": {"note": "heavy"}}

    def test_stones_placed_by_hand_survive_the_round_trip(self, served):
        stones = [{"color": "red", "x": 0.25, "y": -0.4,
                   "distance_to_tee": 0.472, "in_house": True,
                   "confidence": 1.0, "source": "manual"}]
        post(served.base, {"0.3.1": {"stones": stones, "state_known": True}})
        assert served.saved()["0.3.1"]["stones"] == stones

    def test_the_timeline_is_served(self, served):
        status, body = get(served.base, "/timeline.json")
        assert status == 200
        assert json.loads(body)["games"][0]["ends"][0]["shots"][0]["number"] == 1


class TestRejections:
    """A bad request must never damage what is already saved."""

    def _seed(self, served):
        post(served.base, {"0.3.1": {"user_score": 2}})
        return served.saved()

    def test_invalid_json_is_refused(self, served):
        before = self._seed(served)
        status, body = post(served.base, b"{not json")
        assert status == 400 and body["ok"] is False
        assert served.saved() == before

    def test_a_top_level_list_is_refused(self, served):
        before = self._seed(served)
        status, _ = post(served.base, [{"user_score": 1}])
        assert status == 400
        assert served.saved() == before

    def test_a_patch_that_is_not_an_object_is_refused(self, served):
        before = self._seed(served)
        status, _ = post(served.base, {"0.3.1": "hit"})
        assert status == 400
        assert served.saved() == before

    def test_posting_anywhere_else_is_refused(self, served):
        before = self._seed(served)
        status, _ = post(served.base, {"a": {}}, path="/timeline.json")
        assert status in (404, 405)
        assert served.saved() == before

    def test_an_empty_body_is_refused(self, served):
        assert post(served.base, b"")[0] == 400


class TestServedFiles:
    def test_it_copies_every_asset_the_page_needs(self, served):
        local_only(served)
        for name in viewer.ASSETS:
            assert (served.out / name).is_file(), name

    def test_the_page_and_its_script_are_reachable(self, served):
        assert get(served.base, "/")[0] == 200 if served.kind == "service" else True
        page = get(served.base, "/index.html" if served.kind == "local" else "/")[1]
        assert b"app.js" in page
        assert get(served.base, "/app.js")[0] == 200
        assert get(served.base, "/style.css")[0] == 200

    def test_refuses_to_serve_a_directory_with_no_timeline(self, tmp_path):
        with pytest.raises(SystemExit):
            viewer.make_server(tmp_path, port=0)


class TestFeedsBackIntoTheTimeline:
    """What the page saves has to be what the analysis can layer back."""

    def test_a_saved_patch_applies_to_the_document(self, served):
        post(served.base, {"0.3.1": {"user_score": 4, "shot_type": "peel",
                                     "state_known": True}})
        doc = timeline.apply_overrides(_document(), served.saved())
        shot = doc["games"][0]["ends"][0]["shots"][0]
        assert shot["user_score"] == 4
        assert shot["shot_type"] == "peel"
        assert shot["state_known"] is True
        assert shot["corrected"] is True

    def test_applying_the_same_patch_twice_changes_nothing_more(self, served):
        post(served.base, {"0.3.1": {"user_score": 2}})
        once = timeline.apply_overrides(_document(), served.saved())
        twice = timeline.apply_overrides(once, served.saved())
        assert once == twice
