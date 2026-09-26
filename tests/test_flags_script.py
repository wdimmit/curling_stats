"""The terminal side of flags: what the owner reads back."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "flags_script", Path(__file__).resolve().parents[1] / "scripts" / "flags.py")
flags = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flags)

BASE = "https://curling.dimmit.net"


def flag(**kw):
    base = {"id": "f_abc", "status": "open", "created_at": "2026-09-25T21:03:00+00:00",
            "note": "the ghosts are on the wrong stones",
            "user": {"uid": "u1", "email": "sarah@example.org"},
            "where": {"link": "g", "chart_id": None, "share_slug": None, "source_id": "s_x",
                      "run_id": "r_1", "video_id": "VID", "processing_version": "2026.09.25+m",
                      "title": "4/30 - Sheet 2"},
            "place": {"game_index": 0, "end": 4, "end_id": "4", "rock": 16, "rock_id": "16",
                      "key": "0.4.16", "t_video_s": 3354.6, "label": "red, skip"},
            "overrides_version": None}
    base.update(kw)
    return base


def test_a_review_flag_links_to_its_rock():
    assert flags.rock_link(flag(), BASE) == f"{BASE}/g/s_x/#e=4&s=16"


def test_chart_flags_link_to_the_page_they_came_from():
    edit = flag(where={**flag()["where"], "link": "c", "chart_id": "C1", "share_slug": "S1"})
    view = flag(where={**flag()["where"], "link": "s", "chart_id": "C1", "share_slug": "S1"})
    assert flags.rock_link(edit, BASE) == f"{BASE}/c/C1/#e=4&s=16"
    assert flags.rock_link(view, BASE) == f"{BASE}/s/S1/#e=4&s=16"


def test_an_end_with_no_rocks_links_to_the_page():
    f = flag(place={**flag()["place"], "rock": None, "key": None})
    assert flags.rock_link(f, BASE) == f"{BASE}/g/s_x/"


def test_youtube_at_the_rock():
    assert flags.youtube_link(flag()) == "https://youtu.be/VID?t=3354"
    assert flags.youtube_link(flag(place={**flag()["place"], "t_video_s": None})) is None


def test_describe_says_who_where_and_what():
    text = flags.describe(flag(), BASE)
    for part in ("f_abc", "open", "sarah@example.org", "4/30 - Sheet 2",
                 "Game 1 · End 4 · Rock 16 (red, skip)", "the ghosts are on the wrong stones",
                 f"{BASE}/g/s_x/#e=4&s=16", "https://youtu.be/VID?t=3354",
                 "r_1", "0.4.16", "2026.09.25+m"):
        assert part in text, part


def test_describe_says_anonymous():
    assert "anonymous" in flags.describe(flag(user=None), BASE)


def test_describe_never_prints_a_control_character():
    """A second guard: the server strips them, but this reads whatever is stored."""
    f = flag(note="a\x1b]52;c;x\x07b\nsecond",
             place={**flag()["place"], "label": "red\x1b[31m"})
    text = flags.describe(f, BASE)
    assert "\x1b" not in text and "\x07" not in text
    assert "\\x1b" in text and "> second" in text


def _http_error(url, code, detail):
    import io
    import urllib.error
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(
        ('{"detail": "%s"}' % detail).encode()))


def test_resolve_keeps_going_past_a_bad_id(monkeypatch, capsys):
    calls = []

    def fake_call(method, url, token):
        calls.append(url)
        if "f_typo" in url:
            raise _http_error(url, 404, "no such flag")
        return {"id": url.split("/")[-2], "status": "resolved"}

    monkeypatch.setattr(flags, "_call", fake_call)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    assert flags.main(["resolve", "f_typo", "f_a", "f_b"]) == 1
    out = capsys.readouterr()
    assert [u.split("/")[-2] for u in calls] == ["f_typo", "f_a", "f_b"]
    assert "f_a resolved" in out.out and "f_b resolved" in out.out
    assert "f_typo: 404 no such flag" in out.err


def test_a_failed_list_says_why_without_a_traceback(monkeypatch, capsys):
    def fake_call(method, url, token):
        raise _http_error(url, 401, "bad admin token")

    monkeypatch.setattr(flags, "_call", fake_call)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    assert flags.main(["list"]) == 1
    assert "401 bad admin token" in capsys.readouterr().err


def test_list_asks_for_a_limit_and_says_when_it_may_be_cut(monkeypatch, capsys):
    seen = []

    def fake_call(method, url, token):
        seen.append(url)
        return {"flags": [flag(id=f"f_{i}") for i in range(3)]}

    monkeypatch.setattr(flags, "_call", fake_call)
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    assert flags.main(["list", "--limit", "3"]) == 0
    assert "limit=3" in seen[0]
    assert "3 shown; there may be more (raise --limit)" in capsys.readouterr().out


def test_safe_keeps_joiners_tabs_and_newer_characters():
    text = flags._safe("\U0001f468‍\U0001f469\tok \U0001fae9 \x1b[31m ‮")
    assert "‍" in text and "\t" in text and "\U0001fae9" in text
    assert "\x1b" not in text and "‮" not in text
    assert "\\x1b" in text and "\\u202e" in text


def test_a_place_without_an_end_names_the_game_only():
    f = flag(place={**flag()["place"], "end": None, "end_id": None, "rock": None,
                    "rock_id": None, "key": None})
    text = flags.describe(f, BASE)
    assert "End None" not in text and "Game 1" in text
