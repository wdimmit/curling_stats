"""The usage report: who counts as a visitor, and what counts as charting."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "usage_script", Path(__file__).resolve().parents[1] / "scripts" / "usage.py")
usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage)

PHONE = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
T0 = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)   # a Tuesday, 11:00 Pacific
CHART = "7H2663P2tC2uU4rij6sb9D"
PACIFIC = ZoneInfo("America/Los_Angeles")


def entry(path, ip="203.0.113.5", method="GET", status=200, ua=PHONE, at=T0, minutes=0):
    return {"timestamp": (at + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
            "httpRequest": {"remoteIp": ip, "requestMethod": method, "status": status,
                            "requestUrl": f"https://curling.dimmit.net{path}", "userAgent": ua}}


def watch(minutes=0, **kw):
    """A browser opening a game's watch page and the data it goes on to fetch."""
    return [entry("/g/s_abc/", minutes=minutes, **kw),
            entry("/g/s_abc/app.js", minutes=minutes, status=304, **kw),
            entry("/g/s_abc/timeline.json", minutes=minutes, **kw)]


def start_chart(minutes=0, key=CHART, **kw):
    return [entry("/api/charts", method="POST", status=201, minutes=minutes, **kw),
            entry(f"/c/{key}/", minutes=minutes, **kw),
            entry(f"/c/{key}/timeline.json", minutes=minutes, **kw)]


def grade(minutes=0, key=CHART, **kw):
    return [entry(f"/c/{key}/overrides.json?merge=1&v=3", method="POST", minutes=minutes, **kw)]


def run(entries, exclude=(), tz=PACIFIC):
    visitors, excluded = usage.screen([usage.parse(e) for e in entries], exclude)
    return visitors, excluded, usage.report(visitors, tz)


def test_an_address_that_calls_admin_or_worker_routes_is_the_owner_and_is_dropped():
    home = "198.51.100.7"
    got, excluded, _ = run([entry("/api/admin/export", ip=home, ua="curl/8.5.0"),
                            *watch(ip=home, ua=MAC), *watch()])
    assert len(got) == 1
    assert excluded["owner_addresses"] == 1 and excluded["owner_requests"] == 4


def test_the_scheduler_calling_admin_routes_does_not_make_its_address_the_owner():
    _, excluded, _ = run([entry("/api/admin/poll-playlists", ip="107.178.203.140", method="POST",
                                ua="Google-Cloud-Scheduler"), *watch()])
    assert excluded["owner_addresses"] == 0


def test_exclude_ip_takes_a_phones_v6_network():
    v6 = "2607:fb91:1ee6:560a:ac39:c2d8:8091:df0e"
    got, _, _ = run(watch(ip=v6), exclude=["2607:fb91:1ee6:560a::/64"])
    assert got == {}


def test_an_address_probing_for_exploits_is_dropped_even_where_it_looks_like_a_browser():
    scanner = "195.178.110.132"
    got, excluded, _ = run([entry("/.env", ip=scanner, status=404, ua=MAC),
                            entry("/wp-json/batch/v1", ip=scanner, method="POST", status=404),
                            *watch(ip=scanner, ua=MAC)])
    assert got == {} and excluded["scanner_addresses"] == 1


def test_a_real_page_that_404s_is_not_a_scan():
    got, excluded, _ = run([entry("/apple-touch-icon.png", status=404), *watch()])
    assert len(got) == 1 and excluded["scanner_addresses"] == 0


def test_crawlers_and_command_line_clients_are_bots():
    for ua in ["Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)", "curl/8.5.0",
               "Mozilla/5.0 (compatible; CensysInspect/1.1)", "python-httpx/0.28.1", "",
               "GoogleMessages/20.2 facebookexternalhit/1.1 Facebot Twitterbot/1.0"]:
        got, excluded, _ = run(watch(ua=ua))
        assert got == {} and excluded["bot_requests"] == 3, ua


def test_a_link_preview_with_a_browser_user_agent_takes_the_page_and_nothing_else():
    got, excluded, _ = run([entry("/g/s_abc/"), entry("/favicon.ico")])
    assert got == {} and excluded["preview_clients"] == 1


