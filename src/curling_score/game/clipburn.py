"""A game rock's clip with its marks burned into the picture.

The practice page draws a clip's lines over the playing video
(frontend/site/clip.jsx); a game clip carries them in its own pixels, so a
clip someone saves keeps them. On top of practice's three marks -- the thrown
line, the aim line from the hack to the broom, and the broom
(practice/overlay.py) -- it draws the delivery: the rock from the hack to
1.5 m past the hog line as the hog-line camera followed it (the timeline's
``line.delivery``), a dot at a time as the rock reaches it, in one colour
before the hand let go, another after, and a third when there is no release
read (``line.release``): the Delivery chart's before, after and unknown colours.

The camera facing the thrower sees the throwing end small, near the middle of
its picture, so its piece starts zoomed on the hack and the hog line, and pulls
back to the whole camera once the rock is past them: zoomed for its whole
piece, the rock runs out of the bottom of the picture seconds before the cut
to the other camera (s_1JVE e3 r12, 2026-10-12).

The rock's arrival is shown from the overhead camera above the house it was
thrown to (game/clips.plan), turned so the rock comes up the picture as it
would seen from behind the thrower, and kept at its own shape: full height,
black either side (`Panel`). Its marks are placed by its own calibration.

ffmpeg cuts the pieces as `practice/clips.cut` does, but hands them over raw;
this frames and marks each picture and pipes it to a second ffmpeg to encode.
"""

import logging
import subprocess
import tempfile
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from curling_score.game.hogtime import CAMERA_FOR
from curling_score.game.linetime import DELIVERY_END_M, TEE_Y
from curling_score.geometry import constants as G
from curling_score.practice import clips as practice_clips
from curling_score.practice import overlay as practice_overlay
from curling_score.practice.enrich import OTHER_HOUSE

log = logging.getLogger(__name__)

# The zoomed picture shows the camera's panel this many times larger than the
# whole-camera picture does: half its height. ("1.5x" in the samples of
# 2026-10-12, which counted against the panel's own pixels, not the clip's.)
ZOOM = 2.0
# Where the hack sits down the zoomed picture, so the thrower above it is in it.
HACK_AT = 0.44
# ... and the lowest the end of the delivery may sit in it.
DELIVERY_END_BY = 0.85
# Once the rock is past the delivery, back to the whole camera over this long.
PULL_BACK_S = 1.0
# Without a delivery or hog time, the rock is taken past it this long after release.
DELIVERY_S = 3.5
# Where a rock sits in front of the hack, when its own start was not read.
HACK_START_Y = TEE_Y + G.TEE_TO_HACKLINE_M - 0.25

# The pictures are framed from a cut this much bigger than the clip, so the
# zoomed piece is drawn from the camera's own pixels (a panel is ~810x1080).
WORK_SCALE = 4 / 3

# The page's look (frontend/site/style.css svg.clip-lines), in BGR.
THROWN = (208, 47, 255)          # #ff2fd0
AIM = (255, 216, 47)             # #2fd8ff
UNDER = (0, 0, 0)
STROKE, UNDER_STROKE = 3, 6
DASH, GAP = 9, 7
BROOM_R = 13
# The Delivery chart's dots: before the release, after it, and no release read.
DOT_BEFORE = (0x6b, 0x1e, 0x2d)
DOT_AFTER = (0x00, 0x7a, 0xa0)
DOT_UNKNOWN = (0x8c, 0x7a, 0x1f)
DOT_R = 3                        # in the zoomed picture; smaller as it pulls back
DOT_R_MIN = 2
DOT_GAP = 6                      # dots closer than this in the zoomed picture are one
DOT_GAP_WHOLE = 5                # ... or in a picture of the whole camera


