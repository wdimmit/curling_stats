"""The admin's watched playlists: added from a link, each with the nights to
watch it, and the scheduled poll that queues live streams when live is on.
The admin token or a signed-in admin (/playlists) keeps them; only the token
polls."""

from fastapi.testclient import TestClient

from curling_score.service.api import Settings, create_app
from curling_score.service.auth import FakeVerifier
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_service_api import ADMIN, CLUB, T0, Clock  # noqa: F401

NIGHT = [{"days": ["thu"], "start": "18:00", "end": "23:30"}]
SARAH = {"Authorization": "Bearer tok-sarah"}
ALEX = {"Authorization": "Bearer tok-alex"}


def make():
    clock, repo = Clock(), MemoryRepo()
    yt = FakeYouTube(playlists={"PLabc": ["liveVid0001"]})
    yt.add(VideoMeta("liveVid0001", "10/1 - Sheet 3 - Thursday League", CLUB, 0.0, "live", T0))
    settings = Settings(public_base_url="https://chart.example", worker_token="w",
                        admin_token="admin-secret", allowed_channels={CLUB},
                        model_id="m-abc", club_tz="America/Vancouver")
    auth = FakeVerifier()
    auth.add("tok-sarah", "uid-sarah", "sarah@example.org", "Sarah")
    auth.add("tok-alex", "uid-alex", "alex@example.org", "Alex")
    client = TestClient(create_app(repo, MemoryStore(), yt, settings, now=clock, auth=auth))
    return client, repo, clock


def give_role(client, headers, role):
    """As the owner does: the account seen once, its role set with the token."""
    uid = client.get("/api/me", headers=headers).json()["user"]["id"]
    assert client.put(f"/api/admin/users/{uid}/role", headers=ADMIN,
                      json={"role": role}).status_code == 200


def add(client, **body):
    return client.post("/api/admin/playlists", headers=ADMIN, json=body)


class TestAddingAPlaylist:
    def test_it_can_be_added_from_its_link(self):
        client, repo, _ = make()
        r = add(client, playlist_id="https://www.youtube.com/playlist?list=PLabc",
                label="Thursday", schedule=NIGHT)
        assert r.status_code == 201
        (pl,) = repo.list_playlists()
        assert (pl.playlist_id, pl.label, pl.schedule) == ("PLabc", "Thursday", NIGHT)

    def test_the_same_playlist_is_not_watched_twice(self):
        client, _, _ = make()
        add(client, playlist_id="PLabc")
        assert add(client, playlist_id="https://youtube.com/playlist?list=PLabc").status_code == 409

    def test_a_bad_schedule_is_refused(self):
        client, _, _ = make()
        assert add(client, playlist_id="PLabc", schedule="thursday nights").status_code == 422

    def test_without_a_name_it_is_named_by_its_id(self):
        client, repo, _ = make()
        for label in (None, "", "   "):
            add(client, playlist_id="PLabc", label=label)
            (pl,) = repo.list_playlists()
            assert pl.label == "PLabc"
            repo.delete_playlist(pl.id)

    def test_a_video_or_nothing_is_refused_not_watched(self):
        """Watched, it would fail three polls and switch itself off."""
        client, repo, _ = make()
        for body in ({"playlist_id": "https://www.youtube.com/watch?v=liveVid0001"},
                     {"playlist_id": "https://youtu.be/liveVid0001"},
                     {"playlist_id": ""}, {"playlist_id": None}, {"label": "Thursday"}):
            r = client.post("/api/admin/playlists", headers=ADMIN, json=body)
            assert r.status_code == 422, body
        assert repo.list_playlists() == []

    def test_a_name_too_long_for_a_league_is_refused(self):
        """The name is the league of every run it queues, and a league is 60."""
        client, repo, _ = make()
        assert add(client, playlist_id="PLabc", label="x" * 61).status_code == 422
        assert repo.list_playlists() == []


