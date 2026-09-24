"""Click a stone, get its box: SAM behind the box editor.

The review of the first fifty frames settled what the colour detector can and
cannot do. It finds *where* something red or yellow is, which is a real skill --
a handle is genuinely saturated against grey ice. It is bad at two other
things, and they turned out to be the ones that mattered: deciding whether the
thing is a stone, and saying how far it extends. ``longview.candidates`` walks
down from the handle looking for granite and stops at whatever is darker than
ice, so its box was always a guess, and essentially none of them were usable.

A person settles identity in the time it takes to look. So let them: the click
says *this is a red stone*, and SAM says where its edges are. Measured on an
RTX A2000, encoding a frame costs 110 ms and each click after that about 95 ms,
so the loop is a person clicking at their own pace, not waiting on a GPU.

Geometry's job changes here, and improves. As a *filter* over colour blobs it
was unreliable, because rejecting a thing by its aspect only works if the
measured extent is real. As a *chooser* among SAM's own candidate masks it is
exactly right: SAM returns several readings of one click -- the stone, the
stone and its shadow, the stone and the hand on it -- and
``SideView.stone_width_at`` knows which of those is stone-sized at that row.
The model proposes, the calibration disposes.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Beyond these a mask is not a reading of a stone, it is a reading of the ice
# or of a person. Deliberately wider than the bounds the pool's own filter
# uses: there a bad box became a bad label silently, whereas here a person is
# looking at the result and can drag or delete it.
WIDTH_TOL = (0.45, 2.20)
ASPECT_TOL = (0.20, 1.20)

# What a click is expected to be, by the class the page armed. A broom head
# lies on the same ice as a stone, so the same perspective line bounds it,
# scaled to its size: about 30 px against a stone's ~44 at the tee (Phase 0,
# 2026-09-23). Its aspect is loose because a pad is held across the line or
# along it, and one pointing at the camera stands taller than it is wide: the
# first real click, a yellow pad end-on, boxed at 14 x 30 px (aspect 2.14).
SHAPES = {
    "stone": {"scale": 1.0, "width": WIDTH_TOL, "aspect": ASPECT_TOL},
    "broom": {"scale": 0.70, "width": (0.35, 2.0), "aspect": (0.10, 2.80)},
}


def _bbox(mask):
    import numpy as np

    ys, xs = np.nonzero(mask)
    if len(xs) < 8:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def score(box, geom, shape="stone") -> tuple[float, float, float]:
    """How like a ``shape`` -- a stone by default -- a mask's box is. Lower is better.

    Scored on the log of the width ratio so that half-size and double-size are
    equally wrong -- on a plain ratio, everything too small crowds into (0, 1)
    and everything too large has the whole line above it, which quietly makes
    the chooser prefer masks that are too small.
    """
    sh = SHAPES[shape]
    x0, y0, x1, y1 = box
    w, h = float(x1 - x0 + 1), float(y1 - y0 + 1)
    expect = sh["scale"] * geom["k"] * (y1 - geom["yh"])
    if expect <= 1.0:
        return math.inf, w, 0.0
    ratio, aspect = w / expect, (h / w if w else 99.0)
    if not (sh["width"][0] <= ratio <= sh["width"][1]):
        return math.inf, ratio, aspect
    if not (sh["aspect"][0] <= aspect <= sh["aspect"][1]):
        return math.inf, ratio, aspect
    return abs(math.log(ratio)), ratio, aspect


class Segmenter:
    """One SAM, with the last few frames' embeddings kept warm."""

    def __init__(self, weights: str, images: Path, cache: int = 4):
        from ultralytics import SAM

        self.model = SAM(weights)
        self.images = Path(images)
        self._current = None
        self._cache = cache

    @lru_cache(maxsize=1)
    def _warm(self):  # pragma: no cover - the model's own lazy build
        return None

    def _set_image(self, stem: str):
        import cv2

        if self._current == stem:
            return True
        path = self.images / f"{stem}.jpg"
        if not path.is_file():
            return False
        img = cv2.imread(str(path))
        if img is None:
            return False
        if self.model.predictor is None:
            # The predictor only exists after one call has built it.
            self.model(img, points=[[img.shape[1] // 2, img.shape[0] // 2]],
                       labels=[1], verbose=False)
        self.model.predictor.set_image(img)
        self._current = stem
        return True

    def box_at(self, stem: str, x: float, y: float, geom: dict,
               shape: str = "stone") -> dict:
        """The stone-like box around ``(x, y)``, in pixels, or a reason why not."""
        import numpy as np

        if not self._set_image(stem):
            return {"ok": False, "reason": f"no image for {stem}"}
        res = self.model.predictor(points=np.array([[float(x), float(y)]]),
                                   labels=np.array([1]),
                                   multimask_output=True)
        masks = res[0].masks
        if masks is None:
            return {"ok": False, "reason": "no mask"}

        best = None
        seen = []
        for m in masks.data.cpu().numpy():
            box = _bbox(m)
            if box is None:
                continue
            s, ratio, aspect = score(box, geom, shape)
            seen.append({"box": box, "ratio": round(ratio, 2),
                         "aspect": round(aspect, 2),
                         "score": None if s == math.inf else round(s, 3)})
            if best is None or s < best[0]:
                best = (s, box, ratio, aspect)
        if best is None:
            return {"ok": False, "reason": "no usable mask", "candidates": seen}
        s, box, ratio, aspect = best
        # An out-of-bounds best is still returned: the person clicked, so they
        # get something to drag rather than nothing, and `plausible` says how
        # much to trust it.
        return {"ok": True, "box": list(box), "ratio": round(ratio, 2),
                "aspect": round(aspect, 2), "plausible": s != math.inf,
                "candidates": seen}


def segment_request(seg, req: dict, geoms: dict, shapes) -> dict:
    """One /segment call: which frame, where, and what the click says it is.

    The page sends the armed class index; ``shapes`` names what each index is.
    An index the page should not have is treated as a stone, the editor's
    original meaning, rather than failing a person mid-session.
    """
    stem = req["stem"]
    cls = int(req.get("cls") or 0)
    shape = shapes[cls] if 0 <= cls < len(shapes) else "stone"
    return seg.box_at(stem, float(req["x"]), float(req["y"]),
                      geoms.get(stem) or req.get("geom") or {}, shape=shape)


def make_handler(directory: Path, seg: Segmenter, geoms: dict,
                 shapes=("stone", "stone")):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a):  # quiet; the editor is chatty by design
            pass

        def do_POST(self):
            if self.path not in ("/segment", "/save"):
                self.send_error(404)
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/save":
                    out = save_edits(directory, req)
                else:
                    out = segment_request(seg, req, geoms, shapes)
            except Exception as exc:  # noqa: BLE001 -- a bad click must not kill the server
                out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def save_edits(directory, payload: dict) -> dict:
    """Write one export beside the frames it came from.

    A browser download lands wherever that browser's machine puts it, which is
    not necessarily the machine holding the images -- the first real session
    was labelled from a laptop and the file never reached the box that had the
    dataset. Saving through the server puts it next to the pixels by
    construction.

    Each save is its own file, timestamped. Review happens over several
    sittings, and ``labels.merge_edits`` exists precisely so every sitting can
    be kept and a bad one dropped without losing the rest; overwriting one file
    would throw that away.
    """
    import re
    import time

    scope = str(payload.get("scope") or "unscoped")
    safe = re.sub(r"[^A-Za-z0-9]+", "-", scope).strip("-") or "unscoped"
    out_dir = Path(directory) / "edits"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=1) + "\n")
    n_frames = len(payload.get("boxes") or {})
    n_boxes = sum(len(v) for v in (payload.get("boxes") or {}).values())
    return {"ok": True, "path": str(path), "frames": n_frames, "boxes": n_boxes}


