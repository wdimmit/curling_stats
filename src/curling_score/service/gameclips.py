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
import re
from datetime import timedelta

from curling_score import timeline
from curling_score.game import format as format_mod
from curling_score.service import slug
from curling_score.service.records import ClipJob

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
# An overlay is ~1 KB a rock; one far bigger is not one.
MAX_OVERLAY_BYTES = 16_000
# A clip of one shot, asked for from its page: its job's id begins so.
SHOT_JOB = "cs_"
# How long a game's video is taken to stay in the workers' caches after its
# run was analysed: past it, no clip of a shot is offered.
SHOT_CLIP_DAYS = 10

_ROCK_KEYS = ("t_release_s", "t_rest_s", "t_enter_s", "t_hog_s", "v_hog_m_s", "t_far_s",
              "target_broom")
# What the cutter and its marks read of a line: the delivery is drawn, the
# other paths are left behind.
_LINE_KEYS = ("start", "at_hog", "at_broom", "at_tee", "delivery")


def rock_name(game: dict, end: dict, shot: dict) -> str:
    return f"{game['index']}.{timeline.end_identity(end)}.{timeline.shot_identity(shot)}"


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
    fmt = format_mod.of_document(doc)
    out = []
    for game in doc.get("games", []):
        for end in game.get("ends", []):
            for s in end.get("shots") or []:
                if (s.get("color") != color or timeline.slot_of(s, fmt) != slot
                        or s.get("missing")):
                    continue
                spec = rock_spec(game, end, s)
                s["rock"] = spec["name"]
                out.append(spec)
    return out


def shot_job_id(run_id: str, name: str) -> str:
    return f"{SHOT_JOB}{run_id}_{name}"


def shot_expired(run, now) -> bool:
    """Whether ``run``'s video is past the days it is taken to be kept."""
    stamps = [t for t in (run.created_at, run.ready_at, run.revised_at) if t is not None]
    return now - max(stamps) > timedelta(days=SHOT_CLIP_DAYS)


def _claim_order(job):
    # Somebody waiting on one shot comes before a play's batch.
    return (bool(job.play_id), job.run_after, job.created_at)


def queue_ahead(repo, job, now) -> int:
    """How many clip jobs the worker will take before ``job``, the one it
    is cutting now among them."""
    queued = sorted((j for j in repo.clip_jobs_in_state("queued") if j.run_after <= now),
                    key=_claim_order)
    before = next((i for i, j in enumerate(queued) if j.id == job.id), len(queued))
    return before + len(repo.clip_jobs_in_state("running"))


def shot_state(repo, run, name: str, now) -> dict:
    """Where a clip of one shot stands, as its page shows it: ``none`` (not
    asked for), ``queued`` (with how many are ``ahead``), ``cutting``,
    ``ready``, ``gone`` (the video has left every cache) or ``failed`` (with
    its ``reason``). A job from before the run was last analysed is none."""
    job = repo.get_clip_job(shot_job_id(run.id, name))
    if job is None or (run.ready_at is not None and job.created_at < run.ready_at):
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
             calibration: dict | None, now) -> dict:
    """Ask for a clip of one shot, ``rock`` as `find_rock` gives it, unless
    one is made, on its way or cannot be: a failed one is asked for again.
    Where it then stands, as `shot_state` says."""
    name = rock["name"]
    state = shot_state(repo, run, name, now)
    if state["state"] in ("queued", "cutting", "ready", "gone"):
        return state
    if rock.get("t_release_s") is None:
        return {"state": "failed", "reason": "no_release"}
    sides = {k: v for k, v in (calibration or {}).items() if k in ("left", "right")}
    repo.put_clip_job(ClipJob(
        id=shot_job_id(run.id, name), play_id="", user_id=user_id, source_id=source_id,
        run_id=run.id, video_id=run.video_id, state="queued", created_at=now, run_after=now,
        spec={"calibration": sides, "rocks": [rock]}))
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
    """Whether a rock's clip is from before marks were burned in."""
    got = state["rocks"].get(name) or {}
    return got.get("clip") is True and not got.get("burned")


def ensure(repo, play, run, rocks: list[dict], calibration: dict | None, now, *,
           again: bool = False) -> dict:
    """Ask for clips of those of ``rocks`` this play has nothing for on
    ``run`` yet -- none twice, and a failed one not again on that run -- and
    say where they all stand. ``again`` asks too for those whose clip has its
    marks drawn by the page rather than in it (`recut`)."""
    state = state_for(repo, play.id, run)
    todo = [r for r in rocks if r["name"] not in state["rocks"]
            or (again and recut(state, r["name"]))]
    if not todo:
        return state
    sides = {k: v for k, v in (calibration or {}).items() if k in ("left", "right")}
    repo.put_clip_job(ClipJob(
        id=slug.new_slug(slug.SHORT_BYTES, "cj_"), play_id=play.id, user_id=play.user_id,
        source_id=play.source_id, run_id=run.id, video_id=run.video_id, state="queued",
        created_at=now, run_after=now, spec={"calibration": sides, "rocks": todo}))
    return state_for(repo, play.id, run)


def claim(repo, worker_id: str, now, lease_s: float):
    """The next clip job for the clips worker, or None: a shot somebody
    asked for first, then the plays' batches, oldest first.

    A lease that ran out puts its job back (or fails it, at its last try). A
    job whose game is still live waits: its video reaches a cache only when
    the stream ends, and a live run's a while after that. A job whose run is
    gone, or was analysed again since it was asked for, fails. None of it touches a run.
    """
    for job in repo.clip_jobs_in_state("running"):
        if job.lease_expires_at is not None and job.lease_expires_at < now:
            if job.attempts >= job.max_attempts:
                repo.update_clip_job(job.id, state="failed", worker_id=None,
                                     lease_expires_at=None, finished_at=now,
                                     error="the clips worker stopped answering")
            else:
                repo.update_clip_job(job.id, state="queued", worker_id=None,
                                     lease_expires_at=None, run_after=now)
    ready = sorted((j for j in repo.clip_jobs_in_state("queued") if j.run_after <= now),
                   key=_claim_order)
    for job in ready:
        run = repo.get_run(job.run_id)
        if run is None:
            repo.update_clip_job(job.id, state="failed", finished_at=now, error="its run is gone")
            continue
        if run.status == "live":
            repo.update_clip_job(job.id, run_after=now + timedelta(seconds=LIVE_RETRY_S))
            continue
        if run.ready_at is not None and job.created_at < run.ready_at:
            repo.update_clip_job(job.id, state="failed", finished_at=now,
                                 error="its run was analysed again since it was asked for")
            continue
        if run.kind == "live" and run.ready_at is not None:
            settled = run.ready_at + timedelta(seconds=LIVE_SETTLE_S)
            if now < settled:
                repo.update_clip_job(job.id, run_after=settled)
                continue
        got = repo.take_clip_job(job.id, worker_id, now, lease_s)
        if got is not None:
            return got
    return None


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
