# Practice Sessions, Phase 2: The Practice Watch in the Worker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A worker can claim a `practice` job, record that sheet's live stream from 20 minutes back, run phase 1's `PracticeWatch` on it in the live lane ahead of league ends, publish each throw, and stop when told to. It is exercised end to end by a script with no API.

**Architecture:**
- `YtDlpRecorder` gains a lookback mode (`-live_start_index -240`).
- `LiveManager` claims a configurable set of job kinds, makes lookback recorders for practice jobs, and passes on a `stop` that the API returns from a heartbeat.
- `LiveLane` steps practice streams before any league end, and publishes `practice.json` with a small summary.
- `video_sessions` builds a `PracticeWatch` for a practice job.
- The two phase-1 findings cheap enough to fold in are also here: the broomless line, and the watch's stop and wall-clock origin.

**Tech Stack:** Python 3, pytest, yt-dlp + ffmpeg (already used by `live/recorder.py`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-09-practice-sessions-design.md`, section 1 (worker, lane priority) and section 5's phase 2. Phase 1 plan and results: `2026-10-09-practice-phase1-tracker.md` and `2026-10-09-practice-phase1.results.md`.

## Global Constraints

- **No API, records or page changes.** That is phase 3. The worker side talks to the existing worker endpoints, and the script stands in for the API.
- **Off by default:**
  - the worker claims `practice` jobs only with `WORKER_PRACTICE=1`;
  - no deploy is part of this plan.
- **Lookback** `LOOKBACK_S = 1200.0`, so `-live_start_index -240` (5 s segments).
- **Clock:** practice times are on the recording's own clock, starting at 0. The published document carries `wall_t0`, the epoch seconds of recording t = 0, for the page. The stream's absolute clock is not needed.
- **Ruling against the spec:** a practice recording that drops out does not resume; its job fails. Resuming on the same clock needs the stream's segment numbers, which a lookback recording does not pin exactly (the playlist moves between reading it and ffmpeg's own read). Recorded in the plan's ledger.
- **Ruling:** until phase 3 passes the session's Start, Start is the moment the worker starts recording: `since_s = LOOKBACK_S`.
- **Practice recordings are not kept** (`finish(keep=False)`): they are a window on a stream that runs all day, not a game.
- **Unchanged pipeline:** `detect/`, `game/` and `geometry/` are not touched. The broomless line is the existing `linetime.time_lines(without_broom=True)`.
- **Work** in worktree `.claude/worktrees/practice` on branch `practice`. Run tests in affected subsets (the full suite OOMs here), at nice 10 for anything long. Commits use the `practice:` / `live:` / `worker:` / `scripts:` prefixes and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **The real-stream check (Task 8) records from YouTube.** Ask the user before running it.

## Review Focus

1. **A league night with practice running.** The lane must not starve league ends: practice goes first only when it has work, and a step is bounded (`STEP_MAX_S`). *Test:* Task 5, `test_with_nothing_to_do_a_practice_watch_lets_a_due_end_be_built`.
2. **A stop that arrives while the watch is still calibrating** must end the job cleanly, not hang. *Test:* Task 2, `test_a_stop_while_calibrating_ends_it`.
3. **A practice recording dropping out** must fail its job (and so end its sessions), never restart on a shifted clock. *Test:* Task 3, `test_a_practice_recording_that_drops_out_cannot_resume`.
4. **A league `live` job published or completed through the practice branch, or the reverse,** would corrupt a game or lose throws. *Tests:* Task 5's practice tests, plus the existing league tests left green.
5. **A worker without `WORKER_PRACTICE`** must not claim practice jobs. *Test:* Task 6, `test_practice_is_claimed_only_when_asked_for`.

---

### Task 1: The broomless line for practice throws

**Files:**
- Modify: `src/curling_score/practice/enrich.py` (the line pass call)
- Modify: `src/curling_score/practice/throws.py` (`line` record gains `aim_x`)
- Test: `tests/test_practice_enrich.py`, `tests/test_practice_throws.py`

**Interfaces:**
- Produces: `throw_record(...)["line"]` = `{"miss_m", "side", "hog_offset_m", "confirmed", "aim_x"}`. `aim_x` is `Line.at_tee_x`: the line's x at the far tee, set only when no broom was held.

- [ ] **Step 1: Write the failing tests**

In `tests/test_practice_enrich.py`, make the recording stage accept and record keyword arguments, and add the test:

```python
def recording(calls, fail=(), kwargs=None):
    def stage(name):
        def run(shots, video, *views, model=None, **kw):
            calls.append((name, *[v.name for v in views], model))
            if kwargs is not None:
                kwargs[name] = kw
            if name in fail:
                raise RuntimeError(f"{name} fell over")
        return run
    return E.Stages(hog=stage("hog"), side_release=stage("side"),
                    broom=stage("broom"), line=stage("line"))
```

```python
def test_a_throw_nobody_held_a_broom_for_still_gets_its_line():
    # Practice is often solo: Qzh8 had a broom on 17 of 42 throws, uKWn on none.
    calls, kwargs = [], {}
    E.enrich("rec.ts", SETUPS, SIDEVIEWS, MODELS, house="top", arrival=arrival(),
             release=Release("red", 22.0, 1.0, 2.0), house_frames=[], throw_frames=[],
             stages=recording(calls, kwargs=kwargs))
    assert kwargs["line"] == {"without_broom": True}
```

In `tests/test_practice_throws.py`, add `"aim_x": None` to the expected `line` dict in `test_a_measured_throw_is_on_the_streams_clock`, and add:

```python
    def test_a_line_with_no_broom_says_where_it_was_aimed(self):
        dv = arrival()
        shot = Shot(number=1, color="red", stones=[], t_rest_s=dv.t_rest, delivery=dv)
        shot.line = line(at_broom_x=None, miss=None, side=None, at_tee_x=-0.21)
        got = throws.throw_record(shot, house="top")["line"]
        assert got == {"miss_m": None, "side": None, "hog_offset_m": 0.08,
                       "confirmed": True, "aim_x": -0.21}
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_practice_enrich.py tests/test_practice_throws.py -v`
Expected: FAIL. `kwargs["line"]` is `{}`, and the record has no `aim_x`.

- [ ] **Step 3: Implement**

In `enrich.py`, the line call becomes:

```python
        if arrival is not None:
            # The line follows the stone to where it stopped. Practice is often
            # solo, so a rock nobody held a broom for is measured too: its line
            # says where it was aimed at the far tee (as in doubles).
            _run("line", stages.line, shots, video, hog_view, dest_view,
                 model=models.line_model, without_broom=True)
```

In `throws.py`, the `line` entry becomes:

```python
        "line": None if line is None else {
            "miss_m": _r(line.miss, 4), "side": line.side,
            "hog_offset_m": _r(line.at_hog_offset, 4), "confirmed": line.confirmed,
            # Where it was aimed at the far tee: only when no broom was held.
            "aim_x": _r(getattr(line, "at_tee_x", None), 4)},
```

- [ ] **Step 4: Run them and see them pass**

Run: `python -m pytest tests/test_practice_enrich.py tests/test_practice_throws.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/enrich.py src/curling_score/practice/throws.py tests/test_practice_enrich.py tests/test_practice_throws.py
git commit -m "practice: a throw nobody held a broom for gets its line, aimed at the tee"
```

---

### Task 2: The watch can be told to stop, and says when it started

**Files:**
- Modify: `src/curling_score/practice/watch.py`
- Test: `tests/test_practice_watch.py`

**Interfaces:**
- Produces:
  - `watch.LOOKBACK_S = 1200.0`;
  - `PracticeWatch(..., wall_t0=None)`;
  - `PracticeWatch.request_stop() -> None`;
  - `document()["wall_t0"]`.
- After `request_stop()`, `step()` reads to the head (less `HEAD_MARGIN_S`) and then ends with status `ended`. If the watch was still calibrating, it ends at once.

- [ ] **Step 1: Write the failing tests**

```python
def test_told_to_stop_it_reads_what_is_recorded_then_ends():
    p, rec, docs = Pipeline(), Rec(head=20.0), []
    w = W.PracticeWatch(recording=rec, pipeline=p, models=SimpleNamespace(detector=None),
                        since_s=20.0, publish=docs.append, clock=lambda: 0.0)
    w.step()                                       # calibrated
    rec.head = 31.0
    w.request_stop()
    while w.step():
        pass
    assert w.done and w.status == "ended" and docs[-1]["status"] == "ended"
    assert p.spans[-1][1] == 29.0                  # read to the head, less its margin


def test_a_stop_while_calibrating_ends_it():
    p, rec = Pipeline(), Rec(head=5.0)             # the lookback is still arriving
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        clock=lambda: 0.0)
    w.request_stop()
    assert w.step() is True
    assert w.done and w.status == "ended" and p.calibrated == []


