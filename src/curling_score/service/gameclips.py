"""Clips of the rocks a person says they threw in a game, on the API side.

Saying which rocks were yours, or opening your report, asks for clips of
them: a `ClipJob` carrying each rock as the game's timeline has it and the
long cameras' calibration, so the clips worker (service/clipworker.py) needs
nothing else. What has come of them is read back from the play's clip jobs on
the run the report reads; nothing is written to the play or the run.

A rock is named ``<game>.<end>.<shot>`` by the numbers the run detected,
which grading and a start trim keep in ``id``: a clip stays with its rock
whatever order the chart puts the end in.
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

_ROCK_KEYS = ("t_release_s", "t_rest_s", "t_enter_s", "t_hog_s", "v_hog_m_s", "t_far_s",
              "target_broom")
# What the cutter and overlay read of a line; its paths are left behind.
_LINE_KEYS = ("start", "at_hog", "at_broom", "at_tee")


def rock_name(game: dict, end: dict, shot: dict) -> str:
    return f"{game['index']}.{timeline.end_identity(end)}.{timeline.shot_identity(shot)}"


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
                name = rock_name(game, end, s)
                s["rock"] = name
                line = s.get("line") or {}
                kept = {k: line[k] for k in _LINE_KEYS if k in line}
                out.append({"name": name, "house": end.get("house"),
                            **{k: s.get(k) for k in _ROCK_KEYS}, "line": kept or None})
    return out


def state_for(repo, play_id: str, run) -> dict:
    """What has come of this play's clips on ``run``, from its clip jobs
    there, oldest first: a queued or running job's rocks are on their way, a
    finished one's are its results, a failed one's failed. A rock once made
    stays made. Jobs asked for before the run was last analysed (its
    ``ready_at``) are ignored: they were cut from older times, so the report
    asks again with the run's current rocks."""
    rocks = {}
    jobs = [j for j in repo.clip_jobs_for_play(play_id) if j.run_id == run.id
            and (run.ready_at is None or j.created_at >= run.ready_at)]
    for job in sorted(jobs, key=lambda j: j.created_at):
        for r in job.spec.get("rocks", []):
            name = r["name"]
            if (rocks.get(name) or {}).get("clip") is True:
                continue
            if job.state in ("queued", "running"):
                rocks[name] = {"clip": False}
            elif job.state == "done":
                rocks[name] = job.rocks.get(name) or {"clip": None, "reason": "cut_failed"}
            else:
                rocks[name] = {"clip": None, "reason": "cut_failed"}
    return {"rocks": rocks, "pending": any(v.get("clip") is False for v in rocks.values())}


def ensure(repo, play, run, rocks: list[dict], calibration: dict | None, now) -> dict:
    """Ask for clips of those of ``rocks`` this play has nothing for on
    ``run`` yet -- none twice, and a failed one not again on that run -- and
    say where they all stand."""
    state = state_for(repo, play.id, run)
    todo = [r for r in rocks if r["name"] not in state["rocks"]]
    if not todo:
        return state
    sides = {k: v for k, v in (calibration or {}).items() if k in ("left", "right")}
    repo.put_clip_job(ClipJob(
        id=slug.new_slug(slug.SHORT_BYTES, "cj_"), play_id=play.id, user_id=play.user_id,
        source_id=play.source_id, run_id=run.id, video_id=run.video_id, state="queued",
        created_at=now, run_after=now, spec={"calibration": sides, "rocks": todo}))
    return state_for(repo, play.id, run)


def claim(repo, worker_id: str, now, lease_s: float):
    """The next clip job for the clips worker, or None.

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
                   key=lambda j: (j.run_after, j.created_at))
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
    page reads: a clip with its overlay, or none and why. Anything else it
    says is dropped; a rock it says nothing of failed."""
    out = {}
    got = got if isinstance(got, dict) else {}
    for r in job.spec.get("rocks", []):
        name = r["name"]
        one = got.get(name)
        if isinstance(one, dict) and one.get("clip") is True:
            item = {"clip": True}
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
