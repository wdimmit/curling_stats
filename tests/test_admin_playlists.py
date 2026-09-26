"""The admin's watched playlists: added from a link, each with the nights to
watch it, and the scheduled poll that queues live streams when live is on."""

from fastapi.testclient import TestClient

from curling_score.service.api import Settings, create_app
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_service_api import ADMIN, CLUB, T0, Clock  # noqa: F401

NIGHT = [{"days": ["thu"], "start": "18:00", "end": "23:30"}]


def make(live=False):
    clock, repo = Clock(), MemoryRepo()
    yt = FakeYouTube(playlists={"PLabc": ["liveVid0001"]})
    yt.add(VideoMeta("liveVid0001", "10/1 - Sheet 3 - Thursday League", CLUB, 0.0, "live", T0))
    settings = Settings(public_base_url="https://chart.example", worker_token="w",
                        admin_token="admin-secret", allowed_channels={CLUB},
                        model_id="m-abc", live_enabled=live)
    client = TestClient(create_app(repo, MemoryStore(), yt, settings, now=clock))
    return client, repo, clock


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


class TestTheScheduledPoll:
    def test_the_channel_is_indexed_once_an_hour_not_every_poll(self):
        client, _, clock = make()
        assert "index" in client.post("/api/admin/poll-playlists", headers=ADMIN).json()
        clock.advance(360)
        assert client.post("/api/admin/poll-playlists",
                           headers=ADMIN).json().get("index") == "not due"

    def test_a_live_stream_is_queued_only_with_live_on(self):
        for live, expect in ((False, 0), (True, 1)):
            client, repo, _ = make(live=live)
            add(client, playlist_id="PLabc")
            client.post("/api/admin/poll-playlists", headers=ADMIN)
            assert len([r for r in repo.list_runs() if r.kind == "live"]) == expect
