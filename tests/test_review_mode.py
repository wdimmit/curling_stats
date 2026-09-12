"""Watching a game without charting it.

A person who only wants the shot-to-shot navigation should never have to make
a chart to get it. `/g/{source_id}/` is the catalogue's own link to a game:
public, read-only, and -- the property these tests exist to pin -- carrying no
route that could write anything.
"""

import pytest
from fastapi.testclient import TestClient

from tests.test_service_api import VID, submit, work_through, world  # noqa: F401


def a_source(w):
    """Submit, process, and return the source id the catalogue now lists."""
    submit(w)
    work_through(w)
    games = w["client"].get("/api/games").json()["games"]
    ready = [g for g in games if g["source_id"]]
    assert ready, "processing a run should have created sources"
    return ready[0]


class TestReviewPage:
    def test_the_page_boots_in_review_mode(self, world):
        sid = a_source(world)["source_id"]
        r = world["client"].get(f"/g/{sid}/")
        assert r.status_code == 200
        assert '"mode": "review"' in r.text
        # It is the viewer, not the status page.
        assert 'src="app.js"' in r.text

    def test_the_bare_link_redirects_to_the_trailing_slash(self, world):
        sid = a_source(world)["source_id"]
        r = world["client"].get(f"/g/{sid}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == f"/g/{sid}/"

    def test_an_unknown_game_is_404(self, world):
        assert world["client"].get("/g/s_nosuchgame/").status_code == 404

    def test_the_viewer_assets_are_served(self, world):
        sid = a_source(world)["source_id"]
        assert world["client"].get(f"/g/{sid}/app.js").status_code == 200
        assert world["client"].get(f"/g/{sid}/style.css").status_code == 200
        assert world["client"].get(f"/g/{sid}/nope.js").status_code == 404


class TestReviewTimeline:
    def test_it_serves_only_that_game(self, world):
        game = a_source(world)
        doc = world["client"].get(f"/g/{game['source_id']}/timeline.json").json()
        assert [g["index"] for g in doc["games"]] == [game["game_index"]]
        assert doc["source"]["start_s"] == game["start_s"]

    def test_it_offers_no_share_link(self, world):
        """A review link is already public; there is no private twin to leak."""
        sid = a_source(world)["source_id"]
        doc = world["client"].get(f"/g/{sid}/timeline.json").json()
        assert doc.get("chart", {}).get("share_url") is None


class TestReviewIsReadOnly:
    def test_there_is_no_way_to_write(self, world):
        """The safety property: no POST route exists under /g/ at all."""
        sid = a_source(world)["source_id"]
        r = world["client"].post(f"/g/{sid}/overrides.json", json={"0.1.1": {"user_score": 3}})
        assert r.status_code == 405

    def test_watching_creates_no_chart(self, world):
        """The bug this route exists to kill: browsing must not mint charts."""
        sid = a_source(world)["source_id"]
        before = len(world["repo"].charts)
        world["client"].get(f"/g/{sid}/")
        world["client"].get(f"/g/{sid}/timeline.json")
        assert len(world["repo"].charts) == before
