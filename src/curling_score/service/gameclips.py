"""Clips of the rocks a person says they threw in a game, on the API side.

Saying which rocks were yours, or opening your report, asks for clips of
them: a `ClipJob` carrying each rock as the game's timeline has it and the
long cameras' calibration, so the clips worker (service/clipworker.py) needs
nothing else. What has come of them is read back from the play's clip jobs on
the run the report reads; nothing is written to the play or the run.

A rock is named ``<game>.<end>.<shot>`` by the numbers the run detected,
which grading and a start trim keep in ``id``: a clip stays with its rock
whatever order the chart puts the end in.

Anyone signed in may also ask for a clip of any one shot from its game's page
(`ask_shot`): a clip job with no play, named for its run and rock, so asking
twice is one job, and cut to the same file a claimed rock's clip is. The
clips worker takes these before the plays' batches: somebody is waiting.
"""

import json
import logging
import re
from datetime import timedelta

from curling_score import timeline
from curling_score.game import format as format_mod
from curling_score.service import slug
from curling_score.service.records import ClipJob

log = logging.getLogger(__name__)

GAME_ROCK = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
REASONS = ("no_release", "no_camera", "video_gone", "cut_failed")
# A game still being played is recorded until its stream ends, and only then
# is its video in a cache: ask again this much later.
LIVE_RETRY_S = 300.0
# A live run is ready before its kept recording reaches `videos/`, which the
# worker remuxes in the background after the run is done: wait this long past
# ready_at before a clip job looks for the video.
LIVE_SETTLE_S = 600.0
# A clip job that failed is tried again this much later.
RETRY_S = 60.0
# A camera game's files the clips worker could not read yet -- being filed,
# or cut where the stream began -- are looked for again this much later, and
# no longer than this past the run's ready_at: after that they are gone.
NOT_READY_WAIT_S = 120.0
NOT_READY_MAX_S = 3 * 3600.0
# A job on a run not yet ready waits at most this long from when it was asked
# for: a live game's stream runs at most ~5 h (the recorders' cap).
NOT_READY_LIVE_MAX_S = 6 * 3600.0
# An overlay is ~1 KB a rock; one far bigger is not one.
MAX_OVERLAY_BYTES = 16_000
# A clip of one shot, asked for from its page: its job's id begins so.
SHOT_JOB = "cs_"
# How a clip is cut, as the clips worker says (game/clips.CLIP_STYLE, which
# must agree): 2, the arrival from the overhead camera. A clip of an older
# style is cut again when the backfill asks for that (`recut`).
CLIP_STYLE = 2
# Of each overhead panel's calibration, what a clip is drawn by.
_PANEL_KEYS = ("rect", "center_px", "px_per_m", "flipped")
# How long a game's video is taken to stay in the workers' caches after its
# run was analysed: past it, no clip of a shot is offered. A live game's
# recording is kept as long (deploy/docker-compose.worker.yml
# WORKER_RECORDING_DAYS); a download, until its cache needs the room.
SHOT_CLIP_DAYS = 21

_ROCK_KEYS = ("t_release_s", "t_rest_s", "t_enter_s", "t_hog_s", "v_hog_m_s", "t_far_s",
              "target_broom")
# What the cutter and its marks read of a line: the delivery is drawn, the
# other paths are left behind. The release is three numbers: where the hand
# let go, which the burn-in marks.
_LINE_KEYS = ("start", "at_hog", "at_broom", "at_tee", "delivery", "release")


def rock_name(game: dict, end: dict, shot: dict) -> str:
    return f"{game['index']}.{timeline.end_identity(end)}.{timeline.shot_identity(shot)}"


def clip_calibration(calibration: dict | None) -> dict:
    """The cameras a clip is cut and drawn from, out of a timeline's
    ``calibration``: the long cameras whole, and of the overhead panels the
    four facts a clip needs (the rest of their record is the hog line's)."""
    cal = calibration or {}
    out = {k: cal[k] for k in ("left", "right") if k in cal}
    for house in ("top", "bottom"):
        if isinstance(cal.get(house), dict):
            out[house] = {k: cal[house][k] for k in _PANEL_KEYS if k in cal[house]}
    return out