def test_the_document_says_when_the_recording_began_by_the_wall_clock():
    w = W.PracticeWatch(recording=Rec(), pipeline=Pipeline(), models=None, since_s=20.0,
                        wall_t0=1760000000.0)
    assert w.document()["wall_t0"] == 1760000000.0
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_practice_watch.py -v`
Expected: FAIL with `AttributeError: 'PracticeWatch' object has no attribute 'request_stop'`, and `TypeError` on `wall_t0`.

- [ ] **Step 3: Implement**

In `watch.py`, add the constant beside the others:

```python
# The footage before Start a watch is given to calibrate from: twenty minutes
# of the stream's DVR window, which on a long stream holds about an hour.
LOOKBACK_S = 1200.0
```

The `__init__` signature gains `wall_t0=None` (after `t0_s`) and stores `self.wall_t0 = wall_t0` and `self._stop = False`. Add:

```python
    def request_stop(self) -> None:
        """No session is watching any more: read what is recorded, then end."""
        self._stop = True
```

`step()` becomes:

```python
    def step(self) -> bool:
        if self.done:
            return False
        head, ended = self.rec.head_s(), self.rec.ended()
        if self.finders is None:
            if self._stop:
                self.status, self.done = "ended", True
                self._publish()
                return True
            return self._calibrate(head, ended)
        upto = head if ended else head - HEAD_MARGIN_S
        if upto - self.done_s >= STEP_MIN_S or ((ended or self._stop) and upto > self.done_s):
            self._watch(min(upto, self.done_s + STEP_MAX_S))
            return True
        if ended or self._stop:
            self.status, self.done = "ended", True
            self._publish()
            return True
        return False
