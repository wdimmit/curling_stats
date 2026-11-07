"""An auto-clips site (service/site.py: DFW's) cuts every rock's clip without anyone asking."""

import copy

import pytest
from fastapi.testclient import TestClient

from curling_score.service import gameclips, site
from curling_score.service.api import Settings, create_app
from curling_score.service.records import Job, Run
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore
from curling_score.service.youtube import FakeYouTube, VideoMeta
from test_service_api import CLUB, T0, VID, Clock, sample_doc, work_through


def released_doc():
    doc = sample_doc(games=1)
    end = doc["games"][0]["ends"][0]
    first = end["shots"][0]
    first["t_release_s"] = first["t_video_s"]
    second = copy.deepcopy(first)
    second.update(number=2, color="yellow", t_release_s=None)    # never seen released
    end["shots"].append(second)
    return doc


def make_world(profile):
    clock = Clock()
    repo, store, yt = MemoryRepo(), MemoryStore(), FakeYouTube()
    yt.add(VideoMeta(VID, "4/30 - Sheet 2 - Spring League", CLUB, 14392.0, "none", T0))
    settings = Settings(public_base_url="https://chart.example", worker_token="worker-secret",
                        admin_token="admin-secret", allowed_channels={CLUB}, model_id="m-abc",
                        site=profile)
    client = TestClient(create_app(repo, store, yt, settings, now=clock))
    repo.put_run(Run(id="r_1", video_id=VID, processing_version=settings.processing_version,
                     status="queued", created_at=T0, title="t"))
    repo.put_job(Job(id="j_1", run_id="r_1", state="queued", created_at=T0, run_after=T0))
    return {"client": client, "repo": repo, "store": store, "clock": clock, "settings": settings}


def clip_jobs(w):
    return sorted(j.id for s in ("queued", "running", "done", "failed")
                  for j in w["repo"].clip_jobs_in_state(s))


def test_ask_all_asks_every_released_rock_once():
    w = make_world(site.DFW)
    run = w["repo"].get_run("r_1")
    doc = released_doc()
    assert gameclips.ask_all(w["repo"], run, doc, T0) == 1
    assert gameclips.ask_all(w["repo"], run, doc, T0) == 0
    assert clip_jobs(w) == ["cs_r_1_0.1.1"]


def test_complete_asks_on_an_auto_clips_site():
    w = make_world(site.DFW)
    work_through(w, doc=released_doc())
    assert clip_jobs(w) == ["cs_r_1_0.1.1"]


def test_no_auto_clips_on_granite():
    w = make_world(site.GRANITE)
    work_through(w, doc=released_doc())
    assert clip_jobs(w) == []


def test_an_unasked_rock_reads_as_it_will_be_on_an_auto_site():
    w = make_world(site.DFW)
    work_through(w, doc=released_doc())
    sid = w["repo"].sources_for_video(VID)[0].id
    c = w["client"]
    got = c.get("/api/shot-clips", params={"path": f"/g/{sid}/", "rock": "0.1.1"}).json()
    assert got["state"] == "queued"
    got = c.get("/api/shot-clips", params={"path": f"/g/{sid}/", "rock": "0.1.2"}).json()
    assert got == {"state": "failed", "reason": "no_release"}


def test_ask_all_never_scans_the_queue(monkeypatch):
    """Asking 128 rocks at a game's end must cost reads per rock, not per rock
    times the clip queue: each scan of the queue reads every queued job."""
    w = make_world(site.DFW)
    run = w["repo"].get_run("r_1")
    scans = []
    real = w["repo"].clip_jobs_in_state
    monkeypatch.setattr(w["repo"], "clip_jobs_in_state", lambda s: scans.append(s) or real(s))
    doc = released_doc()
    gameclips.ask_all(w["repo"], run, doc, T0)
    gameclips.ask_all(w["repo"], run, doc, T0)
    assert scans == []
    assert clip_jobs(w) == ["cs_r_1_0.1.1"]


def test_an_unasked_rock_is_asked_for_when_its_page_looks():
    """If the ask at publish never happened (a timeout, a worker retry), the
    page's own look asks for it -- it must never wait on a clip nobody asked for."""
    w = make_world(site.DFW)
    work_through(w, doc=released_doc())
    w["repo"].clip_jobs.clear()
    sid = w["repo"].sources_for_video(VID)[0].id
    got = w["client"].get("/api/shot-clips", params={"path": f"/g/{sid}/", "rock": "0.1.1"}).json()
    assert got["state"] == "queued"
    assert clip_jobs(w) == ["cs_r_1_0.1.1"]
