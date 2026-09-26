"""Indexing the club's playlists, and stamping each game with the one its
stream was published in."""

from datetime import datetime, timedelta, timezone

from curling_score.service import playlist_index
from curling_score.service.records import Run, Source
from curling_score.service.repo import MemoryRepo
from curling_score.service.youtube import FakeYouTube

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
CLUB = "UCclub"


def world():
    yt = FakeYouTube()
    yt.add_playlist(CLUB, "PL_super", "2025-2026 Tuesday Super League", ["v1", "v2"])
    yt.add_playlist(CLUB, "PL_monday", "2025-2026 Monday Open League", ["v3"])
    return MemoryRepo(), yt


def source(repo, sid, video):
    repo.put_source(Source(id=sid, video_id=video, game_start_s=0.0, game_end_s=1.0,
                           current_run_id="r", game_index=0, created_at=T0))


class CountingYouTube(FakeYouTube):
    """Counts the playlist reads, which are what the quota goes on."""

    def __init__(self):
        super().__init__()
        self.reads = []

    def playlist_video_ids(self, playlist_id, max_items=5000):
        self.reads.append(playlist_id)
        return super().playlist_video_ids(playlist_id, max_items)


class TestRefresh:
    def test_the_first_pass_reads_every_playlist(self):
        repo, yt = world()
        got = playlist_index.refresh(repo, yt, [CLUB], T0)
        assert got["scanned"] == 2 and got["pending"] == 0
        entries = {e.id: e for e in repo.list_playlist_index()}
        assert entries["PL_super"].video_ids == ["v1", "v2"]
        assert entries["PL_super"].title == "2025-2026 Tuesday Super League"

    def test_an_unchanged_playlist_is_not_read_again(self):
        repo, _ = world()
        yt = CountingYouTube()
        yt.add_playlist(CLUB, "PL_super", "Super", ["v1", "v2"])
        yt.add_playlist(CLUB, "PL_monday", "Monday", ["v3"])
        playlist_index.refresh(repo, yt, [CLUB], T0)
        yt.reads.clear()
        yt.add_playlist(CLUB, "PL_monday", "Monday", ["v3", "v4"])   # one new video
        got = playlist_index.refresh(repo, yt, [CLUB], T0 + timedelta(hours=1))
        assert yt.reads == ["PL_monday"] and got["scanned"] == 1

    def test_a_week_old_entry_is_read_again_anyway(self):
        repo, yt = world()
        playlist_index.refresh(repo, yt, [CLUB], T0)
        got = playlist_index.refresh(repo, yt, [CLUB], T0 + timedelta(days=8))
        assert got["scanned"] == 2

    def test_a_pass_reads_at_most_max_scans_and_says_what_is_left(self):
        repo, yt = world()
        got = playlist_index.refresh(repo, yt, [CLUB], T0, max_scans=1)
        assert got["scanned"] == 1 and got["pending"] == 1
        got = playlist_index.refresh(repo, yt, [CLUB], T0, max_scans=1)
        assert got["scanned"] == 1 and got["pending"] == 0
        assert len(repo.list_playlist_index()) == 2

    def test_a_renamed_playlist_is_renamed_without_a_read(self):
        repo, _ = world()
        yt = CountingYouTube()
        yt.add_playlist(CLUB, "PL_super", "Super", ["v1"])
        playlist_index.refresh(repo, yt, [CLUB], T0)
        yt.reads.clear()
        yt.add_playlist(CLUB, "PL_super", "2025-2026 Tuesday Super League", ["v1"])
        got = playlist_index.refresh(repo, yt, [CLUB], T0 + timedelta(hours=1))
        assert yt.reads == [] and got["retitled"] == 1
        assert repo.list_playlist_index()[0].title == "2025-2026 Tuesday Super League"

    def test_one_unreadable_playlist_does_not_stop_the_rest(self):
        repo, _ = world()

        class Flaky(FakeYouTube):
            def playlist_video_ids(self, playlist_id, max_items=5000):
                if playlist_id == "PL_super":
                    raise RuntimeError("playlistNotFound")
                return super().playlist_video_ids(playlist_id, max_items)

        yt = Flaky()
        yt.add_playlist(CLUB, "PL_super", "Super", ["v1"])
        yt.add_playlist(CLUB, "PL_monday", "Monday", ["v3"])
        got = playlist_index.refresh(repo, yt, [CLUB], T0)
        assert got["failed"] == 1 and got["scanned"] == 1
        assert [e.id for e in repo.list_playlist_index()] == ["PL_monday"]

    def test_a_playlist_the_channel_dropped_claims_nothing(self):
        repo, yt = world()
        playlist_index.refresh(repo, yt, [CLUB], T0)
        del yt._channels[CLUB]["PL_monday"]
        got = playlist_index.refresh(repo, yt, [CLUB], T0 + timedelta(hours=1))
        assert got["emptied"] == 1
        assert {e.id: e.video_ids for e in repo.list_playlist_index()}["PL_monday"] == []