```

`document()` gains `"wall_t0": self.wall_t0`.

- [ ] **Step 4: Run them and see them pass**

Run: `python -m pytest tests/test_practice_watch.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/practice/watch.py tests/test_practice_watch.py
git commit -m "practice: a watch told to stop reads what is recorded, then ends"
```

---

### Task 3: Recording a stream from 20 minutes back

**Files:**
- Modify: `src/curling_score/live/recorder.py`
- Test: `tests/test_live_recorder.py`

**Interfaces:**
- Produces: `recorder.SEGMENT_S = 5.0` and `YtDlpRecorder(..., lookback_s=None)`. With `lookback_s` set:
  - yt-dlp gets `ffmpeg_i:-live_start_index -{ceil(lookback_s / 5)}`;
  - the first-segment check is skipped;
  - `whole()` is always False;
  - a dropout raises `LiveError` instead of restarting.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_practice_recording_starts_its_lookback_back_from_the_live_edge(tmp_path):
    # A sheet's practice stream has run for hours; its window reaches back
    # about an hour. A watch wants twenty minutes of it to calibrate from.
    rec, spawned = make(tmp_path, [Proc(["00:20:00.00"], code=None)], seq=4321,
                        still_live=True, lookback_s=1200.0)
    rec.start()                                    # no refusal: it never wanted segment 0
    (cmd,) = spawned
    assert "ffmpeg_i:-live_start_index -240" in cmd


def test_a_practice_recording_that_drops_out_cannot_resume(tmp_path):
    procs = [Proc(["00:25:00.00"], code=1), Proc(["00:01:00.00"], code=None)]
    rec, spawned = make(tmp_path, procs, seq=4321, still_live=True, lookback_s=1200.0)
    rec.start()
    with pytest.raises(LiveError):
        for _ in range(50):
            rec.check()
            time.sleep(0.01)
    assert len(spawned) == 1


def test_a_practice_recording_is_never_whole(tmp_path):
    rec, _ = make(tmp_path, [Proc(["00:30:00.00"], code=0)], seq=4321, lookback_s=1200.0)
    rec.start()
    settle(rec)
    assert rec.ended() and not rec.whole()
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_live_recorder.py -v`
Expected: FAIL with `TypeError: YtDlpRecorder.__init__() got an unexpected keyword argument 'lookback_s'`

- [ ] **Step 3: Implement**

In `recorder.py`, add next to the other constants:

```python
# YouTube's HLS segments: a stream's clock is its segment number times this.
SEGMENT_S = 5.0
```

`__init__` gains `lookback_s: float | None = None`. Its docstring gains a sentence: "``lookback_s`` records a long-running stream from that far behind its live edge, on a clock of its own (a practice watch's): it never needs segment 0, is never whole, and does not resume after a dropout." It also sets:

```python
        self.lookback_s = lookback_s
        if lookback_s is not None:
            self.require_first_segment = False
```

In `_spawn`, the downloader argument becomes:

