"""The ingest changes a hosted worker depends on.

Links carry a start time; the download waits out a block or reports it as
one; every cache lives under one configurable root; a rebuilt proxy is the same
file to the detection cache; and old media is pruned without touching a job in
progress. None of these need the network.
"""

import os
import time
from pathlib import Path

import pytest

from curling_score import version
from curling_score.detect import cache as detcache
from curling_score.ingest import cache, prune, source


class TestParseLink:
    def test_a_plain_watch_url_has_no_start(self):
        link = source.parse_link("https://www.youtube.com/watch?v=VXU9xwmugRg")
        assert link == source.Link("VXU9xwmugRg", None)

    def test_t_in_seconds(self):
        link = source.parse_link("https://youtu.be/VXU9xwmugRg?t=4212")
        assert link.start_s == 4212.0

    def test_t_with_units(self):
        link = source.parse_link(
            "https://www.youtube.com/watch?v=VXU9xwmugRg&t=1h10m12s")
        assert link.start_s == 4212.0

    def test_t_with_trailing_s(self):
        assert source.parse_link("https://youtu.be/VXU9xwmugRg?t=90s").start_s == 90.0

    def test_start_parameter_is_also_accepted(self):
        link = source.parse_link(
            "https://www.youtube.com/watch?v=VXU9xwmugRg&start=300")
        assert link.start_s == 300.0

    def test_fragment_form(self):
        assert source.parse_link("https://youtu.be/VXU9xwmugRg#t=2m").start_s == 120.0

    def test_playlist_context_does_not_confuse_it(self):
        link = source.parse_link(
            "https://www.youtube.com/watch?v=VXU9xwmugRg&list=PLabc&index=7&t=61")
        assert link == source.Link("VXU9xwmugRg", 61.0)

    def test_garbage_time_is_ignored_not_fatal(self):
        assert source.parse_link("https://youtu.be/VXU9xwmugRg?t=soon").start_s is None

    def test_a_bare_id_is_fine(self):
        assert source.parse_link("VXU9xwmugRg") == source.Link("VXU9xwmugRg", None)

    def test_parse_time_edge_cases(self):
        assert source.parse_time(None) is None
        assert source.parse_time("") is None
        assert source.parse_time("0") == 0.0
        assert source.parse_time("2h") == 7200.0


class TestOneCacheRoot:
    def test_the_env_var_moves_every_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURLING_SCORE_CACHE", str(tmp_path))
        assert cache.default_root() == tmp_path
        assert cache.video_path("abc") == tmp_path / "videos" / "abc.mp4"
        assert detcache.cache_dir() == tmp_path / "detections"

    def test_without_it_the_home_cache_is_used(self, monkeypatch):
        monkeypatch.delenv("CURLING_SCORE_CACHE", raising=False)
        assert cache.default_root() == Path.home() / ".cache" / "curling_score"

    def test_an_explicit_root_still_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURLING_SCORE_CACHE", "/nowhere")
        assert cache.video_path("abc", root=tmp_path).parent == tmp_path / "videos"


class TestPinnedMtime:
    """The detection cache keys on (name, size, mtime); a rebuild must match."""

    def test_two_builds_of_the_same_file_share_an_identity(self, tmp_path):
        a = tmp_path / "x.mp4"
        a.write_bytes(b"same bytes")
        cache.pin_mtime(a)
        first = detcache._file_identity(a)
        time.sleep(0.01)
        a.write_bytes(b"same bytes")  # a fresh write, new mtime
        cache.pin_mtime(a)
        assert detcache._file_identity(a) == first

    def test_different_bytes_are_still_told_apart(self, tmp_path):
        a = tmp_path / "x.mp4"
        a.write_bytes(b"abc")
        cache.pin_mtime(a)
        first = detcache._file_identity(a)
        a.write_bytes(b"abcd")
        cache.pin_mtime(a)
        assert detcache._file_identity(a) != first


