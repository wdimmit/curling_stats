"""YOLO labels for the stone detector, and the rule that keeps people out of them.

Labels written straight from a detector teach a model exactly that detector's
blind spots and mistakes -- the expensive one being people in team colours. A
stone is either sitting still or running down the sheet, and a person does
neither; `settled_only` and `is_flight` are that rule, shared by the analysis
and the harvest. The rest is the label format: class order, box size, edge
clipping and the yaml ultralytics reads.
"""

from dataclasses import dataclass

CLASSES = ("red_stone", "yellow_stone")
_CLASS_OF = {"red": 0, "yellow": 1}


@dataclass(frozen=True)
class Label:
    """One YOLO box, normalised to the image."""

    cls: int
    cx: float
    cy: float
    w: float
    h: float

    def render(self) -> str:
        return f"{self.cls} {self.cx:.6f} {self.cy:.6f} {self.w:.6f} {self.h:.6f}"


# A stone showing less of itself than this is a sliver that teaches nothing.
MIN_VISIBLE_FRACTION = 0.25
# Whether to keep a box that runs off the edge of the panel, clipped to what is
# visible, or drop it.
#
# Off by default, and that is a measurement rather than a preference. Stones at
# the edge are real and countable -- one at (+1.80, +0.37) is 1.84 m from the
# tee, inside the house -- and dropping them means the model never learns to
# see one. But turning it on added 2023 edge labels, and a model trained with
# them found 11 of 17 observed deliveries where the model without them finds
# 16, while scoring *higher* on validation. Many of those labels are people in
# team colours standing at the edge during the clear-up. Worth revisiting with
# the clear-up kept out, which it was not when that was measured.
CLIP_EDGE_BOXES = False


def to_label(detection, box_px: float, w: int, h: int,
             clip: bool | None = None):
    """A normalised box for the stone, clipped to the image or dropped.

    See `CLIP_EDGE_BOXES` for why keeping edge boxes is not the default.
    """
    if clip is None:
        clip = CLIP_EDGE_BOXES
    half = box_px / 2.0
    if not clip:
        if (detection.x_px - half < 0 or detection.y_px - half < 0
                or detection.x_px + half > w or detection.y_px + half > h):
            return None
    x0 = max(0.0, detection.x_px - half)
    y0 = max(0.0, detection.y_px - half)
    x1 = min(float(w), detection.x_px + half)
    y1 = min(float(h), detection.y_px + half)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return None
    if (bw * bh) < MIN_VISIBLE_FRACTION * box_px * box_px:
        return None
    return Label(
        cls=_CLASS_OF[detection.color],
        cx=(x0 + x1) / 2.0 / w,
        cy=(y0 + y1) / 2.0 / h,
        w=bw / w,
        h=bh / h,
    )


def box_px(calib):
    """Label box size: the whole stone, centred on its handle.

    The handle is what we localise, but a box that also covers the granite gives
    the model more to recognise, and it is what makes a stone identifiable when
    the handle itself is blurred or half-swept.

    It depends only on the panel's scale, never on the detection -- which is
    what lets an empty frame have a box size at all. That matters twice over: a
    frame with no stones is exactly where a reviewer adds the first box, and
    across 120 videos there are 120 different values of ``px_per_m``, so one
    borrowed median would be wrong nearly everywhere.
    """
    from curling_score.geometry import constants as C

    return 2.0 * C.STONE_RADIUS_M * calib.px_per_m


def merge_detections(primary, extra):
    """Both detectors' stones for one frame, with duplicates resolved.

    Neither detector sees everything. Over 3133 sampled frames the model found
    369 stones the classical detector missed -- 6% of its own detections, which
    matches the rate of missing labels found by eye -- while the classical
    detector found 141 the model missed. Taking both and resolving the overlap
    covers each one's blind spots with the other's sight.

    The risk is that it covers each one's *mistakes* too. What makes that
    tolerable is that both detectors' mistakes are overwhelmingly transient --
    people, brooms, a shoe passing through -- and the quiet-span rule in
    `settled_only` is aimed squarely at those. A persistent mistake, such as a stone
    parked at the delivery end, survives either way and is no worse for the
    union.
    """
    from curling_score.detect.rocks import enforce_separation

    # Sorted by confidence inside, so where the two disagree about exactly
    # where a stone is, the more confident reading wins.
    return enforce_separation(list(primary) + list(extra))