@dataclass(frozen=True)
class Panel:
    """An overhead panel as a run calibrated it (geometry/calibrate.PanelCalib,
    as the timeline records it): where it is in the stream, where its house's
    tee is in it, its scale, and whether the thrower's end is at its top."""
    rect: tuple
    center_px: tuple
    px_per_m: float
    flipped: bool

    @classmethod
    def from_calibration(cls, cal: dict | None):
        """The panel the timeline's ``calibration[house]`` records, or None."""
        try:
            return cls(rect=tuple(int(v) for v in cal["rect"]),
                         center_px=(float(cal["center_px"][0]), float(cal["center_px"][1])),
                         px_per_m=float(cal["px_per_m"]), flipped=bool(cal["flipped"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    def to_frac(self, x: float, y: float) -> list:
        """A point on the ice of this panel's house, in the house's frame (as
        `calibrate.PanelCalib.to_pixels` takes it), as ``[u, v]`` fractions
        of the panel turned so the thrower's end is at the bottom."""
        if self.flipped:
            x, y = -x, -y
        u = (self.center_px[0] + x * self.px_per_m) / self.rect[2]
        v = (self.center_px[1] + y * self.px_per_m) / self.rect[3]
        return [1 - u, 1 - v] if self.flipped else [u, v]

    def box(self, size) -> tuple:
        """The part of the panel the clip's picture shows: all of its height,
        at its own shape, with black either side."""
        width, height = size
        w = width * self.rect[3] / (height * self.rect[2])
        return ((1 - w) / 2, 0.0, w, 1.0)


# A line on the ice is sampled this finely when drawn over a panel.
PANEL_SAMPLES = 40


def panel_marks(throw, panel: Panel) -> dict:
    """The thrown line, the aim line and the broom over the overhead panel."""
    def line(ends):
        if ends is None:
            return None
        (ax, ay), (bx, by) = ends
        n = PANEL_SAMPLES
        return [panel.to_frac(ax + (bx - ax) * i / (n - 1), ay + (by - ay) * i / (n - 1))
                for i in range(n)]
    thrown, aim = practice_overlay.lines_on_ice(throw)
    broom = throw.get("broom")
    return {"thrown": line(thrown), "aim": line(aim),
            "broom": None if broom is None else panel.to_frac(broom["x"], broom["y"]),
            "dots": []}


@dataclass(frozen=True)
class Framing:
    """The zoomed picture of the first piece: ``box`` is ``(u, v, w, h)``,
    fractions of its camera's panel, held until ``t_out`` on the stream's
    clock, then pulled back to the whole panel over PULL_BACK_S."""
    view: str
    box: tuple
    t_out: float


def dot_colour(t: float, release_t) -> tuple:
    """A dot's colour, ``t`` and ``release_t`` seconds from the throw's
    release on the delivery's clock: before the release, after it, or unknown
    when ``release_t`` is None."""
    if release_t is None:
        return DOT_UNKNOWN
    return DOT_BEFORE if t < release_t else DOT_AFTER


def _delivery(throw) -> list:
    d = (throw.get("line") or {}).get("delivery")
    return [p for p in d if isinstance(p, (list, tuple)) and len(p) >= 3] if d else []


def delivery_done(throw) -> float | None:
    """When, on the stream's clock, the rock is past the end of its delivery."""
    rel = throw.get("t_release_s")
    if rel is None:
        return None
    d = _delivery(throw)
    if d:
        return rel + float(d[-1][0])
    t_hog, v = throw.get("t_hog_s"), throw.get("v_hog_m_s")
    if t_hog is not None and v:
        return t_hog + (DELIVERY_END_M - G.TEE_TO_HOGLINE_M) / v
    return rel + DELIVERY_S


def framing(throw, plan, sideviews) -> Framing | None:
    """The zoom on the throwing end, when the clip opens on the camera facing
    the thrower and that camera can place things across the sheet; else None
    and the clip is the whole camera throughout."""
    first = plan.pieces[0]
    view = (sideviews or {}).get(first.view)
    if first.view != CAMERA_FOR[OTHER_HOUSE[throw["house"]]] or not getattr(view, "has_lateral", False):
        return None
    line = throw.get("line") or {}
    start = line.get("start") or {}
    sx, sy = start.get("x", 0.0), start.get("y", HACK_START_Y)
    hx = line.get("at_hog_x")
    hack = practice_overlay.to_view(view, True, sx, sy)
    past = practice_overlay.to_view(view, True, sx if hx is None else hx,
                                    TEE_Y - DELIVERY_END_M)
    t_out = delivery_done(throw)
    if hack is None or past is None or t_out is None:
        return None
    w = h = 1.0 / ZOOM
    u = (hack[0] + past[0]) / 2 - w / 2
    v = hack[1] - HACK_AT * h
    if past[1] > v + DELIVERY_END_BY * h:
        v = past[1] - DELIVERY_END_BY * h
    u = min(max(u, 0.0), 1.0 - w)
    v = min(max(v, 0.0), 1.0 - h)
    return Framing(first.view, (round(u, 4), round(v, 4), w, h), t_out)


def marks(throw, plan, sideviews) -> list:
    """For each piece of ``plan``, its marks: ``thrown``, ``aim`` and
    ``broom`` as `practice_overlay.overlay` places them, and ``dots``, the
    delivery as ``(t, u, v, bgr)`` with ``t`` on the stream's clock, the colour
    split at the release. A piece whose camera cannot place things across the
    sheet has none."""
    lines = practice_overlay.overlay(throw, plan, sideviews) or [
        {"t0": 0.0, "t1": 0.0} for _ in plan.pieces]
    d, rel = _delivery(throw), throw.get("t_release_s")
    release = (throw.get("line") or {}).get("release")
    release_t = None if release is None else float(release[0])
    facing_view = CAMERA_FOR[OTHER_HOUSE[throw["house"]]]
    out = []
    for p, item in zip(plan.pieces, lines):
        view = (sideviews or {}).get(p.view)
        dots = []
        if d and rel is not None and getattr(view, "has_lateral", False):
            facing = p.view == facing_view
            for t, y, x in (q[:3] for q in d):
                uv = practice_overlay.to_view(view, facing, x, y)
                if uv is not None:
                    dots.append((rel + float(t), uv[0], uv[1],
                                 dot_colour(float(t), release_t)))
        out.append({"thrown": item.get("thrown"), "aim": item.get("aim"),
                    "broom": item.get("broom"), "dots": dots})
    return out


def thin(dots, sx: float, sy: float, gap: float = DOT_GAP) -> list:
    """``dots`` less those within ``gap`` px of the last one kept, at ``sx``
    by ``sy`` px per unit of the panel; the last is always kept."""
    out = []
    for i, dot in enumerate(dots):
        if (not out or i == len(dots) - 1
                or np.hypot((dot[1] - out[-1][1]) * sx, (dot[2] - out[-1][2]) * sy) >= gap):
            out.append(dot)
    return out


WHOLE = (0.0, 0.0, 1.0, 1.0)


def _smooth(f: float) -> float:
    f = min(1.0, max(0.0, f))
    return f * f * (3 - 2 * f)


def box_at(fr: Framing | None, view: str, t: float) -> tuple:
    """The part of ``view``'s panel in the picture at stream time ``t``."""
    s = 1.0 if fr is None or view != fr.view else _smooth((t - fr.t_out) / PULL_BACK_S)
    if s >= 1.0:
        return WHOLE
    return tuple(a + (b - a) * s for a, b in zip(fr.box, WHOLE))


def _to_px(box, size):
    """Panel fractions to the picture's pixels, for the part ``box`` shown."""
    u0, v0, w, h = box
    width, height = size
    return lambda u, v: ((u - u0) * width / w, (v - v0) * height / h)


def _poly(line, px):
    pts = np.array([px(u, v) for u, v in line], np.float64)
    pts = np.clip(pts, -1e6, 1e6)
    return np.round(pts * 16).astype(np.int32)       # 4 bits of subpixel


def _dashes(pts16, size):
    """The polyline ``pts16`` (16ths of a pixel) as DASH-on, GAP-off pieces,
    counted from its start, but only where it crosses the picture: a line can
    run far past a zoomed one."""
    pts = pts16.astype(np.float64) / 16
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    rect = (-DASH, -DASH, size[0] + 2 * DASH, size[1] + 2 * DASH)
    seen = []
    for j in range(len(pts) - 1):
        a = tuple(int(round(c)) for c in pts[j])
        ok, p, q = cv2.clipLine(rect, a, tuple(int(round(c)) for c in pts[j + 1]))
        if ok:
            seen += [cum[j] + np.hypot(p[0] - a[0], p[1] - a[1]),
                     cum[j] + np.hypot(q[0] - a[0], q[1] - a[1])]
    if not seen:
        return []
    out, s = [], np.floor(min(seen) / (DASH + GAP)) * (DASH + GAP)
    while s < min(max(seen), cum[-1]):
        e = min(s + DASH, cum[-1])
        a = [np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])]
        b = [np.interp(e, cum, pts[:, 0]), np.interp(e, cum, pts[:, 1])]
        out.append(np.round(np.array([a, b]) * 16).astype(np.int32))
        s += DASH + GAP
    return out


def draw(img, piece_marks, box, t: float) -> None:
    """Burn one piece's marks into ``img``, the picture of ``box`` of its
    panel at stream time ``t``: the dots the rock has reached by then."""
    px = _to_px(box, (img.shape[1], img.shape[0]))
    aa, sh = cv2.LINE_AA, 4
    aim, thrown, broom = piece_marks.get("aim"), piece_marks.get("thrown"), piece_marks.get("broom")
    for line in (aim, thrown):
        if line and len(line) >= 2:
            cv2.polylines(img, [_poly(line, px)], False, UNDER, UNDER_STROKE, aa, sh)
    if aim and len(aim) >= 2:
        for dash in _dashes(_poly(aim, px), (img.shape[1], img.shape[0])):
            cv2.polylines(img, [dash], False, AIM, STROKE, aa, sh)
    if thrown and len(thrown) >= 2:
        cv2.polylines(img, [_poly(thrown, px)], False, THROWN, STROKE, aa, sh)
    if broom:
        c = tuple(int(round(q * 16)) for q in px(*broom))
        if all(abs(q) < 1e5 for q in c):
            cv2.circle(img, c, BROOM_R * 16, UNDER, UNDER_STROKE, aa, sh)
            cv2.circle(img, c, BROOM_R * 16, THROWN, STROKE, aa, sh)
    r = max(DOT_R_MIN, DOT_R / (box[3] * ZOOM))
    for td, u, v, colour in piece_marks.get("dots") or ():
        if td > t:
            break
        x, y = px(u, v)
        if -r <= x < img.shape[1] + r and -r <= y < img.shape[0] + r:
            c = (int(round(x * 16)), int(round(y * 16)))
            cv2.circle(img, c, int(round((r + 1) * 16)), (255, 255, 255), -1, aa, sh)
            cv2.circle(img, c, int(round(r * 16)), colour, -1, aa, sh)


def _frame(img, box, size):
    """``img``, the whole panel, cut to ``box`` and scaled to ``size``."""
    width, height = size
    if box == WHOLE:
        return cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    u0, v0, w, h = box
    sx = width / (w * img.shape[1])
    sy = height / (h * img.shape[0])
    m = np.array([[sx, 0, -u0 * img.shape[1] * sx], [0, sy, -v0 * img.shape[0] * sy]])
    # A box past the panel's sides (a `Panel.box`) is black there.
    return cv2.warpAffine(img, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def render(plan, views, out_path, piece_marks, fr: Framing | None = None, *,
           panels: dict | None = None, size=practice_clips.CLIP_SIZE,
           fps: int = practice_clips.CLIP_FPS,
           timeout_s: float = practice_clips.CUT_TIMEOUT_S) -> None:
    """Cut ``plan`` from ``views`` (as `practice_clips.cut` takes them) into
    ``out_path``, framed by ``fr`` and with ``piece_marks`` (one per piece,
    `marks`) burned in. A view named in ``panels`` is an overhead panel
    (`Panel`): turned and kept at its own shape. Raises OSError or a
    SubprocessError when no clip was made, as `cut` does."""
    panels = panels or {}
    width, height = size
    work = (int(round(width * WORK_SCALE)), int(round(height * WORK_SCALE)))
    frame_bytes = work[0] * work[1] * 3
    # Each piece's place on the clip's clock, and its dots thinned as the
    # zoomed picture (or, unzoomed, the whole one) shows them.
    spans, t = [], 0.0
    zoom_px = (width / fr.box[2], height / fr.box[3]) if fr is not None else None
    pieces = []
    for p, m in zip(plan.pieces, piece_marks):
        spans.append((t, t + (p.t1 - p.t0)))
        t += p.t1 - p.t0
        zoomed = fr is not None and p.view == fr.view
        sx, sy = zoom_px if zoomed else (width, height)
        pieces.append({**m, "dots": thin(m.get("dots") or [], sx, sy,
                                         DOT_GAP if zoomed else DOT_GAP_WHOLE)})
    decode = practice_clips.decode_args(plan, views, size=work, fps=fps)
    decode += ["-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    encode = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
              "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-an",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
              "-movflags", "+faststart", "-fs", str(practice_clips.CLIP_MAX_BYTES), str(out_path)]
    with tempfile.TemporaryFile() as err_in, tempfile.TemporaryFile() as err_out:
        dec = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=err_in)
        enc = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=err_out)
        late = threading.Event()

        def kill():
            late.set()
            for proc in (dec, enc):
                proc.kill()

        watchdog = threading.Timer(timeout_s, kill)
        watchdog.start()
        try:
            i = 0
            while True:
                buf = dec.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                img = np.frombuffer(buf, np.uint8).reshape(work[1], work[0], 3)
                tc = i / fps
                k = next((j for j, (a, b) in enumerate(spans) if tc < b), len(spans) - 1)
                p = plan.pieces[k]
                ts = p.t0 + (tc - spans[k][0])
                panel = panels.get(p.view)
                if panel is not None:
                    box = panel.box(size)
                    if panel.flipped:
                        img = cv2.flip(img, -1)
                else:
                    box = box_at(fr, p.view, ts)
                out = _frame(img, box, size)
                draw(out, pieces[k], box, ts)
                try:
                    enc.stdin.write(out.tobytes())
                except BrokenPipeError:
                    break       # the encoder stopped: -fs reached, or it failed
                i += 1
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
            dec.stdout.close()
            dec.wait()
            enc.wait()
        except BaseException:
            kill()
            dec.wait()
            enc.wait()
            raise
        finally:
            watchdog.cancel()
        if late.is_set():
            raise subprocess.TimeoutExpired(encode, timeout_s)
        # The decoder stops early, not failing, when the encoder took no more.
        for proc, args, err in ((enc, encode, err_out), (dec, decode, err_in)):
            if proc.returncode != 0 and not (proc is dec and proc.returncode < 0):
                err.seek(0)
                raise subprocess.CalledProcessError(proc.returncode, args, stderr=err.read())
    practice_clips.check_clip(out_path)


def cut(plan, views, out_path, *, throw=None, sideviews=None, panels=None, **kw) -> None:
    """`render` for ``throw``: its framing, and its marks from ``sideviews``
    and, on a piece from an overhead panel, from ``panels`` (view -> `Panel`).
    Marks that cannot be placed cost the clip its marks, not the clip."""
    panels = panels or {}
    fr, m = None, [{} for _ in plan.pieces]
    if throw is not None:
        try:
            fr, m = framing(throw, plan, sideviews), marks(throw, plan, sideviews)
            m = [panel_marks(throw, panels[p.view]) if p.view in panels else got
                 for p, got in zip(plan.pieces, m)]
        except Exception as exc:  # noqa: BLE001
            log.warning("marks for %s failed: %r", throw.get("id"), exc)
            fr, m = None, [{} for _ in plan.pieces]
    render(plan, views, out_path, m, fr, panels=panels, **kw)
