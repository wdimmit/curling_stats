"""Build a YOLO training set from the classical detector's output.

Auto-labelling with colour thresholding alone would teach a model exactly the
blind spots we are trying to escape: it would learn to miss the same blurred,
dim and swept-over stones. The way out is temporal. A stone seen a tenth of a
second either side of a gap was demonstrably there during it, so we can label
frames the threshold missed and let the model learn what they look like.

Only *short* gaps are filled. A long one usually means somebody is standing over
the stone, and a label there would teach the model to hallucinate stones behind
people -- worse than the miss we started with.
"""

from dataclasses import dataclass, replace

CLASSES = ("red_stone", "yellow_stone")
_CLASS_OF = {"red": 0, "yellow": 1}

# A gap this long is threshold noise or a moment's occlusion; longer is
# probably somebody standing over the stone, and a label there would teach the
# model to hallucinate stones behind people.
#
# It was 0.5 s, which is *smaller than one frame interval* at the 2 fps the set
# is built at -- so a single dropped frame is a 1.0 s gap and nothing was ever
# filled. Measured over 300 s of one panel: 0 of 24 gaps. At 1.5 s the eleven
# one-frame dropouts and the two two-frame ones are filled, which is the case
# this was written for: a player crossing in front of a stone for a moment.
MAX_FILL_GAP_S = 1.5
INTERPOLATED_CONFIDENCE = 0.5


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
# team colours standing at the edge during the clear-up. Worth revisiting once
# `play_span` has kept the clear-up out, which it did not when that was
# measured.
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


def _lerp(a, b, f):
    return a + (b - a) * f


def interpolate_gaps(frames, max_gap_s: float = MAX_FILL_GAP_S, calib=None):
    """Fill short detection gaps in each stone's track.

    Works per track rather than per frame, so a stone that flickers out for a
    frame or two is restored where it demonstrably was, without inventing
    anything at times when it was not seen on either side.

    ``calib`` converts the filled sheet position back to pixels. Labels are
    written in pixels, so without it a filled box would land in the wrong place;
    when it is omitted the pixel fields are interpolated directly instead, which
    is close enough over a gap this short.
    """
    from curling_score.detect.delivery import _build_tracks

    frames = [(t, list(d)) for t, d in frames]
    times = [t for t, _ in frames]
    index = {round(t, 6): i for i, t in enumerate(times)}

    # Remember a real detection per colour so filled ones keep the same shape.
    template = {}
    for _, dets in frames:
        for d in dets:
            template.setdefault(d.color, d)

    for track in _build_tracks(frames):
        proto = template.get(track.color)
        if proto is None:
            continue
        for a in range(len(track.ts) - 1):
            b = a + 1
            t0, t1 = track.ts[a], track.ts[b]
            gap = t1 - t0
            if gap <= 0 or gap > max_gap_s:
                continue
            for t in times:
                if not (t0 < t < t1):
                    continue
                f = (t - t0) / gap
                x = _lerp(track.xs[a], track.xs[b], f)
                y = _lerp(track.ys[a], track.ys[b], f)
                if calib is not None:
                    px, py = calib.to_pixels(x, y)
                else:
                    px = _lerp(proto.x_px, proto.x_px, f)
                    py = _lerp(proto.y_px, proto.y_px, f)
                frames[index[round(t, 6)]][1].append(
                    replace(
                        proto,
                        x_m=x,
                        y_m=y,
                        x_px=float(px),
                        y_px=float(py),
                        confidence=INTERPOLATED_CONFIDENCE,
                    )
                )
    return frames


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


def box_px_for(detection, calib):
    """The same size, for callers that happen to hold a detection.

    The detection is not used; see :func:`box_px`.
    """
    return box_px(calib)


def merge_detections(primary, extra):
    """Both detectors' stones for one frame, with duplicates resolved.

    Neither detector sees everything. Over 3133 sampled frames the model found
    369 stones the classical detector missed -- 6% of its own detections, which
    matches the rate of missing labels found by eye -- while the classical
    detector found 141 the model missed. Taking both and resolving the overlap
    covers each one's blind spots with the other's sight.

    The risk is that it covers each one's *mistakes* too. What makes that
    tolerable is that both detectors' mistakes are overwhelmingly transient --
    people, brooms, a shoe passing through -- and the quiet-span and play-span
    rules are aimed squarely at those. A persistent mistake, such as a stone
    parked at the delivery end, survives either way and is no worse for the
    union.
    """
    from curling_score.detect.rocks import enforce_separation

    # Sorted by confidence inside, so where the two disagree about exactly
    # where a stone is, the more confident reading wins.
    return enforce_separation(list(primary) + list(extra))