def camera_feed_spec(run, doc: dict | None) -> dict | None:
    """A camera game's word to the clips worker on its cameras: its sheet,
    whose layout places each view, and the clock its rocks were published
    on. None for a game read from YouTube, or one missing either."""
    source = (doc or {}).get("source") or {}
    clock = source.get("clock")
    if not isinstance(clock, dict):
        return None
    if getattr(run, "source", None) != "cameras" and clock.get("kind") != "cameras":
        return None
    sheet = getattr(run, "sheet", None) or source.get("sheet")
    if sheet is None:
        return None
    return {"kind": "cameras", "sheet": int(sheet), "clock": clock}


def _spec(calibration, rocks, feed=None, **extra) -> dict:
    return {"calibration": clip_calibration(calibration), "rocks": rocks,
            **({"feed": feed} if feed else {}), **extra}


def rock_spec(game: dict, end: dict, shot: dict) -> dict:
    """What a clip of ``shot`` is cut from: the clip job's record of a rock."""
    line = shot.get("line") or {}
    kept = {k: line[k] for k in _LINE_KEYS if k in line}
    return {"name": rock_name(game, end, shot), "house": end.get("house"),
            **{k: shot.get(k) for k in _ROCK_KEYS}, "line": kept or None}


def find_rock(doc: dict, name: str) -> dict | None:
    """The rock named ``name`` in ``doc``, as `rock_spec` records it; None
    when there is none, or it was never seen."""
    for game in doc.get("games", []):
        for end in game.get("ends", []):
            for s in end.get("shots") or []:
                if not s.get("missing") and rock_name(game, end, s) == name:
                    return rock_spec(game, end, s)
    return None


def player_rocks(doc: dict, color: str, slot: int) -> list[dict]:
    """What a clip of each rock one player threw is cut from, from ``doc`` --
    graded, and not yet cut down by `timeline.for_player`. Each of the
    player's rocks in ``doc`` is marked with its name as ``rock``, which
    for_player keeps, so the report can find its clip. A rock never seen has
    nothing to cut."""
    return [rock_spec(game, end, s) for game, end, s in _marked(doc, color, slot)]


def mark_rocks(doc: dict, color: str, slot: int) -> None:
    """Mark one player's rocks in ``doc`` with their names, as player_rocks
    does, when no clips are asked for: a star is kept by that name."""
    for _ in _marked(doc, color, slot):
        pass


def _marked(doc: dict, color: str, slot: int):
    """Each of one player's seen rocks in ``doc``, named as ``rock`` on the way."""
    fmt = format_mod.of_document(doc)
    for game in doc.get("games", []):
        for end in game.get("ends", []):
            for s in end.get("shots") or []:
                if (s.get("color") != color or timeline.slot_of(s, fmt) != slot
                        or s.get("missing")):
                    continue
                s["rock"] = rock_name(game, end, s)
                yield game, end, s


def shot_job_id(run_id: str, name: str) -> str:
    return f"{SHOT_JOB}{run_id}_{name}"


def shot_expired(run, now) -> bool:
    """Whether ``run``'s video is past the days it is taken to be kept."""
    stamps = [t for t in (run.created_at, run.ready_at, run.revised_at) if t is not None]
    return now - max(stamps) > timedelta(days=SHOT_CLIP_DAYS)


# The clip jobs a camera game's every delivery is cut by (`queue_auto`): who
# asked for them. They come after anything a person asked for, and are cut
# only while nothing is being recorded (`recording_now`).
AUTO_USER = "auto"
# The runs whose recording is going on: what an auto job waits for.
_RECORDING_STATUS = ("queued", "processing", "live")
_RECORDING_KINDS = ("live", "standby", "practice")


def is_auto(job) -> bool:
    return job.user_id == AUTO_USER


def _tier(job) -> int:
    # Somebody waiting on one shot, then a play's batch, then the auto pass.
    return 2 if is_auto(job) else (1 if job.play_id else 0)


def _claim_order(job):
    return (_tier(job), job.run_after, job.created_at)


def recording_now(repo) -> bool:
    """Whether a league game, a standby or a practice session is being
    recorded or read: the box's CPU is theirs, and auto jobs wait."""
    return any(r.kind in _RECORDING_KINDS for status in _RECORDING_STATUS
               for r in repo.list_runs(status=status, limit=500))


