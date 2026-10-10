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
