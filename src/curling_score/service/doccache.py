"""The timelines the API has fetched, kept as their bytes.

Every /g/ page, chart and live poll reads a run's timeline: a whole run, both
games, ~2 MB of JSON that parses to ~13 MB of Python objects. The API used to
keep the 64 most recent *parsed* documents, keyed by version. A live game is a
new version after every end, and a practice watch after every throw, so the
cache filled with superseded copies. The instance sat at ~445 of its 512 MiB,
and a few polls landing together pushed it over: Cloud Run killed it 40 times
in the two weeks to 2026-11-10, six of them during one Monday's games.

So what is kept is:

* the bytes, not the parse. Each call parses a copy of its own. That copy is the
  private one the callers need, and parsing it is about twice as fast as
  copying a parsed document (29 against 64 ms for 2 MB);
* one version per key, a newer one replacing the older;
* at most ``max_bytes`` in all, the least recently used going first.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Callable

# ~32 whole-run timelines: a league night's five live games and the charts
# people are opening, with room to spare. They parse to ~6x this, and only
# while a request holds them.
MAX_BYTES = 64 * 1024 * 1024


class DocCache:
    def __init__(self, fetch: Callable[[str], bytes | None], max_bytes: int = MAX_BYTES):
        self._fetch = fetch
        self._max = max_bytes
        self._kept: OrderedDict[str, tuple[str | None, bytes]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()   # the API's sync routes run on a thread pool

    def load(self, key: str, version: str | None = None) -> dict:
        """A parsed copy of the document at `key` as it stood at `version`,
        the caller's own to change. KeyError when there is no such document."""
        return json.loads(self.raw(key, version))

    def raw(self, key: str, version: str | None = None) -> bytes:
        with self._lock:
            hit = self._kept.get(key)
            if hit is not None and hit[0] == version:
                self._kept.move_to_end(key)
                return hit[1]
        data = self._fetch(key)
        if data is None:
            raise KeyError(key)
        with self._lock:
            old = self._kept.pop(key, None)
            if old is not None:
                self._bytes -= len(old[1])
            # One document over the whole budget is served, not kept.
            if len(data) <= self._max:
                self._kept[key] = (version, data)
                self._bytes += len(data)
            while self._bytes > self._max:
                _, (_, gone) = self._kept.popitem(last=False)
                self._bytes -= len(gone)
        return data

    @property
    def bytes_kept(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._kept)