def queue_ahead(repo, job, now) -> int:
    """How many clip jobs the worker will take before ``job``, the one it
    is cutting now among them. The auto pass has loops of its own, and comes
    after everything a person asked for: none of it is ahead."""
    queued = sorted((j for j in repo.clip_jobs_in_state("queued")
                     if j.run_after <= now and not is_auto(j)), key=_claim_order)
    before = next((i for i, j in enumerate(queued) if j.id == job.id), len(queued))
    return before + sum(1 for j in repo.clip_jobs_in_state("running") if not is_auto(j))


def shot_state(repo, run, name: str, now) -> dict:
    """Where a clip of one shot stands, as its page shows it: ``none`` (not
    asked for), ``queued`` (with how many are ``ahead``), ``cutting``,
    ``ready``, ``gone`` (the video has left every cache) or ``failed`` (with
    its ``reason``). A job from before the run was last analysed is none."""
    made = _auto_result(repo, run, name)
    if (made or {}).get("clip") is True:
        return {"state": "ready"}
    job = repo.get_clip_job(shot_job_id(run.id, name))
    if job is None or (run.ready_at is not None and job.created_at < run.ready_at):
        if (made or {}).get("reason") == "video_gone":
            return {"state": "gone"}
        return {"state": "none"}
    if job.state == "queued":
        return {"state": "queued", "ahead": queue_ahead(repo, job, now)}
    if job.state == "running":
        return {"state": "cutting"}
    got = (job.rocks.get(name) or {}) if job.state == "done" else {}
    if got.get("clip") is True:
        return {"state": "ready"}
    if got.get("reason") == "video_gone":
        return {"state": "gone"}
    return {"state": "failed", "reason": got.get("reason") or "cut_failed"}


def ask_shot(repo, run, source_id: str, user_id: str, rock: dict,
             calibration: dict | None, now, *, feed: dict | None = None) -> dict:
    """Ask for a clip of one shot, ``rock`` as `find_rock` gives it, unless
    one is made, on its way or cannot be: a failed one is asked for again.
    Where it then stands, as `shot_state` says. One asked for while its
    stream is on says so (``spec.live``), so the clips worker waits out the
    moment its recording is in no cache rather than calling it gone."""
    name = rock["name"]
    state = shot_state(repo, run, name, now)
    if state["state"] in ("queued", "cutting", "ready", "gone"):
        return state
    if rock.get("t_release_s") is None:
        return {"state": "failed", "reason": "no_release"}
    repo.put_clip_job(ClipJob(
        id=shot_job_id(run.id, name), play_id="", user_id=user_id, source_id=source_id,
        run_id=run.id, video_id=run.video_id, state="queued", created_at=now, run_after=now,
        spec=_spec(calibration, [rock], feed,
                   **({"live": True} if run.status == "live" else {}))))
    return shot_state(repo, run, name, now)


def state_for(repo, play_id: str, run) -> dict:
    """What has come of this play's clips on ``run``, from its clip jobs
    there, oldest first: a queued or running job's rocks are on their way, a
    finished one's are its results, a failed one's failed. A rock once made
    stays made, and a later clip of it replaces it (the same file, cut again:
    `recut`); one still being cut, or that failed, leaves it as it was. Jobs
    asked for before the run was last analysed (its ``ready_at``) are
    ignored: they were cut from older times, so the report asks again with
    the run's current rocks."""
    rocks = {}
    jobs = [j for j in repo.clip_jobs_for_play(play_id) if j.run_id == run.id
            and (run.ready_at is None or j.created_at >= run.ready_at)]
    for job in sorted(jobs, key=lambda j: j.created_at):
        for r in job.spec.get("rocks", []):
            name = r["name"]
            if ((rocks.get(name) or {}).get("clip") is True
                    and not (job.state == "done" and (job.rocks.get(name) or {}).get("clip") is True)):
                continue
            if job.state in ("queued", "running"):
                rocks[name] = {"clip": False}
            elif job.state == "done":
                rocks[name] = job.rocks.get(name) or {"clip": None, "reason": "cut_failed"}
            else:
                rocks[name] = {"clip": None, "reason": "cut_failed"}
    return {"rocks": rocks, "pending": any(v.get("clip") is False for v in rocks.values())}