```python
        start = 0 if self.lookback_s is None else -math.ceil(self.lookback_s / SEGMENT_S)
        cmd = [sys.executable, "-m", "yt_dlp", "--no-part", "--newline", "--no-warnings",
               *self._extractor_args(), "-f", cache.FORMAT, "--hls-use-mpegts",
               "--downloader-args", f"ffmpeg_i:-live_start_index {start}",
               "-o", str(path), source.canonical_url(self.video_id)]
```

Add `import math` at the top. In `_check`, before the restart (`self._require_first_segment()` / `self._spawn()`):

```python
        if self.lookback_s is not None:
            # A lookback recording's clock starts wherever the window did when
            # it began; a new one would start somewhere else. Its watch has
            # nothing to stand on, so its job fails and its sessions end.
            raise LiveError("the practice recording dropped out and cannot resume on its clock")
```

- [ ] **Step 4: Run them and see them pass**

Run: `python -m pytest tests/test_live_recorder.py -v`
Expected: PASS, the existing tests included.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/live/recorder.py tests/test_live_recorder.py
git commit -m "live: record a long-running stream from twenty minutes back, for practice"
```

---

### Task 4: The manager claims practice, records it, passes on a stop

**Files:**
- Modify: `src/curling_score/live/manager.py`
- Test: `tests/test_live_lane.py` (the manager tests live there)

**Interfaces:**
- Consumes: `YtDlpRecorder(..., lookback_s=)` (Task 3), `watch.LOOKBACK_S` (Task 2).
- Produces:
  - `LiveManager(..., kinds=("live",))`;
  - `Stream.stop_requested: bool`, set when a heartbeat's response is `{"stop": true}`;
  - `manager.is_practice(job) -> bool`.

- [ ] **Step 1: Write the failing tests**

The test file's `manager()` helper gains `kinds=("live",)` and passes it to `LiveManager`. The `Api` fake's `progress` returns `{"stop": True}` for any job in a new `self.stop` set, and None otherwise. Then:

```python
def practice_job(n):
    return {"id": f"p_{n}", "run_id": f"pr_{n}", "video_id": f"pracVid000{n}",
            "kind": "practice", "sheet": n, "title": f"Sheet {n} practice"}


class TestPracticeInTheManager:
    def test_it_claims_the_kinds_it_was_given(self):
        api = Api([practice_job(1)])
        m, _ = manager(api, kinds=("live", "practice"))
        m.poll_once()
        assert api.claims[0] == ["live", "practice"]
        assert m.streams()[0].job["kind"] == "practice"

    def test_a_stop_in_a_heartbeat_is_passed_on_to_the_stream(self):
        api = Api([practice_job(1)])
        m, t = manager(api, kinds=("live", "practice"))
        m.poll_once()
        api.stop.add("p_1")
        t[0] = 61.0
        m.poll_once()
        assert m.streams()[0].stop_requested

    def test_a_practice_job_is_recorded_from_its_lookback(self, monkeypatch, tmp_path):
        from curling_score.live import recorder

        made = {}
        monkeypatch.setattr(recorder, "YtDlpRecorder",
                            lambda vid, d, **kw: made.update(vid=vid, **kw) or "rec")
        m = manager_mod.LiveManager(Api(), "home", model_id="m", gpu=None, root=tmp_path)
        assert m._recorder(practice_job(1)) == "rec"
        assert made["vid"] == "pracVid0001" and made["lookback_s"] == 1200.0
        m._recorder(live_job(1))
        assert made["lookback_s"] is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_live_lane.py -v`
Expected: FAIL. `kinds` is unexpected, and `stop_requested` doesn't exist.

- [ ] **Step 3: Implement**

In `manager.py`:

```python
def is_practice(job) -> bool:
    return job.get("kind") == "practice"
```

`Stream` gains `stop_requested: bool = False`, with the comment "the API said no session watches this practice stream any more". `LiveManager.__init__` gains `kinds=("live",)` and stores `self.kinds = tuple(kinds)`. In `poll_once`, `kinds=["live"]` becomes `kinds=list(self.kinds)`.

`_recorder` becomes:

```python
    def _recorder(self, job):
        import os

        from curling_score.live.recorder import YtDlpRecorder

        lookback = None
        if is_practice(job):
            from curling_score.practice.watch import LOOKBACK_S

            lookback = float(job.get("lookback_s") or LOOKBACK_S)
        return YtDlpRecorder(job["video_id"], self.root / "live" / job["video_id"],
                             pot_provider=os.environ.get("YTDLP_POT_PROVIDER"),
                             cookies=os.environ.get("YTDLP_COOKIES"), lookback_s=lookback)