def frames_for_end(video_path, setup, start_s, end_s, fps, deliveries=None,
                   extra=None):
    """Detections for one end, with short gaps filled.

    Pass ``extra`` -- a mapping from rounded time to detections -- to take the
    union with another detector's output; see `merge_detections`.

    Pass ``deliveries`` to clean the labels using the end's own structure.
    ``keep_stone_like`` asks only that a detection hold its place for six
    seconds, which a person standing still does too -- a player's red shoes
    held theirs for twelve. Knowing when each stone was thrown says when
    *everything* should be stationary, and in those spans anything that moves
    is provably not a stone.
    """
    from curling_score.detect import rocks
    from curling_score.ingest import frames as F

    seq = []
    for t, img in F.window(video_path, start_s, end_s, fps, crop=setup.rect):
        dets = rocks.find_stones(img, setup.calib)
        if extra is not None:
            dets = merge_detections(dets, extra.get(round(t, 1), ()))
        seq.append((t, img, dets))
    # Drop anything that does not behave like a stone (chiefly sweepers in
    # team-coloured jackets), then fill short detection gaps.
    cleaned = keep_stone_like([(t, d) for t, _, d in seq])
    filled = interpolate_gaps(cleaned, calib=setup.calib)
    if deliveries:
        filled = _clean_quiet_spans(filled, deliveries, start_s, end_s)
    out = [(t, img, dets) for (t, img, _), (_, dets) in zip(seq, filled)]
    if deliveries:
        span = play_span(deliveries, start_s, end_s)
        if span is not None:
            lo, hi = span
            out = [x for x in out if lo <= x[0] <= hi]
    return out


def _clean_quiet_spans(frames, deliveries, start_s, end_s):
    """Replace labels inside quiet spans with the settled stones alone."""
    by_t = dict(frames)
    for lo, hi in quiet_intervals(deliveries, start_s, end_s):
        for t, dets in settled_only(frames, lo, hi):
            by_t[t] = dets
    return [(t, by_t[t]) for t, _ in frames]