def recut(state: dict, name: str) -> bool:
    """Whether a rock's clip is cut in an older style: before its marks were
    burned in, or before its arrival was seen from overhead."""
    got = state["rocks"].get(name) or {}
    return got.get("clip") is True and (got.get("style") or 0) < CLIP_STYLE


def ensure(repo, play, run, rocks: list[dict], calibration: dict | None, now, *,
           again: bool = False, feed: dict | None = None) -> dict:
    """Ask for clips of those of ``rocks`` this play has nothing for on
    ``run`` yet -- none twice, and a failed one not again on that run -- and
    say where they all stand. ``again`` asks too for those whose clip is cut
    in an older style (`recut`)."""
    state = state_for(repo, play.id, run)
    todo = [r for r in rocks if r["name"] not in state["rocks"]
            or (again and recut(state, r["name"]))]
    # Rocks a shot ask or the auto pass has cut already (the same file) are
    # this play's too: kept as a batch that is done, so every later look at
    # its clips sees them, and none is asked for again once the camera files
    # are gone.
    elsewhere = cut_elsewhere(repo, run, [r["name"] for r in todo])
    if elsewhere:
        repo.put_clip_job(ClipJob(
            id=slug.new_slug(slug.SHORT_BYTES, "cj_"), play_id=play.id, user_id=play.user_id,
            source_id=play.source_id, run_id=run.id, video_id=run.video_id, state="done",
            created_at=now, run_after=now, finished_at=now,
            spec=_spec(calibration, [r for r in todo if r["name"] in elsewhere], feed),
            rocks=elsewhere))
        todo = [r for r in todo if r["name"] not in elsewhere]
    if not todo:
        return state_for(repo, play.id, run) if elsewhere else state
    repo.put_clip_job(ClipJob(
        id=slug.new_slug(slug.SHORT_BYTES, "cj_"), play_id=play.id, user_id=play.user_id,
        source_id=play.source_id, run_id=run.id, video_id=run.video_id, state="queued",
        created_at=now, run_after=now, spec=_spec(calibration, todo, feed)))
    return state_for(repo, play.id, run)


def claim(repo, worker_id: str, now, lease_s: float, *, mode: str = "ask"):
    """The next clip job for the clips worker, or None: a shot somebody
    asked for first, then the plays' batches, oldest first. ``mode="auto"``
    is the auto pass's loops: its jobs alone, and never another's.

    A lease that ran out puts its job back (or fails it, at its last try). A
    play's batch whose game is still live waits: its video reaches a cache
    only when the stream ends, and a live run's a while after that, and the
    report asks again for the game's final rocks. A shot somebody asked for
    does not: the clips worker cuts it from the recording the stream is
    being written to (game/clips.find_video), and every rock the page could
    name is in an end already published. A job whose run is gone, or was
    analysed again since it was asked for, fails. None of it touches a run.
    """
    for job in repo.clip_jobs_in_state("running"):
        if job.lease_expires_at is not None and job.lease_expires_at < now:
            _put_back(repo, job, now, "the clips worker stopped answering")
    auto = mode == "auto"
    ready = sorted((j for j in repo.clip_jobs_in_state("queued")
                    if j.run_after <= now and is_auto(j) == auto), key=_claim_order)
    for job in ready:
        run = repo.get_run(job.run_id)
        if run is None:
            repo.update_clip_job(job.id, state="failed", finished_at=now, error="its run is gone")
            continue
        batch = bool(job.play_id)
        if run.status == "live" and batch:
            repo.update_clip_job(job.id, run_after=now + timedelta(seconds=LIVE_RETRY_S))
            continue
        if run.ready_at is not None and job.created_at < run.ready_at:
            repo.update_clip_job(job.id, state="failed", finished_at=now,
                                 error="its run was analysed again since it was asked for")
            continue
        if run.kind == "live" and run.ready_at is not None and batch:
            settled = run.ready_at + timedelta(seconds=LIVE_SETTLE_S)
            if now < settled:
                repo.update_clip_job(job.id, run_after=settled)
                continue
        got = repo.take_clip_job(job.id, worker_id, now, lease_s)
        if got is not None:
            return got
    return None


