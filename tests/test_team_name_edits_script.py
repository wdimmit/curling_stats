"""The terminal side of team-name history: what the owner reads back."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "team_name_edits_script",
    Path(__file__).resolve().parents[1] / "scripts" / "team_name_edits.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

BASE = "https://curling.dimmit.net"


def edit(**kw):
    base = {"id": "tn_abc", "source_id": "s_x", "at": "2026-10-15T19:03:00+00:00",
            "title": "4/30 - Sheet 2 - Spring League",
            "user": {"uid": "uid-alex", "email": "alex@example.org", "name": "Alex"},
            "before": {"red": "Rice"}, "after": {"red": "Cheats"},
            "now": {"red": "Cheats", "yellow": "Casey"}}
    base.update(kw)
    return base


def test_an_edit_says_who_what_and_where():
    lines = script.describe(edit(), BASE).splitlines()
    assert lines[0] == "2026-10-15T19:03:00+00:00  s_x  4/30 - Sheet 2 - Spring League"
    assert lines[1] == "  by Alex <alex@example.org> (uid-alex)"
    assert lines[2] == '  red: "Rice" -> "Cheats"'
    assert lines[3] == f"  {BASE}/g/s_x/"


def test_a_name_put_right_since_says_what_it_is_now():
    text = script.describe(edit(now={"red": "Rice", "yellow": "Casey"}), BASE)
    assert '  red: "Rice" -> "Cheats"   (now "Rice")' in text


def test_no_name_reads_as_none():
    text = script.describe(edit(before={"red": None, "yellow": "Casey"},
                                after={"red": "Rice", "yellow": None},
                                now={"red": "Rice", "yellow": None}), BASE)
    assert '  red: (none) -> "Rice"' in text and '  yellow: "Casey" -> (none)' in text


def test_a_game_since_deleted_still_lists():
    text = script.describe(edit(title=None, now=None), BASE)
    assert text.splitlines()[0] == "2026-10-15T19:03:00+00:00  s_x"
    assert "(now" not in text


def test_an_account_since_gone_still_lists():
    text = script.describe(edit(user={"uid": "uid-x", "email": None, "name": None}), BASE)
    assert "  by (account gone) (uid-x)" in text


def test_nothing_typed_in_can_drive_the_terminal():
    text = script.describe(edit(after={"red": "R\x1b]52;c;x\x07‮"},
                                now={"red": "R\x1b]52;c;x\x07‮"}), BASE)
    assert "\x1b" not in text and "\x07" not in text and "‮" not in text
