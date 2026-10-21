"""The terminal side of moving chart-level doubles swaps onto their games."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "switched_ends_script", Path(__file__).resolve().parents[1] / "scripts" / "switched_ends.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

BASE = "https://curling.dimmit.net"


def body(**kw):
    out = {"ok": True, "applied": False, "moved": [], "normalised": [], "conflicts": [],
           "other_runs": []}
    out.update(kw)
    return out


def test_each_item_is_one_line_with_its_game_and_chart():
    got = script.lines(body(
        moved=[{"source_id": "s_1", "chart": "sh1", "color": "red", "ends": [2, 3]}],
        normalised=[{"source_id": "s_2", "chart": "sh2", "color": "yellow", "ends": [],
                     "plays": ["uid-a", "uid-b"]}]), BASE)
    assert got == [
        f"moved: red ends 2, 3 — {BASE}/g/s_1/ (chart {BASE}/s/sh1/)",
        f"normalised: yellow ends none — {BASE}/g/s_2/ (chart {BASE}/s/sh2/);"
        " plays saved from it: uid-a, uid-b",
        "dry run: nothing changed; run again with --apply",
    ]


def test_nothing_to_move_says_so():
    assert script.lines(body(applied=True), BASE) == ["nothing to move", "applied"]


def test_conflicts_and_other_runs_are_listed_after():
    got = script.lines(body(
        conflicts=[{"source_id": "s_3", "chart": "sh3", "color": "red", "ends": [4]}],
        other_runs=[{"source_id": "s_4", "chart": "sh4", "color": "red", "ends": [5]}]), BASE)
    assert [g.split(":")[0] for g in got[:2]] == ["conflict", "other run"]
