"""Practice watches and sessions in the store, and practice jobs in the queue."""

from datetime import datetime, timedelta, timezone

from curling_score.service.records import (Job, PracticeSession, PracticeWatch, Run,
                                           WatchedPlaylist)
from curling_score.service.repo import MemoryRepo

T = datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)


def watch(id="pw_1", sheet=3, status="watching"):
    return PracticeWatch(id=id, sheet=sheet, video_id="qJhDHMMtHsE", run_id="r_1",
                         job_id="j_1", created_at=T, status=status)


def session(id="ps_1", user="u_1", watch_id="pw_1", started=T, ended=None):
    return PracticeSession(id=id, user_id=user, sheet=3, watch_id=watch_id,
                           started_at=started, ended_at=ended)


def test_watches_and_sessions_round_trip_and_are_found():
    repo = MemoryRepo()
    repo.put_watch(watch())
    repo.put_watch(watch("pw_2", sheet=4))
    repo.put_session(session())
    repo.put_session(session("ps_2", started=T + timedelta(minutes=5)))
    repo.put_session(session("ps_3", user="u_2"))
    assert repo.get_watch("pw_1").sheet == 3
    assert [w.id for w in repo.watches_for_sheet(3)] == ["pw_1"]
    assert repo.update_watch("pw_1", status="ended").status == "ended"
    assert {s.id for s in repo.sessions_for_watch("pw_1")} == {"ps_1", "ps_2", "ps_3"}
    assert [s.id for s in repo.sessions_for_user("u_1")] == ["ps_2", "ps_1"]
    assert repo.update_session("ps_1", ended_at=T, end_reason="stop").end_reason == "stop"


def test_a_session_is_active_until_it_ends():
    assert session().active and not session(ended=T).active


def test_practice_records_are_backed_up():
    repo = MemoryRepo()
    repo.put_watch(watch())
    repo.put_session(session())
    other = MemoryRepo()
    other.import_all(repo.export_all())
    assert other.get_watch("pw_1").status == "watching"
    assert other.get_session("ps_1").user_id == "u_1"


def test_a_practice_job_goes_only_to_a_worker_that_asks_for_practice():
    repo = MemoryRepo()
    repo.put_run(Run(id="r_1", video_id="qJhDHMMtHsE", processing_version="v", status="queued",
                     created_at=T, kind="practice"))
    repo.put_job(Job(id="j_1", run_id="r_1", state="queued", created_at=T, run_after=T,
                     kind="practice"))
    assert repo.claim_job("w", T, kinds=("vod",)) is None
    assert repo.claim_job("w", T, kinds=("live",)) is None
    assert repo.claim_job("w", T, kinds=("live", "practice")).id == "j_1"


def test_a_playlist_can_be_a_practice_playlist():
    pl = WatchedPlaylist(id="p_1", playlist_id="PLErN0WbLUyFI", label="Practice",
                         created_at=T, practice=True)
    assert WatchedPlaylist.from_dict(pl.to_dict()).practice
    assert not WatchedPlaylist(id="p", playlist_id="x", label="l", created_at=T).practice
