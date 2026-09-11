"""The viewer server, and the one thing it writes.

Charting work only survives if ``overrides.json`` actually lands on disk in a
form ``timeline.apply_overrides`` can read back, so these go end to end: POST
what the page would POST, then feed the saved file to the real function.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from curling_score import timeline, viewer


@pytest.fixture
def served(tmp_path):
    """A running viewer server on an ephemeral port, plus its directory."""
    (tmp_path / "timeline.json").write_text(json.dumps(_document()))
    httpd = viewer.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _document():
    shot = {"number": 1, "color": "red", "stones": [], "state_known": False,
            "missing": False, "shot_type": "unknown"}
    return {
        "schema_version": timeline.SCHEMA_VERSION,
        "source": {"video_id": "abc", "url": "u", "sheet": 2, "duration_s": 1.0},
        "games": [{"index": 0, "ends": [{"number": 3, "shots": [shot]}]}],
    }


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
        return err.code, json.loads(err.read())


def get(base, path):
    with urllib.request.urlopen(base + path) as res:
        return res.status, res.read()


class TestSavingCorrections:
    def test_a_posted_patch_lands_on_disk(self, served):
        base, out = served
        patch = {"0.3.1": {"user_score": 3, "shot_type": "hit_roll"}}
        status, body = post(base, patch)
        assert status == 200 and body == {"ok": True, "shots": 1}
        assert json.loads((out / "overrides.json").read_text()) == patch

    def test_saving_twice_replaces_rather_than_merges(self, served):
        base, out = served
        post(base, {"0.3.1": {"user_score": 1}})
        post(base, {"0.3.1": {"user_score": 4}})
        saved = json.loads((out / "overrides.json").read_text())
        assert saved == {"0.3.1": {"user_score": 4}}

    def test_it_leaves_no_temporary_files_behind(self, served):
        base, out = served
        post(base, {"0.3.1": {"note": "wide"}})
        assert list(out.glob("*.tmp")) == []

    def test_the_page_can_read_its_own_edits_back(self, served):
        # The client reloads overrides.json on start; it has to be served.
        base, _ = served
        post(base, {"0.3.1": {"note": "heavy"}})
        status, body = get(base, "/overrides.json")
        assert status == 200
        assert json.loads(body) == {"0.3.1": {"note": "heavy"}}

    def test_stones_placed_by_hand_survive_the_round_trip(self, served):
        base, out = served
        stones = [{"color": "red", "x": 0.25, "y": -0.4,
                   "distance_to_tee": 0.472, "in_house": True,
                   "confidence": 1.0, "source": "manual"}]
        post(base, {"0.3.1": {"stones": stones, "state_known": True}})
        assert json.loads((out / "overrides.json").read_text())["0.3.1"][
            "stones"] == stones


class TestRejections:
    """A bad request must never damage what is already saved."""

    def _seed(self, base, out):
        post(base, {"0.3.1": {"user_score": 2}})
        return (out / "overrides.json").read_text()

    def test_invalid_json_is_refused(self, served):
        base, out = served
        before = self._seed(base, out)
        status, body = post(base, b"{not json")
        assert status == 400 and body["ok"] is False
        assert (out / "overrides.json").read_text() == before

    def test_a_top_level_list_is_refused(self, served):
        base, out = served
        before = self._seed(base, out)
        status, _ = post(base, [{"user_score": 1}])
        assert status == 400
        assert (out / "overrides.json").read_text() == before

    def test_a_patch_that_is_not_an_object_is_refused(self, served):
        base, out = served
        before = self._seed(base, out)
        status, _ = post(base, {"0.3.1": "hit"})
        assert status == 400
        assert (out / "overrides.json").read_text() == before

    def test_posting_anywhere_else_is_refused(self, served):
        base, out = served
        before = self._seed(base, out)
        status, _ = post(base, {"a": {}}, path="/timeline.json")
        assert status == 404
        assert (out / "overrides.json").read_text() == before

    def test_an_empty_body_is_refused(self, served):
        base, _ = served
        assert post(base, b"")[0] == 400


class TestServedFiles:
    def test_it_copies_every_asset_the_page_needs(self, served):
        _, out = served
        for name in viewer.ASSETS:
            assert (out / name).is_file(), name

    def test_the_page_and_its_script_are_reachable(self, served):
        base, _ = served
        assert get(base, "/index.html")[0] == 200
        assert b"app.js" in get(base, "/index.html")[1]
        assert get(base, "/app.js")[0] == 200
        assert get(base, "/style.css")[0] == 200

    def test_refuses_to_serve_a_directory_with_no_timeline(self, tmp_path):
        with pytest.raises(SystemExit):
            viewer.make_server(tmp_path, port=0)


class TestFeedsBackIntoTheTimeline:
    """What the page saves has to be what the analysis can layer back."""

    def test_a_saved_patch_applies_to_the_document(self, served):
        base, out = served
        post(base, {"0.3.1": {"user_score": 4, "shot_type": "peel",
                              "state_known": True}})
        saved = json.loads((out / "overrides.json").read_text())

        doc = timeline.apply_overrides(_document(), saved)
        shot = doc["games"][0]["ends"][0]["shots"][0]
        assert shot["user_score"] == 4
        assert shot["shot_type"] == "peel"
        assert shot["state_known"] is True
        assert shot["corrected"] is True

    def test_applying_the_same_patch_twice_changes_nothing_more(self, served):
        # The page merges overrides client-side over a timeline that may
        # already have them baked in; that must be harmless.
        base, out = served
        post(base, {"0.3.1": {"user_score": 2}})
        saved = json.loads((out / "overrides.json").read_text())
        once = timeline.apply_overrides(_document(), saved)
        twice = timeline.apply_overrides(once, saved)
        assert once == twice