```

In `_keep`, the progress call becomes:

```python
            got = self.api.progress(stream.job["id"], self.worker_id, "live", None,
                                    f"recorded {head / 60:.0f} min, {ends} end(s) published")
            stream.last_beat = self.clock()
            if isinstance(got, dict) and got.get("stop"):
                stream.stop_requested = True
```

The `ends` count reads `last_doc.get("games", [])` already, so a practice document (no `games`) counts 0.

- [ ] **Step 4: Run them and see them pass**

Run: `python -m pytest tests/test_live_lane.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/live/manager.py tests/test_live_lane.py
git commit -m "live: the manager claims practice jobs, records them from a lookback, passes a stop on"
```

---

### Task 5: Practice in the lane, ahead of league ends

**Files:**
- Modify: `src/curling_score/live/lane.py`
- Test: `tests/test_live_lane.py`

**Interfaces:**
- Consumes:
  - `manager.is_practice`, `Stream.stop_requested` (Task 4);
  - `PracticeWatch.step/done/request_stop`, `LOOKBACK_S`, `PracticeWatch(..., wall_t0=)` (Task 2);
  - `PracticePipeline` (phase 1).
- Produces:
  - **Each lane step:** ready practice streams are stepped first. The first that does something ends the step; only then come the league streams, exactly as before.
  - **A practice publish:** artifact `practice.json`, then `api.publish(job, worker, {"practice": {"status", "recorded_s", "throws"}, "sheet": n})`.
  - **A practice completion:** `api.complete(job, worker, {"practice": {...}, "sheet": n})`, then `finish(keep=False)`.
  - **`video_sessions`:** makes a `PracticeWatch` for a practice job, with `since_s=LOOKBACK_S` and `wall_t0=time.time() - LOOKBACK_S`.

- [ ] **Step 1: Write the failing tests**

```python
class Watch:
    """A practice watch: does work while it has some, ends when told or out of it."""

    def __init__(self, stream, publish, work=3):
        self.stream, self.publish, self.work = stream, publish, work
        self.done, self.stops, self.steps = False, 0, 0

    def request_stop(self):
        self.stops += 1
        self.work = 0

    def step(self):
        self.steps += 1
        if self.work > 0:
            self.work -= 1
            self.publish(pdoc(3 - self.work))
            return True
        self.done = True
        self.publish(pdoc(3, status="ended"))
        return True


def pdoc(n, status="watching"):
    return {"practice": 1, "status": status, "t0_s": 0.0, "wall_t0": 1.7e9,
            "since_s": 1200.0, "recorded_s": 1300.0 + n, "throws": [{"id": f"t_{n}"}] * n}


def pstream(n):
    return manager_mod.Stream(job=practice_job(n), recorder=Recorder(practice_job(n)))


class TestPracticeInTheLane:
    def test_practice_goes_before_an_end_that_is_due(self):
        api = Api()
        ln, _, _ = lane(api, [stream(1), pstream(1)], {
            "j_1": lambda st, pub: Session(st, pub, due=600.0, ends=5),
            "p_1": lambda st, pub: Watch(st, pub)})
        ln.step()
        assert [p[0] for p in api.published] == ["p_1"]

    def test_with_nothing_to_do_a_practice_watch_lets_a_due_end_be_built(self):
        class Idle(Watch):
            def step(self):
                self.steps += 1
                return False

        api = Api()
        ln, _, _ = lane(api, [pstream(1), stream(1)], {
            "j_1": lambda st, pub: Session(st, pub, due=600.0, ends=5),
            "p_1": lambda st, pub: Idle(st, pub)})
        assert ln.step()
        assert [p[0] for p in api.published] == ["j_1"]

    def test_a_practice_publish_is_its_document_and_a_summary(self):
        api = Api()
        ln, _, _ = lane(api, [pstream(1)], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()
        assert api.artifact_files[-1] == [{"name": "practice.json", "bytes": len(api.uploads[-1][1])}]
        (job_id, payload), = api.published
        assert job_id == "p_1"
        assert payload == {"practice": {"status": "watching", "recorded_s": 1301.0,
                                        "throws": 1}, "sheet": 1}

    def test_a_stop_from_the_api_reaches_the_watch_and_it_completes(self):
        api = Api()
        s = pstream(1)
        ln, m, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        s.stop_requested = True
        while ln.busy():
            ln.step()
        assert s.session.stops >= 1
        (job_id, payload), = api.completed
        assert job_id == "p_1" and payload["practice"]["status"] == "ended"
        assert m.finished == ["p_1"] and m.kept == [False]

    def test_a_practice_job_gets_a_watch_sharing_the_models(self, monkeypatch):
        from curling_score import analyze
        from curling_score.practice.watch import LOOKBACK_S, PracticeWatch

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None)
        ps = pstream(1)
        live, practice = make(stream(1), lambda d: None), make(ps, lambda d: None)
        assert isinstance(practice, PracticeWatch)
        assert practice.rec is ps.recorder
        assert practice.since_s == LOOKBACK_S and practice.models is live.models
        assert practice.wall_t0 is not None