class TestStamp:
    def test_each_game_gets_its_playlist(self):
        repo, yt = world()
        source(repo, "s1", "v1")
        source(repo, "s3", "v3")
        source(repo, "s9", "v9")
        playlist_index.refresh(repo, yt, [CLUB], T0)
        assert playlist_index.stamp(repo) == 2
        assert repo.get_source("s1").playlist_title == "2025-2026 Tuesday Super League"
        assert repo.get_source("s1").playlist_id == "PL_super"
        assert repo.get_source("s3").playlist_title == "2025-2026 Monday Open League"
        assert repo.get_source("s9").playlist_title is None

    def test_stamping_again_writes_nothing(self):
        repo, yt = world()
        source(repo, "s1", "v1")
        playlist_index.refresh(repo, yt, [CLUB], T0)
        playlist_index.stamp(repo)
        assert playlist_index.stamp(repo) == 0

    def test_the_smallest_playlist_holding_a_video_wins(self):
        repo, yt = world()
        yt.add_playlist(CLUB, "PL_season", "2025-2026 GCC Curling Season",
                        ["v1", "v2", "v3", "v4", "v5"])
        source(repo, "s1", "v1")
        playlist_index.refresh(repo, yt, [CLUB], T0)
        playlist_index.stamp(repo)
        assert repo.get_source("s1").playlist_id == "PL_super"

    def test_a_stream_taken_out_of_its_playlist_is_unstamped(self):
        repo, yt = world()
        source(repo, "s1", "v1")
        playlist_index.refresh(repo, yt, [CLUB], T0)
        playlist_index.stamp(repo)
        yt.add_playlist(CLUB, "PL_super", "2025-2026 Tuesday Super League", ["v2"])
        playlist_index.refresh(repo, yt, [CLUB], T0 + timedelta(hours=1))
        assert playlist_index.stamp(repo) == 1
        assert repo.get_source("s1").playlist_title is None

    def test_only_the_sources_given_are_touched(self):
        repo, yt = world()
        source(repo, "s1", "v1")
        source(repo, "s2", "v2")
        playlist_index.refresh(repo, yt, [CLUB], T0)
        assert playlist_index.stamp(repo, [repo.get_source("s1")]) == 1
        assert repo.get_source("s2").playlist_title is None


def test_the_channels_are_the_allowed_ones_else_every_runs():
    repo = MemoryRepo()
    repo.put_run(Run(id="r1", video_id="v1", processing_version="p", status="ready", created_at=T0,
                     channel_id="UCa"))
    repo.put_run(Run(id="r2", video_id="v2", processing_version="p", status="ready", created_at=T0,
                     channel_id="UCb"))
    assert playlist_index.channels_to_index(repo, {"UCclub"}) == ["UCclub"]
    assert playlist_index.channels_to_index(repo, set()) == ["UCa", "UCb"]
