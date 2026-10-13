"""A wave already trained on, back on the page under a newer labelling rule.

The hack wave (hack1) was boxed before the kink wave's rule -- the stone alone,
never the hand on it, the slider shoe or a broom beside it -- and some of its
boxes take those in. ds13e learnt the rule from the kink wave and so scores
worse against hack1's own labels (2026-10-13). This serves the frames of a built
tree with the labels it trained on as the proposals, so a reviewer corrects
only what breaks the rule; the frames, their names and their split stay.

    PYTHONPATH=src ~/src/curling_score/.venv/bin/python scripts/ds13/relabel_wave.py \\
        ~/curling-work/ds13d/ds13_trees/hack1 ~/curling-work/ds13hack/wave1 \\
        datasets/ds13/manifest-hack1.json --out ~/curling-work/ds13e/hack1r --scope ds13:hack1r
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mask_wave import view_of  # noqa: E402

HELP = (
    '<p class="muted"><b>This is the hack wave again, under the kink wave\'s rule.</b> These frames '
    "were boxed before it, and some boxes take in the thrower's hand, the slider shoe or a broom "
    "beside the stone; the model now learns the stone alone and the two sets pull against each "
    "other. Frames open with the boxes the model trained on. <b>Box the stone alone</b>: its own "
    "edges, not the hand, the shoe, the pad or the broom, even where they touch or overlap it -- "
    "<b>the handle is part of the stone</b> (the box's top is the handle's top), a hand on it is "
    "not; where the stone is hidden, box where it is. <b>The bottom edge matters most</b>: it is "
    "where the stone sits on the ice, and the chart reads the stone's distance from it. Most "
    "frames need nothing: mark them reviewed and move on. The grey sides are masked; nothing in "
    "the grey is boxed or counted. Box every stone in the clear part. Drag a box to move it, a "
    "corner to resize, <b>Delete</b> to remove. <b>R</b>/<b>Y</b> arm red/yellow; click a stone "
    "and SAM finds its edges -- check SAM did not take the hand with it. Mark each frame "
    "reviewed with <b>space</b>; only reviewed frames count. <b>N</b>/<b>P</b> move between "
    "frames; <b>Save to server</b> writes the session beside the images.</p>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", help="the built tree whose labels are the proposals")
    ap.add_argument("wave", help="the wave directory holding the (masked) images")
    ap.add_argument("manifest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scope", required=True)
    args = ap.parse_args()

    from curling_score.detect import longview
    from curling_score.train import boxedit

    tree, wave, out = (Path(p).expanduser() for p in (args.tree, args.wave, args.out))
    man = {m["stem"]: m for m in json.loads(Path(args.manifest).read_text())}
    (out / "images").mkdir(parents=True, exist_ok=True)
    items, splits = [], {}
    for split in ("train", "val", "heldout"):
        for lab in sorted((tree / "labels" / split).glob("*.txt")):
            m = man[lab.stem]
            shutil.copy2(wave / "images" / f"{lab.stem}.jpg", out / "images" / f"{lab.stem}.jpg")
            rows = [[int(float(v[0]))] + [round(float(x), 6) for x in v[1:]]
                    for v in (ln.split() for ln in lab.read_text().splitlines() if ln.strip())]
            items.append({"stem": lab.stem, "image": f"images/{lab.stem}.jpg", "width": m["width"],
                          "height": m["height"], "boxes": rows,
                          "geom": boxedit.frame_geometry(view_of(m), longview.STONE_WIDTH_AT_HOG_PX,
                                                         row_offset=m["crop_top"])})
            splits[split] = splits.get(split, 0) + 1
    (out / "items.json").write_text(json.dumps(items))
    page = boxedit.render(items, out, scope=args.scope, proposals=True,
                          title="Stones between the hack and the tee, the stone alone")
    html, k = re.subn(r'<p class="muted">.*?</p>', HELP, page.read_text(), count=1, flags=re.S)
    assert k == 1
    page.write_text(html)
    print(f"{len(items)} frames {splits}; page {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