def write_yaml(out_dir, train="images/train", val="images/val", root=None):
    """The dataset description ultralytics reads.

    No ``path:`` key by default. Ultralytics falls back to the directory
    holding the yaml, so a set can be rsynced anywhere and still work; an
    absolute path baked in here is why ds10's yaml only ever worked where it
    was written and every move needed a sed.

    Note that ``path: .`` is *not* the same thing -- "." exists, so it resolves
    against the working directory rather than the yaml -- and the yaml must be
    handed to ultralytics as an absolute path, or its parent is "." and the
    problem comes back.

    ``root`` re-enables an explicit path, which a combined set needs: its
    splits live in sibling directories, so they can only be named relative to
    a root above both.
    """
    def entry(key, value):
        if isinstance(value, (list, tuple)):
            return f"{key}:\n" + "".join(f"  - {v}\n" for v in value)
        return f"{key}: {value}\n"

    path = out_dir / "curling.yaml"
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    path.write_text(
        (f"path: {root}\n" if root is not None else "")
        + entry("train", train)
        + entry("val", val)
        + f"names:\n{names}\n"
    )
    return path


# A track has to hold still this long to be believed a stone on appearance alone.
# Clear of the previous stone settling and of the next one entering, so that
# nothing in the interval is moving under its own steam.
QUIET_MARGIN_S = 6.0
# How near a detection must be to a settled position to be that stone.
SETTLED_TOLERANCE_M = 0.20
# Too short an interval cannot establish what is settled and what is passing.
MIN_QUIET_S = 12.0
# There used to be a cap here, skipping any span far longer than the others on
# the grounds that it must hide a missed delivery whose stone would otherwise be
# deleted. `settled_only` now keeps runs that travel down-sheet, so the stone
# survives and the span can be cleaned -- which matters, because skipping the
# long spans left the people in them: an empty house on sheet 5 with a box on
# somebody's glove.


# A run has to cover this much sheet, this consistently down-sheet, to be a
# stone in flight rather than somebody walking about.
FLIGHT_MIN_TRAVEL_M = 1.5
FLIGHT_MIN_NET_FRACTION = 0.7
FLIGHT_MIN_SAMPLES = 8


def is_flight(track, min_samples: int = FLIGHT_MIN_SAMPLES,
              min_seconds: float | None = None) -> bool:
    """Does this track travel down-sheet the way a thrown stone does?

    The net-to-path ratio is what separates a stone from a person. Players and
    sweepers move constantly but mill about, so they cover a long path and end
    up near where they started; a delivery goes one way.

    ``min_samples`` was tuned at the 2 fps the label pass runs at, where eight
    samples is about three and a half seconds. A caller sampling faster should
    pass ``min_seconds`` instead, or a burst of noise lasting a moment clears
    the sample floor without being a flight at all.
    """
    if len(track.ts) < min_samples:
        return False
    if min_seconds is not None and track.ts[-1] - track.ts[0] < min_seconds:
        return False
    travel = track.ys[0] - track.ys[-1]
    path = sum(abs(track.ys[i + 1] - track.ys[i]) for i in range(len(track.ys) - 1))
    if travel < FLIGHT_MIN_TRAVEL_M or path <= 0:
        return False
    return travel / path >= FLIGHT_MIN_NET_FRACTION


def _flight_keys(frames, t0, t1):
    """Detections belonging to a run that travels down-sheet like a stone.

    Keeping only what holds still deletes a stone in flight, which is how a
    missed delivery came to erase the frames that would teach the model to
    catch it. Not cleaning at all leaves people in -- an empty house on sheet 5
    with a box on somebody's glove. A stone is either sitting somewhere or
    running down the sheet, and a person does neither, so keep both and drop
    the rest.
    """
    from curling_score.detect.delivery import _build_tracks

    win = [(t, list(d)) for t, d in frames if t0 <= t <= t1]
    keys = set()
    for tr in _build_tracks(win):
        if not is_flight(tr):
            continue
        for t, x, y in zip(tr.ts, tr.xs, tr.ys):
            keys.add((round(t, 2), tr.color, round(x, 2), round(y, 2)))
    return keys


def settled_only(frames, t0, t1, tolerance_m: float = SETTLED_TOLERANCE_M):
    """Frames in a quiet span, keeping the stones and dropping the rest.

    A stone is either sitting still or running down the sheet. Whatever is
    neither becomes background, which is the whole point: it is how the model
    is told that the thing following a player about is not a stone.
    """
    # The same notion of "settled" that delivery detection uses to decide
    # whether the sheet gained a stone, so the two cannot drift apart.
    from curling_score.detect.delivery import _settled_stones

    spots = _settled_stones(frames, t0, t1)
    if spots is None:
        return []
    flights = _flight_keys(frames, t0, t1)
    out = []
    for t, dets in frames:
        if not (t0 <= t <= t1):
            continue
        keep = []
        for d in dets:
            settled = any(
                c == d.color
                and ((x - d.x_m) ** 2 + (y - d.y_m) ** 2) ** 0.5 <= tolerance_m
                for c, x, y in spots)
            in_flight = (round(t, 2), d.color, round(d.x_m, 2),
                         round(d.y_m, 2)) in flights
            if settled or in_flight:
                keep.append(d)
        out.append((t, keep))
    return out
