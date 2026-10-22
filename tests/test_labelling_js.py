"""The members' labelling page's arithmetic (frontend/core/labelling.mjs), in node.

The page only draws and listens; which box a click lands on, what a corner may
do at the image's edge, and the answer the server will accept are all here, so
they are tested without a browser."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "frontend/core/labelling.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")

# Three starts, the middle one flagged, on a 720 x 360 image.
ITEM = {
    "id": "b1", "kind": "box", "image_url": "x", "w": 720, "h": 360,
    "starts": [
        {"x0": 10, "y0": 10, "x1": 50, "y1": 50, "color": "red", "conf": 0.9, "flagged": False},
        {"x0": 100, "y0": 100, "x1": 140, "y1": 140, "color": "yellow", "conf": 0.3, "flagged": True},
        {"x0": 300, "y0": 200, "x1": 340, "y1": 240, "color": "red", "conf": 0.8, "flagged": False},
    ],
}

# Ten frames in a sprite four across: three rows, the last one short.
RELEASE = {"id": "r1", "kind": "release", "image_url": "x", "cols": 4, "fw": 160, "fh": 120,
           "n": 10, "fps": 10}


def js(expr: str):
    script = (f"const L = await import({MODULE.as_uri()!r});\n"
              f"const item = {json.dumps(ITEM)};\n"
              f"const rel = {json.dumps(RELEASE)};\n"
              f"const s0 = L.startBoxes(item);\n"
              f"console.log(JSON.stringify({expr}));")
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_fresh_item_cannot_finish_until_the_flagged_box_is_acted_on():
    s = js("s0")
    assert s["starts"] == ["untouched"] * 3 and s["flagged"] == 1
    assert [b["start"] for b in s["boxes"]] == [0, 1, 2]
    assert js("L.canFinish(s0)") is False
    assert js("L.canFinish(L.keepFlagged(s0))") is True
    assert js('L.canFinish(L.setColour(s0, 1, "red"))') is True
    assert js("L.canFinish(L.removeBox(s0, 1))") is True
    # Acting on some other box does not do it.
    assert js("L.canFinish(L.keepFlagged(L.removeBox(s0, 0)))") is True
    assert js("L.canFinish(L.removeBox(s0, 0))") is False


def test_moving_a_starting_box_marks_it_changed():
    moved = js('L.setBox(s0, 0, L.moveCorner(s0.boxes[0], "se", 60, 70, 720, 360))')
    assert moved["starts"] == ["changed", "untouched", "untouched"]
    assert moved["boxes"][0]["x1"] == 60 and moved["boxes"][0]["y1"] == 70
    shifted = js("L.setBox(s0, 2, L.moveBox(s0.boxes[2], 5, -4, 720, 360))")
    assert shifted["starts"][2] == "changed"
    assert (shifted["boxes"][2]["x0"], shifted["boxes"][2]["y0"]) == (305, 196)
    # Putting it back where it was changes nothing.
    same = js("L.setBox(s0, 0, {...s0.boxes[0]})")
    assert same["starts"] == ["untouched"] * 3
    # A colour that differs counts; the same colour does not.
    assert js('L.setColour(s0, 0, "yellow")')["starts"][0] == "changed"
    assert js('L.setColour(s0, 0, "red")')["starts"][0] == "untouched"
    # And a state is never mutated on the way.
    assert js('(L.setColour(s0, 0, "yellow"), s0.starts[0])') == "untouched"


def test_deleting_marks_deleted_and_drops_it_from_the_answer():
    s = js("L.removeBox(s0, 2)")
    assert s["starts"] == ["untouched", "untouched", "deleted"]
    assert [b["start"] for b in s["boxes"]] == [0, 1]
    ans = js("L.boxAnswer(L.removeBox(s0, 2))")
    assert [b["start"] for b in ans["boxes"]] == [0, 1]
    assert ans["starts"][2] == "deleted"
    # Removing a box the member drew leaves every start as it was.
    drawn = js('L.removeBox(L.addBox(s0, {x0: 400, y0: 20, x1: 440, y1: 60, color: "red"}), 3)')
    assert drawn["starts"] == ["untouched"] * 3 and len(drawn["boxes"]) == 3


def test_keep_only_touches_the_flagged_box():
    assert js("L.keepFlagged(s0)")["starts"] == ["untouched", "kept", "untouched"]
    # A flagged box already changed stays changed.
    changed = js('L.keepFlagged(L.setColour(s0, 1, "red"))')
    assert changed["starts"][1] == "changed"
    # Added boxes have no start.
    added = js('L.addBox(s0, {x0: 1, y0: 2, x1: 30, y1: 40, color: "yellow", start: 2})')
    assert added["boxes"][3]["start"] is None and added["starts"] == ["untouched"] * 3


def test_corners_clamp_to_the_image_and_keep_three_pixels():
    box = ITEM["starts"][1]
    b = js(f'L.moveCorner({json.dumps(box)}, "nw", -50, -50, 720, 360)')
    assert (b["x0"], b["y0"], b["x1"], b["y1"]) == (0, 0, 140, 140)
    b = js(f'L.moveCorner({json.dumps(box)}, "se", 900, 900, 720, 360)')
    assert (b["x1"], b["y1"]) == (720, 360)
    # Dragged past the opposite corner, it stops three pixels short of it.
    b = js(f'L.moveCorner({json.dumps(box)}, "se", 90, 90, 720, 360)')
    assert (b["x0"], b["y0"], b["x1"], b["y1"]) == (100, 100, 103, 103)
    b = js(f'L.moveCorner({json.dumps(box)}, "nw", 200, 200, 720, 360)')
    assert (b["x0"], b["y0"], b["x1"], b["y1"]) == (137, 137, 140, 140)
    b = js(f'L.moveCorner({json.dumps(box)}, "ne", 130, 95, 720, 360)')
    assert (b["x0"], b["y0"], b["x1"], b["y1"]) == (100, 95, 130, 140)
    # A whole box moved off the edge keeps its size.
    b = js(f'L.moveBox({json.dumps(box)}, 900, -900, 720, 360)')
    assert (b["x0"], b["y0"], b["x1"], b["y1"]) == (680, 0, 720, 40)


def test_the_topmost_box_under_the_pointer_wins():
    assert js('L.boxAt(L.addBox(s0, {x0: 105, y0: 105, x1: 200, y1: 200, color: "red"}), 120, 120, 0)') == 3
    assert js("L.boxAt(s0, 120, 120, 0)") == 1
    assert js("L.boxAt(s0, 52, 30, 0)") == -1
    assert js("L.boxAt(s0, 52, 30, 3)") == 0
    assert js("L.cornerAt(s0, 101, 99, 4)") == {"i": 1, "corner": "nw"}
    assert js("L.cornerAt(s0, 120, 120, 4)") is None


def test_a_drawn_box_is_ordered_clamped_and_at_least_three_pixels():
    b = js('L.drawnBox(200, 150, 120, 90, 720, 360, "yellow")')
    assert (b["x0"], b["y0"], b["x1"], b["y1"], b["color"]) == (120, 90, 200, 150, "yellow")
    assert js('L.drawnBox(10, 10, 12, 80, 720, 360, "red")') is None
    b = js('L.drawnBox(700, 340, 800, 400, 720, 360, "red")')
    assert (b["x1"], b["y1"]) == (720, 360)


def test_the_picture_is_drawn_at_most_twice_as_big():
    assert js("L.drawScale(720, 360, 1400, 700)") == 1.9444444444444444
    assert js("L.drawScale(294, 527, 1400, 700)") == pytest.approx(700 / 527)
    assert js("L.drawScale(100, 50, 1400, 700)") == 2
    assert js("L.MIN_WIDTH_PX") == 1000


def test_box_answer_matches_the_server_shape():
    ans = js('L.boxAnswer(L.addBox(L.keepFlagged(s0), {x0: 400.04, y0: 20, x1: 440, y1: 60, color: "yellow"}))')
    assert ans["cant_tell"] is False
    assert len(ans["starts"]) == len(ITEM["starts"])
    assert ans["starts"] == ["untouched", "kept", "untouched"]
    assert [b["start"] for b in ans["boxes"]] == [0, 1, 2, None]
    assert set(ans["boxes"][3]) == {"x0", "y0", "x1", "y1", "color", "start"}
    assert ans["boxes"][3]["x0"] == 400  # a tenth of a pixel is plenty
    assert js("L.CANT_TELL") == {"cant_tell": True}


def test_sprite_offsets_walk_rows():
    # Frame i is at column i % cols, row i // cols; the sprite slides the other way.
    assert js("L.spriteOffset(rel, 0)") == {"x": 0, "y": 0}
    assert js("L.spriteOffset(rel, 3)") == {"x": 3 * 160, "y": 0}
    assert js("L.spriteOffset(rel, 4)") == {"x": 0, "y": 120}
    assert js("L.spriteOffset(rel, 9)") == {"x": 160, "y": 2 * 120}


def test_step_clamps():
    assert js("L.step(0, -1, 10)") == 0
    assert js("L.step(3, 1, 10)") == 4
    assert js("L.step(3, 5, 10)") == 8
    assert js("L.step(8, 5, 10)") == 9
    assert js("L.step(2, -5, 10)") == 0
    assert js("L.step(0, 1, 1)") == 0


def test_release_answer_drops_frame_unless_off():
    assert js('L.releaseAnswer("off", 7)') == {"mark": "off", "frame": 7}
    assert js('L.releaseAnswer("off", 0)') == {"mark": "off", "frame": 0}
    assert js('L.releaseAnswer("held", 7)') == {"mark": "held", "frame": None}
    assert js('L.releaseAnswer("cant_tell", 7)') == {"mark": "cant_tell", "frame": None}


def test_a_409_names_who_has_the_item():
    assert js('L.conflictNotice("held")') == "Someone else has this one now."
    assert js('L.conflictNotice("full")') == "Someone else finished this one."
    assert js('L.conflictNotice("closed")') == "Someone else finished this one."
    assert js("L.conflictNotice(undefined)") == "Someone else finished this one."