def write_split(out_dir, split, samples, calib, stride=1, prefix="",
                clip_edges=None, keep_empty=False):
    """Write images and YOLO label files for one split.

    By default only frames carrying at least one stone are kept: when the
    labels come from a detector, an "empty" frame means only that nothing was
    found, which is as often a miss as an empty house, and there are far more
    of them than useful ones.

    ``keep_empty`` writes those frames anyway, with an empty label file, which
    ultralytics reads as a background image. It is meant for a set whose labels
    a person has checked -- then "nothing here" is an assertion rather than an
    absence, and it is the only way the model is ever told what no stone looks
    like. Every set up to ds10 skipped them by construction.
    """
    import cv2

    img_dir = out_dir / "images" / split
    lbl_dir = out_dir / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for i, (t, img, dets) in enumerate(samples):
        if i % stride:
            continue
        if not dets and not keep_empty:
            continue
        h, w = img.shape[:2]
        box = box_px(calib)
        labels = [to_label(d, box, w, h, clip=clip_edges) for d in dets]
        labels = [lab for lab in labels if lab is not None]
        if not labels and not keep_empty:
            continue
        name = f"{prefix}{t:09.2f}".replace(".", "_")
        cv2.imwrite(str(img_dir / f"{name}.jpg"), img,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        # "".join over "\n".join so an empty list gives an empty file rather
        # than a stray newline; identical output for a non-empty one.
        (lbl_dir / f"{name}.txt").write_text(
            "".join(lab.render() + "\n" for lab in labels)
        )
        written += 1
    return written


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


# A little before the first stone enters, to catch its run-up.
PLAY_LEAD_S = 5.0


def play_span(deliveries, start_s, end_s, lead_s: float = PLAY_LEAD_S):
    """When stones were being thrown, stopping short of the clear-up.

    Ends at the *second to last* delivery, not the last. Ending at the last one
    is not enough, for a reason the ends themselves demonstrate: the last thing
    kept in game 1 ends 3, 4 and 6 is confirmed only by `house-remove`, and in
    end 4 it is the clearing itself -- the yellow at 3554.1 that the players
    push about after the red at 3546.5 has decided the end. A span reaching to
    its resting place sweeps in frames where people in team colours are
    labelled as stones and real stones in the house are not.

    The last real delivery cannot be told apart from the clearing that follows
    it -- that is the same problem the scoring faces -- so its frames go too.
    That costs roughly one delivery in each end, about 3% of the set, and it is
    worth it: every bad label found by eye so far came from this period.
    """
    ds = sorted(deliveries, key=lambda d: d.t_enter)
    if not ds:
        return None
    # Drop the first as well as the last. Sheet 5's end 1 offers a red
    # "delivery" at 68.2 s and the next at 172.7 -- a 104 second gap where the
    # rest of the end runs 40 to 50 -- so the first is the players setting up,
    # and taking it at face value dragged the span back to 65 s and swept in
    # every person milling about an empty house.
    first = ds[1] if len(ds) >= 3 else ds[0]
    last = ds[-2] if len(ds) >= 2 else ds[-1]
    return (max(start_s, first.t_enter - lead_s), min(end_s, last.t_rest))


def quiet_intervals(deliveries, start_s, end_s,
                    margin_s: float = QUIET_MARGIN_S,
                    min_s: float = MIN_QUIET_S):
    """Spans in which every stone on the sheet is already at rest.

    This is what makes honest labels possible without hand-labelling. The model
    was trained on the classical detector's output, so it inherited that
    detector's idea of a stone -- including a player's red shoes, which it
    rings and follows around the sheet. Labelling more of the same cannot
    unteach it.

    Between one stone settling and the next being thrown, nothing on the ice is
    moving except people. So the stones are exactly the detections that hold a
    position, and everything else in the frame is provably not one. Stones
    spend most of an end at rest, so most frames can be labelled this way.
    """
    ds = sorted(deliveries, key=lambda d: d.t_enter)
    if not ds:
        return []
    edges = [(start_s, ds[0].t_enter)]
    edges += [(a.t_rest, b.t_enter) for a, b in zip(ds, ds[1:])]
    edges.append((ds[-1].t_rest, end_s))
    spans = []
    for lo, hi in edges:
        lo, hi = lo + margin_s, hi - margin_s
        if hi - lo >= min_s:
            spans.append((lo, hi))
    return spans


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


STATIC_HOLD_S = 6.0
STATIC_TOLERANCE_M = 0.10


def keep_stone_like(frames):
    """Drop detections that do not behave like stones.

    Auto-labelling straight from the detector teaches the model its mistakes,
    and the expensive one is people: a sweeper's team-coloured jacket reads as
    stones, and because they run alongside the delivery those phantoms mimic a
    throw in every respect. Filtering them at inference time works but costs
    real deliveries, so the knowledge belongs in the model instead.

    A detection earns its label by belonging to a track that either sits still
    for a good while, or is confirmed as a delivery. A jacket does neither.
    """
    from curling_score.detect.delivery import _build_tracks, find_deliveries

    frames = [(t, list(d)) for t, d in frames]
    if not frames:
        return []

    # A track is a delivery track when a confirmed delivery of the same colour
    # ends inside its lifetime -- including one that left the sheet rather than
    # settling, which is what a takeout does.
    deliveries = find_deliveries(frames)

    keep_ids = set()
    for track in _build_tracks(frames):
        span = track.ts[-1] - track.ts[0]
        # Held still for long enough to be a stone sitting on the ice.
        moved = max(
            ((track.xs[i] - track.xs[0]) ** 2 + (track.ys[i] - track.ys[0]) ** 2)
            ** 0.5
            for i in range(len(track.ts))
        )
        static_enough = span >= STATIC_HOLD_S and moved <= STATIC_TOLERANCE_M
        # ...or it carries a confirmed delivery, which makes its in-flight
        # frames genuine examples of a stone in motion -- the only examples of
        # that the model will ever get.
        is_delivery = any(
            d.color == track.color
            and track.ts[0] - 0.5 <= d.t_rest <= track.ts[-1] + 0.5
            for d in deliveries
        )
        if static_enough or is_delivery:
            keep_ids.update(id_ for id_ in _track_keys(track))

    out = []
    for t, dets in frames:
        out.append((t, [d for d in dets if (round(t, 3), _key_of(d)) in keep_ids]))
    return out


def _key_of(d):
    return (d.color, round(d.x_m, 3), round(d.y_m, 3))


def _track_keys(track):
    for i in range(len(track.ts)):
        yield (round(track.ts[i], 3),
               (track.color, round(track.xs[i], 3), round(track.ys[i], 3)))
