"""Grey out the sides of a hack wave's frames, where the parked stones are.

The hack wave's crop runs 1 m behind the hack to +2 m past the tee at full view
width, and at that depth the stones stored behind the far end sit in the same
rows and inside the sheet's width -- 353 of the wave's first 487 proposed boxes,
all 1.4 m or more off the centre line. The thrown rock is never more than 0.66 m
off it in that depth (24,401 samples, 156 rocks), and nothing was proposed
between 0.6 and 1.0 m.

So everything beyond ``--half-m`` of the painted centre line, row by row, is
filled with YOLO's own letterbox grey (114): a region the model already reads
as nothing. Masked, not cropped: a narrower image is scaled up to the 800 px
the model trains at, and it would learn the hack's stones at twice the size it
meets them in production's full-width crops.

    ./.venv/bin/python scripts/ds13/mask_wave.py ~/curling-work/ds13hack/wave1 \\
        datasets/ds13/manifest-hack1.json --half-m 1.0 --scope ds13:hack1

The unmasked frames move to ``images_full/``. Proposed boxes whose centre is
masked are dropped, the manifest records ``mask_half_m``, and a tree built from
the wave must drop any box whose centre lies in the mask (a reviewer may have
drawn one before the mask arrived).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

from curling_score.geometry import sideview

GREY = 114

HELP_MASKED = (
    '<p class="muted"><b>This wave is the hack to just past the tee</b>, which the model '
    "was never trained on. The grey sides are masked -- the stones parked behind the far end "
    "are there, and nothing in the grey is boxed or counted. Frames open with the model's boxes; "
    "correct them. <b>Box every stone in the clear part</b>: the thrown rock and any stone "
    "waiting by the hack. Drag a box to move it, a corner to resize, <b>Delete</b> to remove. "
    "<b>R</b>/<b>Y</b> arm red/yellow; click a stone and SAM finds its edges. A box on a stone "
    "the model missed was placed from the frames either side and may be a little off. Mark each "
    "frame reviewed with <b>space</b> -- only reviewed frames count. <b>N</b>/<b>P</b> move "
    "between frames; <b>Save to server</b> writes the session beside the images.</p>")


def view_of(m: dict) -> sideview.SideView:
    return sideview.SideView(
        rect=tuple(m["rect"]), tee_row=m["tee_row"], hog_row=m["hog_row"],
        centre_col=m["centre_col"], lat_px_per_m_at_tee=m["lat_px_per_m_at_tee"],
        centre_line=tuple(m["centre_line"]) if m.get("centre_line") else None)


def window(view, crop_top: int, height: int, width: int, half_m: float):
    """Per crop row, the columns [lo, hi) left unmasked."""
    lo, hi = [], []
    for r in range(height):
        vr = crop_top + r
        c, h = view.centre_col_at(vr), half_m * view.lateral_px_per_m(vr)
        lo.append(max(0, int(np.floor(c - h)))); hi.append(min(width, int(np.ceil(c + h))))
    return lo, hi


def mask(img, lo, hi):
    out = img.copy()
    for r, (a, b) in enumerate(zip(lo, hi)):
        out[r, :a] = GREY
        out[r, b:] = GREY
    return out


def inside(box, lo, hi, width: int, height: int) -> bool:
    """A normalised [cls, cx, cy, w, h] box whose centre is unmasked."""
    _c, cx, cy, _w, _h = box
    r = min(height - 1, max(0, int(cy * height)))
    return lo[r] <= cx * width < hi[r]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wave")
    ap.add_argument("manifest")
    ap.add_argument("--half-m", type=float, default=1.0)
    ap.add_argument("--scope", required=True)
    args = ap.parse_args()

    from curling_score.train import boxedit

    wave = Path(args.wave).expanduser()
    full = wave / "images_full"
    full.mkdir(exist_ok=True)
    rows = json.loads(Path(args.manifest).read_text())
    man = {m["stem"]: m for m in rows}
    items = json.loads((wave / "items.json").read_text())
    kept = dropped = 0
    for it in items:
        m = man[it["stem"]]
        src = full / Path(it["image"]).name
        if not src.exists():
            shutil.copy2(wave / it["image"], src)          # keep the original once
        img = cv2.imread(str(src))
        lo, hi = window(view_of(m), m["crop_top"], img.shape[0], img.shape[1], args.half_m)
        cv2.imwrite(str(wave / it["image"]), mask(img, lo, hi), [cv2.IMWRITE_JPEG_QUALITY, 92])
        before = len(it["boxes"])
        it["boxes"] = [b for b in it["boxes"] if inside(b, lo, hi, it["width"], it["height"])]
        kept += len(it["boxes"]); dropped += before - len(it["boxes"])
        m["mask_half_m"] = args.half_m
    (wave / "items.json").write_text(json.dumps(items))
    Path(args.manifest).write_text(json.dumps(rows, indent=1) + "\n")
    # The page gets the images under a new address: a browser that loaded the
    # unmasked frames keeps showing them from its cache, even across a reload.
    # items.json keeps the plain path, which is what a tree builder reads.
    import time
    tag = int(time.time())
    shown = [{**it, "image": f"{it['image']}?masked={tag}"} for it in items]
    page = boxedit.render(shown, wave, scope=args.scope, proposals=True,
                          title="Stones between the hack and the tee")
    import re
    html, k = re.subn(r'<p class="muted">.*?</p>', HELP_MASKED, page.read_text(), count=1, flags=re.S)
    assert k == 1
    page.write_text(html)
    print(f"{len(items)} frames masked beyond {args.half_m} m; boxes kept {kept}, dropped {dropped}; page {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
