"""The league views report: which league each game page a visitor loaded belongs to."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "league_views_script", Path(__file__).resolve().parents[1] / "scripts" / "league_views.py")
lv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lv)

PHONE = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
T0 = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)   # a Tuesday, 11:00 Pacific
PACIFIC = ZoneInfo("America/Los_Angeles")
SUPER = "2025-2026 Tuesday Super League"


def entry(path, ip="203.0.113.5", method="GET", status=200, ua=PHONE, at=T0, minutes=0):
    return {"timestamp": (at + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
            "httpRequest": {"remoteIp": ip, "requestMethod": method, "status": status,
                            "requestUrl": f"https://curling.dimmit.net{path}", "userAgent": ua}}


def page(path, minutes=0, **kw):
    """A browser loading a page and the data it goes on to fetch."""
    return [entry(path, minutes=minutes, **kw),
            entry(f"{path}timeline.json", minutes=minutes, **kw)]


def doc(collection, key, **fields):
    """A document as Firestore's REST API returns it."""
    return {"name": f"projects/p/databases/(default)/documents/{collection}/{key}",
            "fields": {k: {"stringValue": v} if v is not None else {"nullValue": None}
                       for k, v in fields.items()}}


SOURCES = [doc("sources", "s_super", title="4/30 - Sheet 2 - Spring League",
               playlist_title=SUPER, league="Super"),
           doc("sources", "s_spring", title="4/30 - Sheet 3 - Spring League", league="Spring"),
           doc("sources", "s_label", title="Sheet two", league="Thursday Mixed"),
           doc("sources", "s_none", title="untitled")]
CHARTS = [doc("charts", "c_super", source_id="s_super", share_slug="sh_super"),
          doc("charts", "c_orphan", source_id=None, share_slug="sh_orphan")]


def run(entries, tz=PACIFIC):
    visitors, _ = lv.usage.screen([lv.usage.parse(e) for e in entries])
    return lv.report(visitors, lv.leagues_by_game(SOURCES), *lv.games_by_chart(CHARTS), tz)


def by_league(rep):
    return {x["league"]: x for x in rep["leagues"]}


def test_a_games_league_is_its_playlist_else_its_title_else_its_label():
    assert lv.leagues_by_game(SOURCES) == {"s_super": SUPER, "s_spring": "Spring League",
                                           "s_label": "Thursday Mixed", "s_none": "Other"}


def test_watch_share_and_chart_pages_all_reach_their_games_league():
    rep = run([*page("/g/s_super/"), *page("/s/sh_super/", minutes=5),
               *page("/c/c_super/", minutes=10)])
    row = by_league(rep)[SUPER]
    assert (row["watch"], row["share"], row["chart"], row["views"]) == (1, 1, 1, 3)
    assert row["games"] == 1 and row["visitors"] == 1


def test_reloads_are_views_but_not_more_visitors():
    rep = run([*page("/g/s_spring/"), *page("/g/s_spring/", minutes=1),
               *page("/g/s_spring/", ip="198.51.100.9", ua=MAC)])
    row = by_league(rep)["Spring League"]
    assert row["views"] == 3 and row["visitors"] == 2 and row["games"] == 1


def test_polling_and_other_pages_are_not_views_of_a_game():
    rep = run([*page("/g/s_super/"), entry("/g/s_super/timeline.json", minutes=1),
               entry("/g/s_super/export.json", minutes=2), *page("/games", minutes=3),
               *page("/thinking", minutes=4)])
    assert rep["totals"]["views"] == 1


def test_a_chart_or_link_not_on_record_is_counted_apart_and_last():
    rep = run([*page("/c/c_gone/"), *page("/s/sh_orphan/", minutes=1),
               *page("/g/s_label/", minutes=2)])
    assert [x["league"] for x in rep["leagues"]] == ["Thursday Mixed", lv.NOT_ON_RECORD]
    gone = by_league(rep)[lv.NOT_ON_RECORD]
    assert gone["views"] == 2 and gone["games"] == 0


def test_leagues_are_ranked_by_views():
    rep = run([*page("/g/s_spring/"), *page("/g/s_super/", minutes=1),
               *page("/g/s_super/", minutes=2)])
    assert [x["league"] for x in rep["leagues"]] == [SUPER, "Spring League"]


def test_bots_and_your_own_network_view_nothing():
    home = "198.51.100.7"
    rep = run([entry("/api/admin/export", ip=home, ua="curl/8.5.0"),
               *page("/g/s_super/", ip=home, ua=MAC),
               entry("/g/s_super/", ua="Mozilla/5.0 (compatible; Googlebot/2.1)")])
    assert rep["leagues"] == [] and rep["totals"]["views"] == 0


def test_an_evening_view_lands_in_its_local_week():
    sunday_night = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)   # Sun 21:00 Pacific
    rep = run([*page("/g/s_super/", at=sunday_night)])
    assert rep["weeks"] == ["2026-09-28"]
    assert by_league(rep)[SUPER]["weeks"] == {"2026-09-28": 1}


def test_the_rendered_report_escapes_what_a_label_could_do_to_a_terminal():
    sources = [doc("sources", "s_evil", title="x", league="Evil\x1b[2J League")]
    visitors, excluded = lv.usage.screen([lv.usage.parse(e) for e in page("/g/s_evil/")])
    rep = lv.report(visitors, lv.leagues_by_game(sources), {}, {}, PACIFIC)
    text = lv.render(rep, excluded, "window")
    assert "\x1b" not in text and "Evil\\x1b[2J League" in text
    assert "203.0.113" not in text


def test_main_reads_a_saved_dump(tmp_path, capsys):
    dump = tmp_path / "views.json"
    dump.write_text(json.dumps({"requests": [*page("/g/s_super/"), *page("/s/sh_super/")],
                                "sources": SOURCES, "charts": CHARTS}))
    assert lv.main(["--input", str(dump), "--tz", "America/Los_Angeles"]) == 0
    out = capsys.readouterr().out
    assert SUPER in out and "2 views of 1 game by 1 visitor." in out


def test_main_refuses_a_dump_saved_by_usage(tmp_path, capsys):
    dump = tmp_path / "req.json"
    dump.write_text(json.dumps({"requests": page("/g/s_super/"), "users": []}))
    assert lv.main(["--input", str(dump)]) == 2
    assert "league_views.py --save" in capsys.readouterr().err