```

The `Api` fake's `artifacts` records `files` into a new `self.artifact_files` list.

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_live_lane.py -v`
Expected: FAIL. Practice goes through the league path: there is no `next_end_due` on `Watch`, and the payload has `games`.

- [ ] **Step 3: Implement**

In `lane.py`, import `from curling_score.live.manager import is_practice` and add:

```python
def _practice_summary(doc) -> dict:
    return {"status": doc["status"], "recorded_s": doc["recorded_s"],
            "throws": len(doc["throws"])}
```

In `step()`, after the pending-resend loop and before the league logic:

```python
        # Practice first: a card is due seconds after a stone stops, an end
        # minutes after it closes. A watch with nothing to read says so, and
        # one step reads at most STEP_MAX_S, so league ends are never starved.
        for stream in ready:
            if not is_practice(stream.job):
                continue
            try:
                if stream.stop_requested:
                    stream.session.request_stop()
                did = stream.session.step()
            except Exception as exc:  # noqa: BLE001
                return self._trouble(stream, exc)
            stream.failures = 0
            if stream.session.done:
                return self._complete(stream)
            if did:
                return True
        ready = [s for s in ready if not is_practice(s.job)]
```

The rest of `step()` then runs over the league streams in `ready`, unchanged.

`_send` becomes:

```python
    def _send(self, stream) -> bool:
        from curling_score.service.worker import Lost

        job, doc = stream.job, stream.pending
        if is_practice(job):
            name = "practice.json"
            summary = {"practice": _practice_summary(doc), "sheet": _sheet(job)}
        else:
            name = "timeline.json"
            summary = {"games": _games(doc), "title": job.get("title"),
                       "sheet": _sheet(job), "duration_s": doc["live"]["recorded_s"]}
        try:
            data = json.dumps(doc).encode()
            plan = self.api.artifacts(job["id"], self.worker_id,
                                      [{"name": name, "bytes": len(data)}], [])
            for up in plan["uploads"]:
                self.api.upload(up["url"], up["headers"], data)
            self.api.publish(job["id"], self.worker_id, summary)
        except Lost:
            raise
        except Exception as exc:  # noqa: BLE001 - the API may be down; send it again
            log.warning("live job %s: publish did not go through (%s); trying again",
                        job["id"], exc)
            stream.retry_at = self.clock() + RETRY_S
            return False
        stream.pending, stream.last_doc = None, doc
        if is_practice(job):
            log.info("practice job %s: published %d throw(s)", job["id"], len(doc["throws"]))
        else:
            log.info("live job %s: published %s end(s)", job["id"],
                     [len(g["ends"]) for g in doc["games"]])
        return True
```

In `_complete`, after the pending check:

```python
        if is_practice(stream.job):
            return self._complete_practice(stream)
```

Add:

```python
    def _complete_practice(self, stream) -> bool:
        """A practice watch that has ended: its job completed with a summary.
        Its recording is a window on a stream that runs all day, not a game,
        so it is not kept."""
        from curling_score.service.worker import Lost

        job, doc = stream.job, stream.last_doc
        summary = (_practice_summary(doc) if doc is not None
                   else {"status": "ended", "recorded_s": 0.0, "throws": 0})
        try:
            self.api.complete(job["id"], self.worker_id,
                              {"practice": summary, "sheet": _sheet(job)})
        except Lost:
            log.warning("practice job %s was taken away before it completed", job["id"])
        except Exception as exc:  # noqa: BLE001 - try again shortly
            log.warning("practice job %s: completing did not go through (%s)", job["id"], exc)
            stream.retry_at = self.clock() + RETRY_S
            return True
        self.manager.finish(stream, keep=False)
        return True
```

In `video_sessions.make`, after the models are loaded:

