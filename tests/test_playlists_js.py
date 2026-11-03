"""The playlists page's words and its form (frontend/core/playlists.mjs), in node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "frontend/core/playlists.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")

THU = {"days": ["thu"], "start": "18:00", "end": "23:30"}


def js(expr: str):
    script = (f"const P = await import({MODULE.as_uri()!r});\n"
              f"console.log(JSON.stringify({expr}));")
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def pl(**kw):
    """A playlist as GET /api/admin/playlists serves it."""
    return {"id": "p_1", "playlist_id": "PLabc", "label": "Thursday", "enabled": True,
            "schedule": [THU], "live_poll_s": 180.0, "idle_poll_s": 3600.0,
            "failures": 0, "last_error": None, "last_polled_at": None,
            "last_seen_video_ids": [], **kw}


def form(**kw):
    return {**js("P.emptyForm()"), **kw}


def body(f, adding=False):
    return js(f"P.bodyFrom({json.dumps(f)}, {{adding: {str(adding).lower()}}})")


class TestTheNights:
    def test_in_the_weeks_order_whatever_order_they_were_ticked(self):
        assert js('P.scheduleText([{days: ["thu", "tue"], start: "18:00", end: "23:30"}])') == \
            "Tue, Thu 18:00–23:30"
        assert js(f"P.scheduleText([{json.dumps(THU)}, "
                  '{days: ["sat"], start: "10:00", end: "14:00"}])') == \
            "Thu 18:00–23:30; Sat 10:00–14:00"

    def test_all_seven_and_none(self):
        assert js('P.daysText(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])') == "Every day"
        assert js("P.scheduleText([])") == "" and js("P.scheduleText(null)") == ""

    def test_a_late_draw_runs_into_the_next_day(self):
        assert js('P.scheduleText([{days: ["fri"], start: "22:00", end: "01:00"}])') == \
            "Fri 22:00–01:00 the next day"
        assert js('[P.pastMidnight({start: "22:00", end: "01:00"}),'
                  ' P.pastMidnight({start: "18:00", end: "23:30"}),'
                  ' P.pastMidnight({start: "", end: "01:00"})]') == [True, False, False]

    def test_a_tick_toggles_one_day_and_keeps_the_weeks_order(self):
        assert js('P.toggleDay(["thu"], "mon")') == ["mon", "thu"]
        assert js('P.toggleDay(["mon", "thu"], "mon")') == ["thu"]


class TestHowOftenItIsLookedAt:
    def test_on_its_nights_and_otherwise(self):
        assert js(f"P.pollText({json.dumps(pl())})") == \
            "Looked at every 3 min on those nights, every hour otherwise."
        assert js(f"P.pollText({json.dumps(pl(idle_poll_s=None))})") == \
            "Looked at every 3 min on those nights, and never otherwise."

    def test_with_no_nights(self):
        assert js(f"P.pollText({json.dumps(pl(schedule=[], idle_poll_s=7200))})") == \
            "Looked at every 2 hours."
        assert js(f"P.pollText({json.dumps(pl(schedule=[], idle_poll_s=None))})") == \
            "Never looked at."

    def test_intervals(self):
        assert js("[180, 90, 3600, 5400, 7200].map(P.every)") == [
            "3 min", "1.5 min", "hour", "90 min", "2 hours"]


class TestItsState:
    def test_watching_off_or_stopped_by_its_failures(self):
        assert js(f"P.statusOf({json.dumps(pl())})") == {"kind": "watching", "text": "watching"}
        assert js(f"P.statusOf({json.dumps(pl(enabled=False))})")["text"] == "off"
        assert js(f"P.statusOf({json.dumps(pl(enabled=False, failures=3))})") == \
            {"kind": "failing", "text": "stopped"}

    def test_what_went_wrong(self):
        def trouble(**kw):
            return js(f"P.troubleText({json.dumps(pl(**kw))})")
        assert trouble() is None
        assert trouble(failures=1, last_error="quota") == "The last look failed: quota"
        assert trouble(failures=2, last_error="quota") == "The last 2 looks failed: quota"
        stopped = trouble(enabled=False, failures=3, last_error="gone")
        assert stopped.startswith("Stopped after 3 failed looks: gone.")
        assert "Watch it again" in stopped

    def test_when_it_was_last_looked_at(self):
        now = "Date.parse('2026-10-19T12:00:00Z')"
        cases = {None: "never", "2026-10-19T11:59:30Z": "just now",
                 "2026-10-19T11:48:00Z": "12 min ago", "2026-10-19T07:00:00Z": "5 h ago",
                 "2026-10-16T12:00:00Z": "3 days ago"}
        for iso, want in cases.items():
            assert js(f"P.since({json.dumps(iso)}, {now})") == want, iso

    def test_the_line_under_it(self):
        def line(**kw):
            return js(f"P.lastLookText({json.dumps(pl(**kw))}, Date.parse('2026-10-19T12:00:00Z'))")
        at = "2026-10-19T11:48:00Z"
        assert line(last_polled_at=at, last_seen_video_ids=[f"v{i}" for i in range(37)]) == \
            "Last looked at 12 min ago · 37 videos seen"
        assert line(last_polled_at=at, last_seen_video_ids=["v1"]) == \
            "Last looked at 12 min ago · 1 video seen"
        assert line() == "Not looked at yet"

    def test_the_link_is_the_playlists_on_youtube(self):
        assert js('P.playlistUrl("PLab_c-1")') == "https://www.youtube.com/playlist?list=PLab_c-1"


class TestTheForm:
    def test_a_playlist_round_trips_through_the_form(self):
        p = pl(schedule=[{"days": ["tue", "thu"], "start": "18:00", "end": "23:30"}],
               live_poll_s=120.0, idle_poll_s=None)
        got = body(js(f"P.formFrom({json.dumps(p)})"))
        assert got == {"body": {"label": "Thursday",
                                "schedule": p["schedule"], "live_poll_s": 120,
                                "idle_poll_s": None}}

    def test_adding_sends_the_link_and_a_change_does_not(self):
        f = form(link="  https://www.youtube.com/playlist?list=PLabc ", label=" Thu ",
                 nights=[THU])
        assert body(f, adding=True)["body"]["playlist_id"] == \
            "https://www.youtube.com/playlist?list=PLabc"
        assert body(f, adding=True)["body"]["label"] == "Thu"
        assert "playlist_id" not in body(f)["body"]

    def test_the_defaults_are_the_records(self):
        """WatchedPlaylist's: every 3 minutes on its nights, hourly otherwise."""
        got = body(form(link="PLabc", nights=[THU]), adding=True)["body"]
        assert (got["live_poll_s"], got["idle_poll_s"]) == (180, 3600)
        assert got["label"] == ""  # the server names it by its id

    def test_a_new_night_is_an_evening_with_no_day_yet(self):
        assert js("P.newNight()") == {"days": [], "start": "18:00", "end": "23:30"}

    def test_what_is_plainly_missing_is_said_before_it_is_sent(self):
        assert "link" in body(form(), adding=True)["error"]
        assert "name" in body(form(label="  ", nights=[THU]))["error"]
        assert "day" in body(form(label="T", nights=[{**THU, "days": []}]))["error"]
        assert "start" in body(form(label="T", nights=[{**THU, "end": ""}]))["error"]
        assert "1 or more" in body(form(label="T", nights=[THU], liveMin="0"))["error"]
        assert "1 or more" in body(form(label="T", nights=[THU], idleMin=""))["error"]

    def test_never_otherwise_needs_some_nights(self):
        assert "some other time" in body(form(label="T", idleNever=True))["error"]
        assert body(form(label="T", nights=[THU], idleNever=True, idleMin=""))["body"][
            "idle_poll_s"] is None
