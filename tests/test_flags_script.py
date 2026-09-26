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