```python
        if is_practice(job):
            import time as _time

            from curling_score.practice.pipeline import PracticePipeline
            from curling_score.practice.watch import LOOKBACK_S, PracticeWatch

            lookback = float(job.get("lookback_s") or LOOKBACK_S)
            # Until the API passes the session's Start (phase 3), Start is now:
            # the recording began `lookback` behind the live edge.
            return PracticeWatch(
                recording=stream.recorder,
                pipeline=PracticePipeline(weights=weights, skip_longview=skip_longview,
                                          line=models.line_model is not None,
                                          progress=progress),
                models=models, since_s=lookback, wall_t0=_time.time() - lookback,
                publish=publish, progress=progress)
```

- [ ] **Step 4: Run them and see them pass**

Run: `python -m pytest tests/test_live_lane.py tests/test_live_session.py -v`
Expected: PASS, every league test included.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/live/lane.py tests/test_live_lane.py
git commit -m "live: practice watches step ahead of league ends and publish their throws"
```

---

### Task 6: `WORKER_PRACTICE` switches it on

**Files:**
- Modify: `src/curling_score/service/worker.py` (`build_live`)
- Test: `tests/test_worker.py` (`TestBuildingTheLiveLane`)

- [ ] **Step 1: Write the failing test**

```python
    def test_practice_is_claimed_only_when_asked_for(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKER_LIVE", "1")
        monkeypatch.delenv("WORKER_PRACTICE", raising=False)
        got = worker.build_live("http://api", "t", "home", root=tmp_path, weights=None,
                                start=False)
        assert got.manager.kinds == ("live",)
        monkeypatch.setenv("WORKER_PRACTICE", "1")
        got = worker.build_live("http://api", "t", "home", root=tmp_path, weights=None,
                                start=False)
        assert got.manager.kinds == ("live", "practice")
```

- [ ] **Step 2: Run it and see it fail**

Run: `python -m pytest tests/test_worker.py -k practice -v`
Expected: FAIL with `assert ('live',) == ('live', 'practice')`

- [ ] **Step 3: Implement**

In `build_live`, the `LiveManager(...)` call gains:

```python
        kinds=("live", "practice") if os.environ.get("WORKER_PRACTICE") == "1" else ("live",),
```

The docstring gains: "Practice jobs are claimed too when ``WORKER_PRACTICE=1``."

- [ ] **Step 4: Run it and see it pass**

Run: `python -m pytest tests/test_worker.py -k "Live or practice" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/worker.py tests/test_worker.py
git commit -m "worker: WORKER_PRACTICE=1 claims practice jobs"
```

---

### Task 7: A script that watches a live stream as practice, with no API

**Files:**
- Create: `scripts/practice/live_practice.py`
- Test: `tests/test_practice_live_script.py`

**Interfaces:**
- Consumes: `LiveManager`, `LiveLane`, `video_sessions` (Tasks 4-5).
- Produces:
  - `python scripts/practice/live_practice.py VIDEO_ID --minutes M --out DIR [--lookback 1200]`;
  - `live_practice.FakeApi(job, stop_after_s, clock)`, which serves one practice job, writes each uploaded document to `DIR/publishes/NNN.json` and `DIR/practice.json`, and answers heartbeats with `{"stop": true}` once `stop_after_s` has passed.

- [ ] **Step 1: Write the failing test**

```python
"""scripts/practice/live_practice.py: the stand-in API a live practice check runs against."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "practice"))
import live_practice as lp  # noqa: E402


def test_it_serves_one_practice_job_and_says_stop_when_the_time_is_up(tmp_path):
    t = [0.0]
    api = lp.FakeApi(lp.practice_job("abcdefghijk", 1200.0), out=tmp_path,
                     stop_after_s=60.0, clock=lambda: t[0])
    job = api.claim("w", "m", None, kinds=["live", "practice"])
    assert job["kind"] == "practice" and job["lookback_s"] == 1200.0
    assert api.claim("w", "m", None, kinds=["live", "practice"]) is None
    assert api.progress(job["id"], "w", "live", None, "") is None
    t[0] = 61.0
    assert api.progress(job["id"], "w", "live", None, "") == {"stop": True}


def test_each_publish_is_written_where_it_can_be_read(tmp_path):
    api = lp.FakeApi(lp.practice_job("abcdefghijk", 1200.0), out=tmp_path,
                     stop_after_s=60.0, clock=lambda: 0.0)
    plan = api.artifacts("p", "w", [{"name": "practice.json", "bytes": 2}], [])
    api.upload(plan["uploads"][0]["url"], {}, b'{"throws": []}')
    api.publish("p", "w", {"practice": {"throws": 0}})
    assert json.loads((tmp_path / "practice.json").read_text()) == {"throws": []}
    assert (tmp_path / "publishes" / "001.json").exists()
```

- [ ] **Step 2: Run it and see it fail**

Run: `python -m pytest tests/test_practice_live_script.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live_practice'`

- [ ] **Step 3: Write the script**

```python
"""Watch a live YouTube stream as a practice session, with no API.

    python scripts/practice/live_practice.py VIDEO_ID --minutes 30 --out out/practice/live

The worker's own live side -- manager thread, lookback recorder, lane, practice
watch -- runs in this process against a stand-in API that serves one practice
job, writes every published document under --out, and says stop after
--minutes. It records from YouTube: ask before running it.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def practice_job(video_id, lookback_s):
    return {"id": "p_live", "run_id": "pr_live", "video_id": video_id, "kind": "practice",
            "sheet": None, "title": f"{video_id} practice", "lookback_s": lookback_s}


class FakeApi:
    def __init__(self, job, *, out, stop_after_s, clock=time.monotonic):
        self.job, self.out, self.clock = job, Path(out), clock
        self.stop_at = clock() + stop_after_s
        self.pending, self.count, self.completed = None, 0, None
        (self.out / "publishes").mkdir(parents=True, exist_ok=True)

    def claim(self, worker_id, model_id, gpu, kinds=None):
        job, self.job = self.job, None
        return job

    def progress(self, job_id, worker_id, phase, fraction, message):
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)
        return {"stop": True} if self.clock() >= self.stop_at else None

    def artifacts(self, job_id, worker_id, files, detcache):
        return {"uploads": [{"name": f["name"], "url": f"memory://{f['name']}", "headers": {}}
                            for f in files]}

    def upload(self, url, headers, data):
        self.pending = data

    def publish(self, job_id, worker_id, payload):
        self.count += 1
        (self.out / "publishes" / f"{self.count:03d}.json").write_bytes(self.pending)
        (self.out / "practice.json").write_bytes(self.pending)
        print(f"[{time.strftime('%H:%M:%S')}] published {payload}", flush=True)

    def complete(self, job_id, worker_id, payload):
        self.completed = payload
        print(f"[{time.strftime('%H:%M:%S')}] complete {payload}", flush=True)

    def fail(self, job_id, worker_id, error, kind, retry_after_s=None):
        self.completed = {"failed": error}
        print(f"failed: {error}", flush=True)