def test_a_phone_rotating_inside_its_v6_network_is_one_visitor():
    a = "2607:fb91:1ee6:560a:ac39:c2d8:8091:df0e"
    b = "2607:fb91:1ee6:560a:1111:2222:3333:4444"
    got, _, rep = run([*watch(ip=a), *watch(ip=b, minutes=90)])
    assert len(got) == 1 and rep["totals"]["visitors"] == 1


def test_a_browser_update_is_not_a_new_visitor():
    newer = PHONE.replace("Chrome/140.0.0.0", "Chrome/141.0.0.0")
    got, _, _ = run([*watch(), *watch(ua=newer, minutes=60 * 24)])
    assert len(got) == 1


def test_page_views_are_the_html_pages_not_their_polling():
    poll = [entry("/c/K/status.json", minutes=m / 12) for m in range(60)]
    _, _, rep = run([entry("/"), entry("/static/site.js"), entry("/games", status=304),
                     entry("/c/K/"), *poll, entry("/s/S/"), entry("/thinking"),
                     entry("/c/K", status=302)])
    week = rep["weeks"][0]
    assert week["page_views"] == 5
    assert week["views"]["catalogue"] == 2 and week["views"]["chart"] == 1
    assert week["views"]["share"] == 1 and week["views"]["thinking"] == 1


def test_charting_is_starting_submitting_or_saving_grading():
    _, _, rep = run([*start_chart(), *grade(minutes=1), *grade(minutes=2),
                     entry("/api/submissions", method="POST", status=201, minutes=3),
                     entry("/api/games/s_abc/teams", method="POST", minutes=4)])
    day = rep["days"][0]
    assert day["charted"] == 1 and day["viewed_only"] == 0
    assert day["charts_started"] == 1 and day["videos_submitted"] == 1
    assert day["grading_saves"] == 2 and day["charts_graded"] == 1
    assert day["game_details"] == 1


def test_requests_that_changed_nothing_are_not_charting():
    _, _, rep = run([*watch(),
                     entry("/api/charts", method="POST", status=409),
                     entry("/s/S/overrides.json?merge=1", method="POST", status=403),
                     entry("/api/submissions", method="POST", status=400)])
    day = rep["days"][0]
    assert day["charted"] == 0 and day["viewed_only"] == 1
    assert day["charts_started"] == day["grading_saves"] == day["videos_submitted"] == 0


def test_viewing_only_details_do_not_make_a_charter():
    _, _, rep = run([*watch(), entry("/api/games/s_abc/league", method="POST", minutes=1)])
    assert rep["days"][0]["charted"] == 0 and rep["days"][0]["game_details"] == 1


def test_pressing_chart_on_a_game_you_already_have_is_a_reopen_not_a_new_chart():
    _, _, rep = run([*start_chart(), *grade(minutes=5), *start_chart(minutes=60 * 24)])
    t = rep["totals"]
    assert t["charts_started"] == 1 and t["charts_reopened"] == 1
    assert [d["charts_started"] for d in rep["days"]] == [1, 0]
    assert rep["days"][1]["charted"] == 1      # sitting down to chart again still counts


def test_two_different_charts_are_two_starts():
    _, _, rep = run([*start_chart(), *start_chart(minutes=10, key="OtherChartKey0000000001")])
    assert rep["totals"]["charts_started"] == 2 and rep["totals"]["charts_reopened"] == 0


def test_a_30_minute_gap_starts_a_new_session_and_sessions_take_their_type():
    _, _, rep = run([*watch(), *watch(minutes=20), *start_chart(minutes=60),
                     *grade(minutes=70), *watch(minutes=200)])
    t = rep["totals"]
    assert t["view_sessions"] == 2 and t["chart_sessions"] == 1


def test_polling_does_not_hold_a_session_open():
    poll = [entry("/c/K/status.json", minutes=m) for m in range(0, 120, 5)]
    _, _, rep = run([*watch(), *poll, *watch(minutes=120)])
    assert rep["totals"]["view_sessions"] == 2


def test_returning_visitors_are_those_seen_in_an_earlier_week():
    other = "198.51.100.99"
    _, _, rep = run([*watch(), *watch(ip=other), *watch(minutes=60 * 24 * 7)])
    first, second = rep["weeks"]
    assert first["visitors"] == 2 and first["returning"] == 0
    assert second["visitors"] == 1 and second["returning"] == 1
    assert rep["totals"]["came_back"] == 1