def _put_back(repo, job, now, error: str) -> None:
    """A running job its worker will not finish: queued again, or failed at
    its last attempt."""
    if job.attempts >= job.max_attempts:
        repo.update_clip_job(job.id, state="failed", worker_id=None,
                             lease_expires_at=None, finished_at=now, error=error)
    else:
        repo.update_clip_job(job.id, state="queued", worker_id=None,
                             lease_expires_at=None, run_after=now)


def release(repo, worker_id: str, now) -> int:
    """The clips worker ``worker_id`` has started again: the jobs it held go
    back as if their leases had run out, without the wait. How many."""
    held = [job for job in repo.clip_jobs_in_state("running") if job.worker_id == worker_id]
    for job in held:
        _put_back(repo, job, now, "the clips worker restarted")
    return len(held)


def clean_results(job, got) -> dict:
    """The worker's word on each rock the job asked for, in the shapes the
    page reads: a clip, its marks burned in or with its overlay, or none and
    why. Anything else it says is dropped; a rock it says nothing of failed."""
    out = {}
    got = got if isinstance(got, dict) else {}
    for r in job.spec.get("rocks", []):
        name = r["name"]
        one = got.get(name)
        if isinstance(one, dict) and one.get("clip") is True:
            item = {"clip": True}
            if one.get("burned") is True:
                item["burned"] = True
            if isinstance(one.get("style"), int) and not isinstance(one.get("style"), bool):
                item["style"] = one["style"]
            lines = one.get("overlay")
            if isinstance(lines, list) and len(json.dumps(lines)) <= MAX_OVERLAY_BYTES:
                item["overlay"] = lines
            out[name] = item
        else:
            reason = one.get("reason") if isinstance(one, dict) else None
            out[name] = {"clip": None, "reason": reason if reason in REASONS else "cut_failed"}
    return out


def fail(repo, job, error: str, now) -> bool:
    """A clip job the worker could not finish: tried again shortly, or at its
    last try given up. True when it was given up."""
    if job.attempts >= job.max_attempts:
        repo.update_clip_job(job.id, state="failed", error=error, finished_at=now,
                             worker_id=None, lease_expires_at=None)
        return True
    repo.update_clip_job(job.id, state="queued", error=error, worker_id=None,
                         lease_expires_at=None, run_after=now + timedelta(seconds=RETRY_S))
    return False


def later(repo, job, run, reason: str, now) -> bool:
    """A camera game's job whose files the clips worker could not read yet:
    queued again shortly, the try it took given back -- unless the run has
    been ready for longer than NOT_READY_MAX_S, failed, or (never ready) was
    asked for longer than NOT_READY_LIVE_MAX_S ago, when its files are not
    coming and every rock is ``video_gone``. True when it was put back."""
    ready = getattr(run, "ready_at", None) if run is not None else None
    if ready is not None:
        waiting = now < ready + timedelta(seconds=NOT_READY_MAX_S)
    else:
        waiting = (run is not None and run.status != "failed"
                   and now < job.created_at + timedelta(seconds=NOT_READY_LIVE_MAX_S))
    if waiting:
        repo.update_clip_job(job.id, state="queued", error=reason, worker_id=None,
                             lease_expires_at=None, attempts=max(0, job.attempts - 1),
                             run_after=now + timedelta(seconds=NOT_READY_WAIT_S))
        return True
    rocks = {r["name"]: {"clip": None, "reason": "video_gone"} for r in job.spec.get("rocks", [])}
    repo.update_clip_job(job.id, state="done", finished_at=now, error=reason, worker_id=None,
                         lease_expires_at=None, rocks=rocks)
    return False


def auto_job_id(run_id: str, game_index, end_ident) -> str:
    """The auto clip job of one end: its rocks are named ``<g>.<e>.<s>``."""
    return f"ca_{run_id}_{game_index}.{end_ident}"


def auto_job_for(run_id: str, name: str) -> str:
    """The auto clip job that cuts the rock named ``name``."""
    game, end, _ = name.split(".")
    return auto_job_id(run_id, game, end)


