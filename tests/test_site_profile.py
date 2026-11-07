"""Site profiles: one deploy's name, nav, time zone and rules (SITE); Granite's when unset."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from curling_score.service import site
from curling_score.service.api import Settings, create_app
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore
from curling_score.service.youtube import FakeYouTube

ADMIN = {"Authorization": "Bearer admin-secret"}
WORKER = {"Authorization": "Bearer worker-secret"}


def test_from_env_default_is_granite():
    assert site.from_env({}) is site.GRANITE
    assert site.GRANITE.name == "curling.dimmit.net"
    assert not site.GRANITE.read_only and site.GRANITE.discover_ranges is None


def test_dfw_profile_and_ranges():
    p = site.from_env({"SITE": "dfw", "DISCOVER_RANGES": "2026-11-06/2026-11-09, 2026-11-13/2026-11-16"})
    assert p.key == "dfw" and p.name == "dfwcurling.dimmit.net" and p.tz == "America/Chicago"
    assert p.discover_ranges == ((date(2026, 11, 6), date(2026, 11, 9)),
                                 (date(2026, 11, 13), date(2026, 11, 16)))
    assert {"/thinking", "/ice"} <= p.nav_hidden
    assert p.read_only and p.auto_clips


def test_dfw_without_ranges_queues_nothing():
    assert site.from_env({"SITE": "dfw"}).discover_ranges == ()


def test_unknown_site_is_refused():
    with pytest.raises(ValueError):
        site.from_env({"SITE": "nowhere"})


def test_settings_take_the_sites_time_zone(monkeypatch):
    monkeypatch.setenv("SITE", "dfw")
    monkeypatch.delenv("CLUB_TZ", raising=False)
    s = Settings.from_env()
    assert s.site.key == "dfw" and s.club_tz == "America/Chicago"


def client(profile):
    settings = Settings(public_base_url="https://x.example", worker_token="worker-secret",
                        admin_token="admin-secret", model_id="m", site=profile)
    return TestClient(create_app(MemoryRepo(), MemoryStore(), FakeYouTube(), settings))


def test_api_site():
    got = client(site.DFW).get("/api/site").json()
    assert got["name"] == "dfwcurling.dimmit.net" and got["read_only"] is True
    assert "/thinking" in got["nav_hidden"] and got["auto_clips"] is True
    assert client(site.GRANITE).get("/api/site").json()["read_only"] is False


def test_read_only_refuses_public_writes():
    c = client(site.DFW)
    r = c.post("/api/charts", json={"source_id": "s_x"})
    assert r.status_code == 403 and r.json()["detail"] == "this site is read-only"
    assert c.post("/api/charts", json={"source_id": "s_x"}, headers=ADMIN).status_code != 403
    assert c.post("/api/submissions", json={"url": "https://youtu.be/abcdefghijk"}).status_code == 403
    assert c.post("/api/worker/claim", headers=WORKER,
                  json={"worker_id": "w", "model_id": "m"}).status_code != 403
    assert c.get("/api/site").status_code == 200


def test_granite_writes_unchanged():
    assert client(site.GRANITE).post("/api/charts", json={"source_id": "s_x"}).status_code != 403


def test_public_posts_allowed():
    c = client(site.DFW)
    for path in site.PUBLIC_POSTS:
        assert c.post(path, json={}).status_code != 403, path


# --- pages ------------------------------------------------------------------------------

def test_dfw_terms_page_served():
    body = client(site.DFW).get("/terms").text.split("<main", 1)[1]
    assert "DFW Curling Club" in body and "dfwcurling.dimmit.net" in body
    privacy = client(site.DFW).get("/privacy").text.split("<main", 1)[1]
    assert "DFW Curling Club" in privacy and "sign in" not in privacy.lower()


def test_titles_carry_the_site_name():
    text = client(site.DFW).get("/games").text
    title = text[text.index("<title>"):text.index("</title>")]
    assert "dfwcurling.dimmit.net" in title and "curling.dimmit.net" not in title.replace("dfwcurling.dimmit.net", "")


def test_pages_carry_the_site_for_the_frontend():
    import json
    text = client(site.DFW).get("/games").text
    start = text.index("window.SITE=") + len("window.SITE=")
    got = json.loads(text[start:text.index(";</script>", start)])
    assert got["name"] == "dfwcurling.dimmit.net" and got["read_only"] is True
    assert "/ice" in got["nav_hidden"]


def test_granite_pages_unchanged():
    c = client(site.GRANITE)
    assert "window.SITE" not in c.get("/games").text
    terms = c.get("/terms").text
    assert "DFW" not in terms and "<title>Terms of use · curling.dimmit.net</title>" in terms