class StalePage(RuntimeError):
    """The page on disk predates the segmenter it is being served against."""


def check_page(directory) -> None:
    """Refuse to serve a page that cannot call ``/segment``.

    This exact mistake cost a review session: ``boxedit._PAGE`` was wired to
    the segmenter, the tests passed, the endpoint answered correctly by curl --
    and the ``index.html`` on disk had been rendered *before* the wiring, so
    every click fell back to the geometric box. It looked exactly like
    segmentation working badly, which is the worst way for it to fail. The
    end-to-end check missed it too, because it asserted the box was centred on
    the click, and the geometric fallback is also centred on the click.
    """
    page = Path(directory) / "index.html"
    if not page.is_file():
        raise StalePage(f"no index.html in {directory}; render one first")
    if "/segment" not in page.read_text():
        raise StalePage(
            f"{page} was rendered before the segmenter wiring: every click "
            f"would silently fall back to a geometry-sized box. Re-render it "
            f"with boxedit.render().")


def serve(directory, items, weights, port: int = 8777, host: str = "127.0.0.1",
          shapes=("stone", "stone")):
    directory = Path(directory)
    check_page(directory)
    geoms = {it["stem"]: it["geom"] for it in items}
    seg = Segmenter(weights, directory / "images")
    httpd = ThreadingHTTPServer((host, port),
                                make_handler(directory, seg, geoms, shapes))
    return httpd