def queue_auto(repo, run, doc: dict, now) -> list[str]:
    """Ask for a clip of every delivery of a camera game, one job an end, so
    its camera files can go once they are cut: each shown game's rocks seen
    released (a practice-marked game's are not). The jobs wait LIVE_SETTLE_S,
    while the game's files are filed and cut where its stream began. A game
    read from YouTube gets none. Their ids go on the run; a run with nothing
    to cut is clipped at once."""
    feed = camera_feed_spec(run, doc)
    if feed is None:
        return []
    ids = []
    for game in doc.get("games", []):
        if game.get("practice"):
            continue
        for end in game.get("ends", []):
            rocks = [rock_spec(game, end, s) for s in end.get("shots") or []
                     if not s.get("missing") and s.get("t_release_s") is not None]
            if not rocks:
                continue
            job_id = auto_job_id(run.id, game["index"], timeline.end_identity(end))
            repo.put_clip_job(ClipJob(
                id=job_id, play_id="", user_id=AUTO_USER, source_id="", run_id=run.id,
                video_id=run.video_id, state="queued", created_at=now,
                run_after=now + timedelta(seconds=LIVE_SETTLE_S),
                spec=_spec(doc.get("calibration"), rocks, feed)))
            ids.append(job_id)
    repo.update_run(run.id, auto_clips=ids, clipped_at=None if ids else now)
    return ids


def _auto_asked_since_ready(repo, run) -> bool:
    if not run.auto_clips:
        return False
    first = repo.get_clip_job(run.auto_clips[0])
    return first is not None and (run.ready_at is None or first.created_at >= run.ready_at)


def catch_up_auto(repo, load_doc, now, *, days: float = 2.0) -> int:
    """Every camera game ready in the last ``days`` whose deliveries were not
    asked for since it was last analysed (a completion that failed to ask, or
    a reprocess): asked for now (`queue_auto`). How many runs."""
    queued = 0
    for run in repo.runs_ready_since(now - timedelta(days=days)):
        if run.source != "cameras" or run.status != "ready" or _auto_asked_since_ready(repo, run):
            continue
        try:
            queue_auto(repo, run, load_doc(run), now)
        except Exception:  # noqa: BLE001 - one game's trouble must not stop the rest
            log.exception("auto clips for run %s were not asked for", run.id)
            continue
        queued += 1
    return queued


def _valid_done(job, run) -> bool:
    return (job is not None and job.state == "done"
            and (run.ready_at is None or job.created_at >= run.ready_at))


def _auto_result(repo, run, name: str) -> dict | None:
    """What the auto pass made of the rock named ``name`` on this run of it."""
    try:
        job = repo.get_clip_job(auto_job_for(run.id, name))
    except ValueError:                  # not a rock's name
        return None
    return (job.rocks.get(name) or None) if _valid_done(job, run) else None


def cut_elsewhere(repo, run, names) -> dict:
    """Of ``names``, the rocks a clip was made of on this run of their game
    by a shot ask or the auto pass, or found gone: ``{name: result}``."""
    out = {}
    for name in names:
        shot = repo.get_clip_job(shot_job_id(run.id, name))
        got = (shot.rocks.get(name) or {}) if _valid_done(shot, run) else {}
        if got.get("clip") is not True:
            got = _auto_result(repo, run, name) or got
        if got.get("clip") is True or got.get("reason") == "video_gone":
            out[name] = got
    return out


def note_auto_done(repo, job, now) -> bool:
    """After an auto job finished: when every auto job of its run has (done,
    or given up), the run is clipped -- its camera files may go. True then."""
    if job is None or not is_auto(job):
        return False
    run = repo.get_run(job.run_id)
    if run is None or not run.auto_clips:
        return False
    for job_id in run.auto_clips:
        other = repo.get_clip_job(job_id)
        if other is not None and other.state not in ("done", "failed"):
            return False
    repo.update_run(run.id, clipped_at=now)
    return True


def clipped(repo, video_id: str) -> bool:
    """Whether the newest camera run of ``video_id`` has had every delivery
    clipped: its camera files are no longer needed for that."""
    runs = [r for r in repo.runs_for_video(video_id)
            if r.source == "cameras" and r.status == "ready" and r.ready_at is not None]
    if not runs:
        return False
    return max(runs, key=lambda r: r.ready_at).clipped_at is not None