def test_an_evening_game_lands_on_its_local_day():
    # 19:30 Pacific on Tuesday is 02:30 UTC on Wednesday.
    evening = datetime(2026, 9, 30, 2, 30, tzinfo=timezone.utc)
    _, _, rep = run(watch(at=evening))
    assert [d["start"] for d in rep["days"]] == ["2026-09-29"]
    assert rep["weeks"][0]["start"] == "2026-09-28"           # weeks start on Monday
    _, _, utc = run(watch(at=evening), tz=ZoneInfo("UTC"))
    assert [d["start"] for d in utc["days"]] == ["2026-09-30"]


def test_the_rendered_report_names_no_address_or_chart_key():
    ip = "203.0.113.5"
    entries = [*watch(ip=ip), *start_chart(ip=ip), *grade(ip=ip, minutes=1)]
    visitors, excluded = usage.screen([usage.parse(e) for e in entries])
    text = usage.render(usage.report(visitors, PACIFIC), excluded, "test window")
    assert ip not in text and CHART not in text
    assert "1 visitors: 1 charted" in text


def test_main_reads_a_saved_dump(tmp_path, capsys):
    dump = tmp_path / "req.json"
    dump.write_text(json.dumps([*watch(), *start_chart(minutes=5)]))
    assert usage.main(["--input", str(dump), "--tz", "America/Los_Angeles", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["totals"]["visitors"] == 1 and got["totals"]["charts_started"] == 1
    assert "registered_users" not in got["totals"]     # a bare list holds no users


def test_main_refuses_a_bad_time_zone(capsys):
    assert usage.main(["--input", "unused.json", "--tz", "Mars/Olympus"]) == 2


def account(created, seen=None):
    """A `users` document as Firestore's REST API returns it."""
    fields = {"created_at": {"timestampValue": created.isoformat().replace("+00:00", "Z")}}
    if seen:
        fields["last_seen_at"] = {"timestampValue": seen.isoformat().replace("+00:00", "Z")}
    return {"name": "projects/p/databases/(default)/documents/users/u", "fields": fields}


def test_a_user_document_gives_its_two_timestamps():
    assert usage.parse_user(account(T0, T0 + timedelta(days=1))) == (T0, T0 + timedelta(days=1))
    assert usage.parse_user(account(T0)) == (T0, None)
    assert usage.parse_user({"fields": {}}) is None


def test_registered_users_count_signups_and_sign_ins_inside_the_window():
    since = T0 - timedelta(days=7)
    old, evening = T0 - timedelta(days=40), datetime(2026, 9, 30, 2, 30, tzinfo=timezone.utc)
    users = [(old, T0), (old, old), (evening, evening)]
    visitors, _ = usage.screen([usage.parse(e) for e in watch()])
    rep = usage.report(visitors, PACIFIC, users, since)
    t = rep["totals"]
    assert t["registered_users"] == 3 and t["signed_up"] == 1 and t["seen_signed_in"] == 2
    # The evening signup lands on its local Tuesday, with the day's visit.
    assert [(d["start"], d["signups"]) for d in rep["days"]] == [("2026-09-29", 1)]
    assert rep["weeks"][0]["signups"] == 1


def test_the_report_says_when_users_could_not_be_read():
    visitors, excluded = usage.screen([usage.parse(e) for e in watch()])
    text = usage.render(usage.report(visitors, PACIFIC), excluded, "w")
    assert "Registered users: could not be read." in text and "signups" not in text
    text = usage.render(usage.report(visitors, PACIFIC, [(T0, T0)], T0 - timedelta(days=1)),
                        excluded, "w")
    assert "1 registered users" in text and "signups" in text


def test_main_reads_users_saved_with_the_log(tmp_path, capsys):
    dump = tmp_path / "req.json"
    dump.write_text(json.dumps({"requests": watch(), "users": [account(T0, T0), account(T0)]}))
    assert usage.main(["--input", str(dump), "--tz", "America/Los_Angeles", "--json"]) == 0
    t = json.loads(capsys.readouterr().out)["totals"]
    assert t["registered_users"] == 2 and t["seen_signed_in"] == 1
