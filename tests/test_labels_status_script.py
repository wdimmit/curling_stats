"""scripts/labels_status.py: the labelling report, from canned listings."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "labels_status_script", Path(__file__).resolve().parents[1] / "scripts" / "labels_status.py")
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)

BOX = {"x0": 10, "y0": 10, "x1": 30, "y1": 20, "color": "red"}


def box_answer(uid, at, *, starts=("kept",), boxes=(dict(BOX, start=0),), seconds=10.0):
    return {"uid": uid, "at": at, "seconds": seconds,
            "answer": {"cant_tell": False, "starts": list(starts), "boxes": list(boxes)}}


def item(n, state, answers, reference=None):
    return {"id": f"b_{n}", "state": state, "n_labels": len(answers),
            "answers": answers, "reference": reference}


def test_a_box_report():
    moved = dict(BOX, x0=11, start=0)
    items = [
        item(1, "done", [box_answer("u1", "1", starts=["changed"], boxes=[moved]),
                         box_answer("u2", "2", starts=["changed"], boxes=[moved])]),
        item(2, "done", [box_answer("u1", "1"), box_answer("u2", "2")]),     # both stamped
        item(3, "open", [box_answer("u2", "3", seconds=4.0)]),
        item(4, "open", []),
        item(5, "open", [box_answer("u1", "4")], reference={"boxes": [BOX]}),
    ]
    lines = status.summarise("box", items, {"u1": "one@example.org"})
    assert lines[0] == "Rock boxes: 5 items"
    assert lines[1] == "  3 open, 2 done"
    assert lines[2] == "  labels per item: 1 with 0, 2 with 1, 2 with 2"
    assert lines[3] == "  finished pairs: 2 -- 1 agree, 1 rubber_stamp"
    assert lines[4] == ("  one@example.org: 3 labels, median 10 s, 0 can't tell, "
                        "2 changed nothing, checks 1/1")
    assert lines[5] == "  u2: 3 labels, median 10 s, 0 can't tell, 2 changed nothing, no checks yet"


def test_a_release_report():
    off = lambda uid, f, at: {"uid": uid, "at": at, "seconds": 6.0,  # noqa: E731
                              "answer": {"mark": "off", "frame": f}}
    items = [item(1, "done", [off("u1", 40, "1"), off("u2", 44, "2")]),
             item(2, "open", [{"uid": "u1", "at": "3", "seconds": 2.0,
                               "answer": {"mark": "cant_tell", "frame": None}}])]
    lines = status.summarise("release", items, {})
    assert lines[0] == "Release marks: 2 items"
    assert lines[3] == "  finished pairs: 1 -- 1 apart"
    assert lines[4] == "  u1: 2 labels, median 6 s, 1 can't tell, no checks yet"


def test_no_token_is_exit_2(monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert status.main([]) == 2
    assert "ADMIN_TOKEN" in capsys.readouterr().err