class TestDownloadBackoff:
    """Blocks are waited out and then named; other errors are raised at once."""

    URL = "https://www.youtube.com/watch?v=VXU9xwmugRg"

    def _downloader_that(self, script, written=b"video"):
        """Fail with each message in ``script``, then write the file."""
        calls = []

        def download(url, opts):
            calls.append(opts)
            if len(calls) <= len(script):
                raise RuntimeError(script[len(calls) - 1])
            Path(opts["outtmpl"]).write_bytes(written)

        download.calls = calls
        return download

    def test_a_clean_download_lands_at_the_cached_path(self, tmp_path):
        dl = self._downloader_that([])
        out = cache.ensure_cached(self.URL, root=tmp_path, downloader=dl)
        assert out == tmp_path / "videos" / "VXU9xwmugRg.mp4"
        assert out.read_bytes() == b"video"
        assert not list((tmp_path / "videos").glob("*.part-*"))

    def test_the_cached_file_carries_the_pinned_mtime(self, tmp_path):
        out = cache.ensure_cached(self.URL, root=tmp_path,
                                  downloader=self._downloader_that([]))
        assert out.stat().st_mtime_ns == cache.PINNED_MTIME_NS

    def test_a_block_is_waited_out_then_retried(self, tmp_path):
        slept = []
        dl = self._downloader_that(["Sign in to confirm you're not a bot"])
        out = cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                  sleep=slept.append)
        assert out.is_file()
        assert slept == [cache.BACKOFF_S[0]]
        assert len(dl.calls) == 2

    def test_exhausting_the_attempts_names_the_block(self, tmp_path):
        slept = []
        dl = self._downloader_that(["HTTP Error 429: Too Many Requests"] * 4)
        with pytest.raises(cache.BlockedError):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                sleep=slept.append)
        assert slept == list(cache.BACKOFF_S)
        assert not cache.is_cached("VXU9xwmugRg", tmp_path)

    def test_a_worker_can_ask_for_no_in_process_waiting(self, tmp_path):
        slept = []
        dl = self._downloader_that(["not a bot"])
        with pytest.raises(cache.BlockedError):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                sleep=slept.append, attempts=1)
        assert slept == []

    def test_other_errors_are_not_retried(self, tmp_path):
        slept = []
        dl = self._downloader_that(["Video unavailable: private"])
        with pytest.raises(RuntimeError, match="private"):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl,
                                sleep=slept.append)
        assert slept == [] and len(dl.calls) == 1

    def test_an_empty_download_is_not_accepted(self, tmp_path):
        dl = self._downloader_that([], written=b"")
        with pytest.raises(RuntimeError, match="usable file"):
            cache.ensure_cached(self.URL, root=tmp_path, downloader=dl)
        assert not cache.is_cached("VXU9xwmugRg", tmp_path)

    def test_progress_is_reported_as_a_fraction(self, tmp_path):
        seen = []

        def download(url, opts):
            for hook in opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 50,
                      "total_bytes": 200})
                hook({"status": "finished"})
            Path(opts["outtmpl"]).write_bytes(b"v")

        cache.ensure_cached(self.URL, root=tmp_path, downloader=download,
                            progress_hook=lambda f, m: seen.append(f))
        assert seen == [0.25, 1.0]

    def test_cookies_and_pot_provider_come_from_the_environment(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("YTDLP_COOKIES", "/secrets/cookies.txt")
        monkeypatch.setenv("YTDLP_POT_PROVIDER", "http://pot:4416")
        dl = self._downloader_that([])
        cache.ensure_cached(self.URL, root=tmp_path, downloader=dl)
        opts = dl.calls[0]
        assert opts["cookiefile"] == "/secrets/cookies.txt"
        assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
            "http://pot:4416"]

    def test_is_blocked_message(self):
        assert cache.is_blocked_message("ERROR: Sign in to confirm you’re not a bot")
        assert cache.is_blocked_message("HTTP Error 429")
        assert not cache.is_blocked_message("Video unavailable")


class TestPrune:
    def _file(self, root, sub, name, size, age_s, now):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(b"x" * size)
        os.utime(p, (now - age_s, now - age_s))
        return p

    def test_nothing_goes_while_under_budget(self, tmp_path):
        now = 1_000_000.0
        self._file(tmp_path, "videos", "a.mp4", 1000, 99999, now)
        assert prune.prune(tmp_path, keep_gb=1.0, now=now) == []

    def test_oldest_read_goes_first(self, tmp_path):
        now = 1_000_000.0
        old = self._file(tmp_path, "videos", "old.mp4", 600, 90000, now)
        new = self._file(tmp_path, "proxies", "new.mp4", 600, 80000, now)
        removed = prune.prune(tmp_path, keep_gb=800e-9, now=now)
        assert removed == [old]
        assert new.exists()

    def test_a_file_in_use_is_never_removed(self, tmp_path):
        now = 1_000_000.0
        busy = self._file(tmp_path, "videos", "busy.mp4", 1000, 60, now)
        removed = prune.prune(tmp_path, keep_gb=100e-9, now=now)
        assert removed == [] and busy.exists()

    def test_partial_downloads_are_left_alone(self, tmp_path):
        now = 1_000_000.0
        part = self._file(tmp_path, "videos", "v.part-1-ab.mp4", 1000, 99999, now)
        assert prune.prune(tmp_path, keep_gb=0.0, now=now) == []
        assert part.exists()

    def test_detections_are_never_pruned(self, tmp_path):
        now = 1_000_000.0
        det = self._file(tmp_path, "detections", "k.npz", 5000, 99999, now)
        prune.prune(tmp_path, keep_gb=0.0, now=now)
        assert det.exists()


class TestVersion:
    def test_the_model_id_names_the_file_and_its_bytes(self, tmp_path):
        w = tmp_path / "yolo11n-club.pt"
        w.write_bytes(b"weights v1")
        first = version.model_id(w)
        assert first.startswith("yolo11n-club-")
        w.write_bytes(b"weights v2")
        assert version.model_id(w) != first

    def test_a_renamed_copy_hashes_the_same(self, tmp_path):
        a = tmp_path / "a.pt"
        b = tmp_path / "b.pt"
        a.write_bytes(b"same")
        b.write_bytes(b"same")
        assert version.model_id(a).split("-")[-1] == version.model_id(b).split("-")[-1]

    def test_no_weights_means_the_classical_detector(self):
        assert version.model_id(None) == "classical"
        assert version.processing_version(None) == (
            f"{version.PIPELINE_VERSION}+classical")
