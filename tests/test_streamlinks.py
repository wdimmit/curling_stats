"""A shot outside its own stream points at the stream on its sheet that shows
it -- the league before or after -- or at none (service/streamlinks.py)."""

from datetime import datetime, timedelta, timezone

from curling_score.service import streamlinks
from curling_score.service.records import Run

T = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)      # 19:00 PST, the Super stream


def run(rid, vid, start, end=None, wall_t0=None, **kw):
    return Run(id=rid, video_id=vid, processing_version="p", status="ready", created_at=start,
               kind="live", source="cameras", sheet=3, published_at=start,
               stream_ended_at=end, wall_t0=wall_t0, wall_t0_end=wall_t0, **kw)


def doc(*times):
    return {"source": {"video_id": "SUPER"}, "games": [{"index": 0, "ends": [
        {"number": 1, "shots": [{"number": i + 1, "t_release_s": t, "t_video_s": t - 3.0}
                                for i, t in enumerate(times)]}]}]}


SUPPER = run("r_supper", "SUPPER", T - timedelta(hours=2), T,
             wall_t0=(T - timedelta(hours=2)).timestamp() + 1.5, next_run_id="r_super")
SUPER = run("r_super", "SUPER", T, T + timedelta(hours=4, minutes=30),
            wall_t0=T.timestamp() + 0.5, prev_run_id="r_supper")
RUNS = {r.id: r for r in (SUPPER, SUPER)}


def shots(d):
    return d["games"][0]["ends"][0]["shots"]


def test_an_early_shot_links_to_the_previous_stream():
    got = shots(streamlinks.annotate(doc(-300.0), SUPER, RUNS.get))
    # 300 s before the Super stream's t 0 is 6899 s into the Supper stream
    # (their t 0s are 7199 s apart on the wall clock).
    assert got[0]["video"] == {"id": "SUPPER", "shift_s": 7199.0}
    assert got[0]["youtube_url"] == "https://youtu.be/SUPPER?t=6896"


def test_a_late_shot_links_to_the_next_stream():
    late = run("r_supper", "SUPPER", T - timedelta(hours=2), T,
               wall_t0=(T - timedelta(hours=2)).timestamp() + 1.5, next_run_id="r_super")
    d = {"source": {"video_id": "SUPPER"}, "games": [{"index": 0, "ends": [
        {"number": 9, "shots": [{"number": 1, "t_release_s": 7500.0, "t_video_s": 7497.0}]}]}]}
    got = shots(streamlinks.annotate(d, late, RUNS.get))
    assert got[0]["video"] == {"id": "SUPER", "shift_s": -7199.0}


def test_a_shot_with_no_stream_says_none():
    first = run("r_first", "FIRST", T, None, wall_t0=T.timestamp())
    got = shots(streamlinks.annotate(doc(-120.0, 60.0), first, {}.get))
    assert got[0]["video"] is None and got[0]["youtube_url"] is None
    assert "video" not in got[1]


def test_a_shot_inside_its_stream_is_untouched():
    d = doc(10.0, 3600.0)
    got = streamlinks.annotate(d, SUPER, RUNS.get)
    assert all("video" not in s for s in shots(got))


def test_the_served_doc_is_a_copy():
    d = doc(-300.0)
    streamlinks.annotate(d, SUPER, RUNS.get)
    assert "video" not in shots(d)[0]


def test_a_youtube_run_is_left_alone():
    yt = Run(id="r_yt", video_id="YT", processing_version="p", status="ready", created_at=T,
             published_at=T)
    d = doc(-5.0)
    assert streamlinks.annotate(d, yt, RUNS.get) is d