class TestChangingAPlaylist:
    def test_its_schedule_and_intervals_can_be_changed(self):
        client, repo, _ = make()
        pid = add(client, playlist_id="PLabc").json()["id"]
        r = client.patch(f"/api/admin/playlists/{pid}", headers=ADMIN,
                         json={"schedule": NIGHT, "live_poll_s": 120, "idle_poll_s": None,
                               "label": "Thu"})
        assert r.status_code == 200
        pl = repo.get_playlist(pid)
        assert (pl.schedule, pl.live_poll_s, pl.idle_poll_s, pl.label) == (NIGHT, 120, None, "Thu")

    def test_re_enabling_a_disabled_one_forgets_its_failures(self):
        client, repo, _ = make()
        pid = add(client, playlist_id="PLabc").json()["id"]
        repo.update_playlist(pid, enabled=False, failures=3, last_error="gone")
        client.patch(f"/api/admin/playlists/{pid}", headers=ADMIN, json={"enabled": True})
        pl = repo.get_playlist(pid)
        assert pl.enabled and pl.failures == 0 and pl.last_error is None

    def test_nonsense_is_refused(self):
        client, _, _ = make()
        pid = add(client, playlist_id="PLabc").json()["id"]
        assert client.patch(f"/api/admin/playlists/{pid}", headers=ADMIN,
                            json={"schedule": [{"days": ["someday"]}]}).status_code == 422
        assert client.patch(f"/api/admin/playlists/{pid}", headers=ADMIN,
                            json={"owner": "me"}).status_code == 422
        assert client.patch("/api/admin/playlists/p_nope", headers=ADMIN,
                            json={"label": "x"}).status_code == 404

    def test_it_keeps_a_name(self):
        client, repo, _ = make()
        pid = add(client, playlist_id="PLabc", label="Thursday").json()["id"]
        for label in ("", "  ", None, "x" * 61):
            assert client.patch(f"/api/admin/playlists/{pid}", headers=ADMIN,
                                json={"label": label}).status_code == 422
        assert client.patch(f"/api/admin/playlists/{pid}", headers=ADMIN,
                            json={"label": "  Thu  "}).status_code == 200
        assert repo.get_playlist(pid).label == "Thu"


class TestTheListing:
    def test_it_says_the_clubs_time_the_schedules_are_in(self):
        client, _, _ = make()
        add(client, playlist_id="PLabc", label="Thursday", schedule=NIGHT)
        got = client.get("/api/admin/playlists", headers=ADMIN).json()
        assert got["club_tz"] == "America/Vancouver"
        (pl,) = got["playlists"]
        assert (pl["playlist_id"], pl["label"], pl["schedule"]) == ("PLabc", "Thursday", NIGHT)
        assert pl["enabled"] and pl["last_seen_video_ids"] == [] and pl["created_at"]


class TestASignedInAdmin:
    """/playlists makes the token's calls as a signed-in admin."""

    def test_keeps_the_playlists(self):
        client, repo, _ = make()
        give_role(client, SARAH, "admin")
        r = client.post("/api/admin/playlists", headers=SARAH,
                        json={"playlist_id": "https://www.youtube.com/playlist?list=PLabc",
                              "label": "Thursday", "schedule": NIGHT})
        assert r.status_code == 201
        pid = r.json()["id"]
        assert [p["id"] for p in
                client.get("/api/admin/playlists", headers=SARAH).json()["playlists"]] == [pid]
        r = client.patch(f"/api/admin/playlists/{pid}", headers=SARAH, json={"enabled": False})
        assert r.status_code == 200 and r.json()["playlist"]["enabled"] is False
        assert client.delete(f"/api/admin/playlists/{pid}", headers=SARAH).status_code == 200
        assert repo.list_playlists() == []

    def test_but_does_not_poll_them(self):
        """The poll spends YouTube's quota; it is the scheduler's, with the token."""
        client, _, _ = make()
        give_role(client, SARAH, "admin")
        assert client.post("/api/admin/poll-playlists", headers=SARAH).status_code == 401

    def test_nobody_else_sees_or_touches_them(self):
        client, repo, _ = make()
        pid = add(client, playlist_id="PLabc").json()["id"]
        give_role(client, ALEX, "validated")
        for headers, code in (({}, 401), (SARAH, 403), (ALEX, 403)):
            assert client.get("/api/admin/playlists", headers=headers).status_code == code
            assert client.post("/api/admin/playlists", headers=headers,
                               json={"playlist_id": "PLother"}).status_code == code
            assert client.patch(f"/api/admin/playlists/{pid}", headers=headers,
                                json={"enabled": False}).status_code == code
            assert client.delete(f"/api/admin/playlists/{pid}", headers=headers).status_code == code
        (pl,) = repo.list_playlists()
        assert pl.id == pid and pl.enabled


class TestTheScheduledPoll:
    def test_the_channel_is_indexed_once_an_hour_not_every_poll(self):
        client, _, clock = make()
        assert "index" in client.post("/api/admin/poll-playlists", headers=ADMIN).json()
        clock.advance(360)
        assert client.post("/api/admin/poll-playlists",
                           headers=ADMIN).json().get("index") == "not due"

    def test_a_live_stream_is_queued_as_a_live_run(self):
        client, repo, _ = make()
        add(client, playlist_id="PLabc")
        client.post("/api/admin/poll-playlists", headers=ADMIN)
        assert len([r for r in repo.list_runs() if r.kind == "live"]) == 1
