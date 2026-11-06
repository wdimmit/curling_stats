"""A camera game's clock while it plays.

A league game read from its sheet's cameras is published on the YouTube
video's clock (``timeline.build_document``'s ``clock``). The Aligner finds it
from the live stream itself: once the recording has ``ALIGN_AFTER_S``, it
fetches the stream's HLS playlist and segment 0, takes a segment about
``ALIGN_BEHIND_S`` behind the live edge, puts it on the video's clock (its pts
against segment 0's, ``hls.video_time_s``) and matches it against the
``long_a`` camera (``align.match``). Two checks in a row that agree give the
clock; after that it checks again every ``RECHECK_S``, and probes where the
video turns out to have lost time (``align.ClockTrack``).

When no piece matches (``ALIGN_TRIES`` checks), the playlist no longer starts
at segment 0, or YouTube cannot be reached, the clock falls back to the
stream's start (``Run.published_at``, YouTube's actualStartTime) and says so
(``aligned: "start_time"``); later checks may still replace it. It fetches at
most ``MAX_FETCHES`` segments in a game, and never stops the game.
"""

from __future__ import annotations

import logging
import math
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from curling_score.feeds import align
from curling_score.live import hls

log = logging.getLogger(__name__)

ALIGN_AFTER_S = 150.0     # recorded before the first check
ALIGN_BEHIND_S = 90.0     # how far behind the live edge a checked segment is
ALIGN_TRIES = 4           # first checks before falling back to the stream's start
FIRST_GAP_S = 60.0        # between those first checks
RECHECK_S = align.RECHECK_S
MAX_FETCHES = 60          # segments fetched in a game, at most
STEP_S = 5.0              # how often the thread looks


def _write_piece(data: bytes) -> Path:
    path = Path(tempfile.mkdtemp(prefix="clock-")) / "segment.ts"
    path.write_bytes(data)
    return path


class Aligner:
    """The clock of one camera game: ``clock()`` is None until there is one.

    ``wall_started`` is when the camera recording's zero was (its recorder's),
    ``published_at`` the stream's start; ``head()`` how far the recording
    reaches. The fetching and matching are passed in so a test can stand in
    for YouTube and the cameras.
    """

    def __init__(self, video_id: str, sheet: int, feed_dir, wall_started: float,
                 published_at: datetime | None, *, head, now=time.time,
                 pot_provider=None, cookies=None, resolve=None,
                 fetch_text=hls.fetch_text, fetch_bytes=hls.fetch_bytes,
                 video_time=hls.video_time_s, match=align.match, write_piece=_write_piece):
        self.video_id, self.sheet = video_id, sheet
        self.camera = Path(feed_dir) / "long_a.ts"
        self.wall_started, self.published_at = wall_started, published_at
        self.head, self.now = head, now
        self.resolve = resolve or (lambda: hls.media_playlist_url(
            video_id, pot_provider=pot_provider, cookies=cookies))
        self.fetch_text, self.fetch_bytes = fetch_text, fetch_bytes
        self.video_time, self.match, self.write_piece = video_time, match, write_piece
        self.track = align.ClockTrack()
        self.url = None
        self.seg0 = None
        self.fetches = 0
        self.tries = 0
        self.next_check = None
        self.no_start = False
        self._fallback = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # --- what the lane sees -------------------------------------------------------

    def clock(self) -> dict | None:
        with self._lock:
            return self.track.clock("segment") or (dict(self._fallback) if self._fallback
                                                   else None)

    def start(self):
        self._thread = threading.Thread(target=self._run, name=f"clock-{self.video_id}",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(STEP_S):
            try:
                self.step()
            except Exception:  # noqa: BLE001 - the clock must never stop the game
                log.exception("clock of %s", self.video_id)

    # --- one look ------------------------------------------------------------------

    def step(self) -> None:
        """At most one fetch-and-match, when one is due."""
        if self.no_start or self.fetches >= MAX_FETCHES or self.head() < ALIGN_AFTER_S:
            return
        probing = self.track.probe() is not None
        if not probing and self.next_check is not None and self.now() < self.next_check:
            return
        try:
            self._look(probing)
        except hls.NoStart as exc:
            log.warning("clock of %s: %s; the stream's start stands in", self.video_id, exc)
            self.no_start = True
            self._fall_back(None)
        except Exception as exc:  # noqa: BLE001 - YouTube, the network, a bad segment
            log.warning("clock of %s: %s", self.video_id, exc)
            self._fall_back(None)
            self.next_check = self.now() + (FIRST_GAP_S if self.track.clock() is None
                                            else RECHECK_S)

    def _look(self, probing: bool) -> None:
        if self.url is None:
            self.url = self.resolve()
            if self.url is None:
                raise ValueError("the stream has no HLS playlist")
        text = self.fetch_text(self.url)
        segs = hls.segments(text, self.url, from_start=True)
        if sum(seg.extinf_s for seg in segs) < ALIGN_BEHIND_S + 10.0:
            return                     # the stream has barely begun: not a try
        if self.seg0 is None:
            self.seg0 = self._fetch(segs[0].uri)
        if probing:
            target = self.track.probe()
            seg = self._segment_at(segs, target)
            data = self._fetch(seg.uri)
            t = self.video_time(self.seg0, data)
            with self._lock:
                self.track.add_probe(t, self._match(data, t))
            return
        k = len(segs) - 1 - math.ceil(ALIGN_BEHIND_S / max(segs[-1].extinf_s, 1.0))
        data = self._fetch(segs[k].uri)
        t = self.video_time(self.seg0, data)
        found = self._match(data, t)
        with self._lock:
            had = self.track.clock() is not None
            self.track.add(t, found)
            have = self.track.clock() is not None
        if have:
            self.next_check = self.now() + RECHECK_S
            return
        self.tries += 1
        if not had and self.tries >= ALIGN_TRIES:
            self._fall_back(text)
            self.next_check = self.now() + RECHECK_S
        else:
            self.next_check = self.now() + FIRST_GAP_S

    def _fetch(self, uri: str) -> bytes:
        self.fetches += 1
        return self.fetch_bytes(uri)

    def _segment_at(self, segs, video_t: float):
        """The segment whose start, by the playlist's durations, is nearest
        ``video_t``; its exact time comes from its pts."""
        best, best_d, t = segs[0], math.inf, 0.0
        for seg in segs:
            if abs(t - video_t) < best_d:
                best, best_d = seg, abs(t - video_t)
            t += seg.extinf_s
        return best

    def _guess(self) -> float:
        steps = self.track.steps()
        if steps:
            return steps[-1][1]
        if self.published_at is not None:
            return self.wall_started - self.published_at.timestamp()
        return 0.0

    def _match(self, data: bytes, video_t: float):
        piece = self.write_piece(data)
        try:
            return self.match(piece, video_t, self.camera, self._guess(), self.sheet,
                              search_s=30.0)
        finally:
            if isinstance(piece, Path):
                piece.unlink(missing_ok=True)

    def _fall_back(self, playlist_text) -> None:
        """The stream's start, when no piece of it has been matched."""
        if self._fallback is not None or self.track.clock() is not None:
            return
        start = self.published_at or (hls.program_date_time(playlist_text)
                                      if playlist_text else None)
        if start is None:
            log.warning("clock of %s: no start time either; publishing on the cameras'",
                        self.video_id)
            offset, how = 0.0, "unknown"
        else:
            offset, how = self.wall_started - start.timestamp(), "start_time"
        with self._lock:
            self._fallback = {"kind": "cameras", "aligned": how, "offset_s": round(offset, 3),
                              "steps": [[0.0, round(offset, 3)]]}