def main(argv=None) -> int:
    from curling_score import weights as weights_mod
    from curling_score.live import lane as lane_mod, manager as manager_mod

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("video_id")
    p.add_argument("--minutes", type=float, default=30.0)
    p.add_argument("--lookback", type=float, default=1200.0)
    p.add_argument("--out", default="out/practice/live")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(a.out)
    api = FakeApi(practice_job(a.video_id, a.lookback), out=out, stop_after_s=60 * a.minutes)
    mgr = manager_mod.LiveManager(api, "practice-check", model_id="local", gpu=None,
                                  root=out, kinds=("practice",), heartbeat_s=30.0).start(poll_s=5.0)
    lane = lane_mod.LiveLane(mgr, api, "practice-check",
                             make_session=lane_mod.video_sessions(str(weights_mod.default_path())))
    try:
        while api.completed is None:
            if not lane.step():
                time.sleep(0.25)
    finally:
        mgr.stop()
    print(json.dumps(api.completed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it and see it pass**

Run: `python -m pytest tests/test_practice_live_script.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/practice/live_practice.py tests/test_practice_live_script.py
git commit -m "scripts: watch a live stream as practice, against a stand-in API"
```

---

### Task 8: Check it on a real live stream (ask first)

No code. **Stop and ask the user before Step 2:** it records a live stream from YouTube.

- [ ] **Step 1: Find a candidate.** A club stream that is live and has run for more than 20 minutes: a sheet's practice stream once the playlist runs, else a league stream (Sunday 10/11 from ~11:30).
- [ ] **Step 2: With the user's OK, run it at nice 10 for 30 minutes:**
  `PYTHONPATH=src nice -n 10 python scripts/practice/live_practice.py <VIDEO_ID> --minutes 30 --out out/practice/live-<VIDEO_ID>`
- [ ] **Step 3: Check:**
  - the recorder started ~240 segments back, so the first heartbeat's "recorded N min" is ~20 min within a minute or two;
  - the watch calibrated;
  - throws were published;
  - the stop ended the watch;
  - the job completed.

  Then compare the throws with the stream as watched: count deliveries in the window by eye from saved frames, as in phase 1's warm-up check.
- [ ] **Step 4: Write `docs/superpowers/plans/2026-10-10-practice-phase2.results.md` and commit it.**
