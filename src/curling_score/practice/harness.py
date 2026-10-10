"""Running a practice watch over a replay, and timing what it publishes.

A throw's latency is how long after it could first have been known the watch
published it: for an arrival, the moment its rest was written into the
recording; for a throw that never arrived, the moment its release had waited
`releases.NO_ARRIVAL_S` with nothing coming. When each moment was written is
read off the replay's head, sampled from a thread, since the watch's own
steps can take seconds.
"""

import json
import threading
import time
from pathlib import Path

from curling_score.practice.releases import NO_ARRIVAL_S


class HeadClock:
    """When each moment of a recording first became readable, by the wall clock.

    Between samples it is interpolated: a replay is written at a steady rate."""

    def __init__(self, clock=time.monotonic, every_s: float = 0.2):
        self.clock, self.every_s = clock, every_s
        self.samples: list = []                     # (wall, head), head increasing
        self._lock, self._stop, self._thread = threading.Lock(), threading.Event(), None

    def sample(self, head: float, wall: float | None = None) -> None:
        wall = self.clock() if wall is None else wall
        with self._lock:
            if not self.samples or head > self.samples[-1][1]:
                self.samples.append((wall, head))

    def reached(self, t: float):
        with self._lock:
            s = list(self.samples)
        if s and t <= s[0][1]:
            return s[0][0]
        for (w0, h0), (w1, h1) in zip(s, s[1:]):
            if h0 <= t <= h1:
                return w0 + (w1 - w0) * (t - h0) / (h1 - h0)
        return None

    def start(self, rec):
        def run():
            while not self._stop.is_set():
                self.sample(rec.head_s())
                self._stop.wait(self.every_s)

        self._thread = threading.Thread(target=run, name="head-clock", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()


class Sink:
    """What the watch publishes: the latest document, and each throw once, with
    its latency, as a line of ``throws.jsonl``."""

    def __init__(self, out, heads, clock=time.monotonic, show=print):
        self.out, self.heads, self.clock, self.show = Path(out), heads, clock, show
        self.out.mkdir(parents=True, exist_ok=True)
        self.seen: set = set()

    def publish(self, doc) -> None:
        now, t0 = self.clock(), doc.get("t0_s", 0.0)
        with open(self.out / "throws.jsonl", "a") as fh:
            for th in doc["throws"]:
                if th["id"] in self.seen:
                    continue
                self.seen.add(th["id"])
                known = th["t_rest_s"] if th["arrived"] else th["t_release_s"] + NO_ARRIVAL_S
                at = self.heads.reached(known - t0)
                rec = dict(th, latency_s=None if at is None else round(now - at, 2))
                fh.write(json.dumps(rec) + "\n")
                rest = (th.get("rest") or {}).get("ring", "-")
                self.show(f"{th['id']} {th['house']:6} {th['color']:6} "
                          f"split {th.get('split_s')} line {th.get('line') and th['line']['miss_m']} "
                          f"rest {rest}  latency {rec['latency_s']} s")
        (self.out / "practice.json").write_text(json.dumps(doc))


def run(watch, *, recording=None, sleep=time.sleep, idle_s: float = 0.25) -> None:
    """Step ``watch`` until it is done, resting whenever it has nothing to do.
    A ``recording`` that failed ends the run: the watch would wait for its
    footage for ever."""
    while not watch.done:
        if recording is not None and recording.failed():
            raise RuntimeError("the recording failed; see ffmpeg's error above")
        if not watch.step():
            sleep(idle_s)
