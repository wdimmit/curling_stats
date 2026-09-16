"""Scan a clip's two side views once and bank what every moment holds.

``harvest/sideframes.py`` chooses 600 of these frames for a training set --
one half by where the stone sits along its travel, the other by which gate
``detect/longview.py`` used to refuse it. Both halves need every candidate
frame described up front, which is this module's job: decode a clip once,
run the colour scan and the crossing detector over it for both wide views and
both stone colours, and describe every moment -- including the ones with no
stone in them at all, since ``sideframes`` needs "clear" and "occluded"
moments to choose from as much as it needs a crossing.

What is *banked*, though, is only what got a JPEG. That distinction cost a
Critical: this module used to return a :class:`~curling_score.harvest.
sideframes.SideCandidate` for every moment scanned while writing at most
``MAX_PER_CLIP_VIEW`` of them, so ``sideframes.select`` could choose a frame no
file existed for -- about fourteen in every fifteen -- and the failure surfaced
as a "missing" count at the very last stage rather than as an error. The pool a
caller receives is now exactly the set of files on disk. ``stats``, by
contrast, still counts everything scanned: the refusal taxonomy is about what
the detector saw, not about what was cheap enough to keep.

The split between :func:`scan_moments` and :func:`scan_clip` mirrors
``harvest/pool.py``'s panel scan: ``scan_moments`` takes already-decoded
arrays and holds every judgement, so it is testable with synthetic frames and
never touches ffmpeg, a video file or a GPU; ``scan_clip`` is decode plus
``scan_moments`` and nothing else.

That cost is already doubled before ``MAX_PER_CLIP_VIEW`` (below) even comes
into it: ``longview.candidates`` runs once inside ``longview.find_in_frames``
(building the crossing track) and a second time directly in the loop below it
(finding that moment's own proposal), so every moment-colour pair pays for the
granite scan twice. That is ~480 a clip -- 24 s x 5 fps x 2 colours -- and
about 574,000 across the 1197-clip archive. Unavoidable without reaching into
``longview.find_in_frames`` itself, which is protected code.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from curling_score.detect import longview
from curling_score.geometry import constants as C
from curling_score.harvest import pool
from curling_score.harvest.sideframes import SideCandidate, position_of, stem_for
from curling_score.train import dataset

COLORS = ("red", "yellow")

# Per clip-view. See ``pick_writes`` -- 1197 clips x 24 s x 5 fps x 2 views is
# about 287,000 moments, and writing them all would be days of JPEG for a
# 600-frame set.
MAX_PER_CLIP_VIEW = 8
JPEG_QUALITY = 92
DETECT_FPS = 5.0

# How present a colour has to be before ``candidates`` even looks for a body
# under it. Matched to ``longview.candidates``'s own early-exit so "no
# proposal" and "no colour blob at all" can never disagree about the same
# pixels.
_MIN_BLOB_PX = 20


def crowding(win, cx: float, edge_row: float, expect_px: float) -> int:
    """How many bodies stand beside the stone at the moment it is seen.

    A sweeper 22 m away is a tall dark column and a stone is a short wide one,
    so counting dark spans in the band just above the ice separates them
    without anything having to recognise a person. Recorded rather than
    gated: it says how hard a frame is, and that belongs in the manifest
    where a person choosing what to label can read it.
    """
    grey = win.mean(axis=2)
    top = max(0, int(edge_row - 2 * expect_px))
    band = grey[top:int(edge_row) + 1]
    if band.size == 0:
        return 0
    ice = np.percentile(grey, 90)
    dark = (band < ice - longview.BODY_DARKER_THAN_ICE).mean(axis=0) > 0.5
    spans = [(a, b) for a, b in longview.runs(dark) if b - a > 0.3 * expect_px]
    return sum(1 for a, b in spans if not a <= cx <= b)


def _shifted(view):
    """``view``, moved onto the crop's own origin -- as ``longview.find_crossing``
    does before calling ``find_in_frames``.

    ``view.rect`` names where the crop sits in the *source* frame; the crop
    itself starts at (0, 0). Handing ``find_in_frames`` the unshifted rect
    would put the hog line at the wrong row and mistime every crossing in the
    clip, silently.
    """
    return type(view)(rect=(0, 0, view.rect[2], view.rect[3]),
                      tee_row=view.tee_row, hog_row=view.hog_row, d_m=view.d_m)


def _label_for(prop: longview.Proposal, color: str, shape) -> dataset.Label:
    """The proposal as a box, normalised to the crop.

    This is a **starting point for a person to correct, not the dataset**.
    Bottom is ``edge_row`` -- the stone's contact with the ice, the only row
    the perspective solve turns into a distance -- top is ``top_row``, and
    left/right come from ``cx`` and ``body_px``.
    """
    h, w = shape[:2]
    half = prop.body_px / 2.0
    x0, x1 = prop.cx - half, prop.cx + half
    y0, y1 = prop.top_row, prop.edge_row
    return dataset.Label(
        cls=dataset.CLASSES.index(f"{color}_stone"),
        cx=(x0 + x1) / 2.0 / w,
        cy=(y0 + y1) / 2.0 / h,
        w=(x1 - x0) / w,
        h=(y1 - y0) / h,
    )


def scan_moments(video_id, moments, views, clip_start_s) -> list[SideCandidate]:
    """Every judgement this module makes, from already-decoded crops.

    ``moments`` is ``(absolute_t, {view_name: crop})`` as ``harvest.pool.
    clip_moments`` returns it; ``views`` maps a view name to its
    ``sideview.SideView``, in the source frame's own coordinates (this shifts
    them itself before handing them to ``longview``).

    One :class:`SideCandidate` comes out per moment per colour that found
    something: two colour scans, red and yellow, each independently deciding
    what that moment shows for that colour. ``position`` is
    ``position_of(edge_row, hog_row)`` when a proposal survived, falling back
    to ``"occluded"`` when a coloured handle was seen but no granite body was
    found under it, and ``"clear"`` when there was no coloured handle at all
    -- in which case ``color`` is reset to ``""``, since no colour scan
    actually proposed anything for a person to look at. A moment where
    *neither* colour found anything gets exactly one ``"clear"`` row rather
    than one per colour scan: with nothing to attribute to a colour, the two
    scans' "clear" rows would otherwise be identical down to the stem -- two
    objects for one JPEG. ``sideframes.select`` dedupes on the stem itself,
    not on object identity (fixed in ``efc6e22``), and ``pick_writes`` and
    ``sidestages._supply`` each dedupe by stem too -- so no caller today is
    fooled by the duplicate. It is collapsed here anyway because none of them
    should have to be: a row that describes the absence of a colour has no
    colour to attribute, so emitting one per colour scan invents a distinction
    that does not exist, and every consumer then pays to undo it. Collapsed
    once, at the source. A moment with a real
    proposal or an ``"occluded"`` blob under one colour still gets its own
    row for that colour even when the other colour's scan of the same moment
    is "clear" -- only two identical "clear" rows collapse.

    ``outcome`` is not the moment's own verdict: it is the whole clip-view-
    colour window's crossing key from ``longview.find_in_frames``, stamped
    onto every moment in that window alike, because that is the question the
    outcome half of ``sideframes.select`` asks -- which gate this *window*
    hit, not what any one frame within it happened to show.
    """
    out: list[SideCandidate] = []
    for view_name, view in views.items():
        pairs = [(t, crops[view_name]) for t, crops in moments
                 if view_name in crops]
        if not pairs:
            continue
        times = [t for t, _ in pairs]
        frames = [img for _, img in pairs]
        shifted = _shifted(view)

        # Which moments (by t_abs) already got their one "clear" row from an
        # earlier colour scan in this view -- see the docstring above.
        clear_seen: set = set()

        for color in COLORS:
            crossing = longview.find_in_frames(frames, shifted, color, times)
            for t, img in pairs:
                win = np.asarray(img, dtype=np.float32)
                props = longview.candidates(win, color, longview.STONE_WIDTH_AT_HOG_PX)
                prop = min(props, key=lambda p: abs(p.edge_row - view.hog_row)) \
                    if props else None

                if prop is not None:
                    position = position_of(prop.edge_row, view.hog_row)
                    row_color = color
                    labels = (_label_for(prop, color, win.shape),)
                    edge_row = prop.edge_row
                    crowd = crowding(win, prop.cx, prop.edge_row,
                                     longview.STONE_WIDTH_AT_HOG_PX)
                else:
                    has_blob = longview.colour_mask(win, color).sum() >= _MIN_BLOB_PX
                    position = "occluded" if has_blob else "clear"
                    row_color = color if has_blob else ""
                    labels = ()
                    edge_row = None
                    crowd = 0

                if position == "clear":
                    if t in clear_seen:
                        continue
                    clear_seen.add(t)

                out.append(SideCandidate(
                    video_id=video_id, view=view_name, t_abs=t,
                    clip_start_s=clip_start_s, position=position,
                    outcome=crossing.key, color=row_color, crowding=crowd,
                    edge_row=edge_row, labels=labels,
                ))
    return out


def pick_writes(candidates, max_per_clip_view: int = MAX_PER_CLIP_VIEW):
    """Which of a clip-view's moments earn a JPEG.

    Round-robins across the ``position`` bins the candidates actually landed
    in, in a fixed order, so every bin present gets one pick before any bin
    gets a second -- a clip that produced only a "crossing" and a "clear"
    bin fills both before doubling either, and a clip rich in one bin cannot
    crowd out the others just by being larger. Kept separate from
    ``build_video_pool`` so the cap is testable without touching a disk.

    ``scan_moments`` gives two candidates per moment -- one per colour scan --
    sharing one ``stem`` (video, view and time, not colour). Only one JPEG can
    exist at that stem, so one of the two is kept and its twin dropped before
    the spread runs, rather than letting both compete for a cap slot over what
    would be the same written frame. The one kept is whichever **carries a
    proposal** (``edge_row is not None``, equivalently ``labels`` non-empty):
    for a yellow delivery, the red scan of the same moment finds nothing and
    reports "clear", and a stone's colour is exactly the fact a "clear" row
    cannot express -- keeping it over the real yellow row would spend the
    clip's whole write budget on rows that record the absence of a red stone
    and never write the crossing at all.

    When neither row carries a proposal, this falls back to stability -- the
    first one seen, red before yellow, since that ordering carries no
    information either way.

    When *both* do, red wins for the same arbitrary reason, and the yellow
    row's box is dropped. The frame is still written, since both rows name the
    same stem and so the same image; what is lost is yellow's proposed box and
    its share of the frame's ``position`` and ``color``, so the frame reaches
    selection described as a red one. How often that happens is not measured,
    and it should not be assumed rare: a delivery arrives at a house that
    already holds stones, which is the ordinary case rather than the
    exception. The cost is bounded -- a person labelling the frame draws the
    missing box, which is what they are there for -- but it does skew the
    stratification toward red, and measuring it is worth a look once the
    archive run has produced real counts.
    """
    if max_per_clip_view <= 0 or not candidates:
        return []
    by_stem = {}
    for c in sorted(candidates, key=lambda c: (c.t_abs, c.stem)):
        existing = by_stem.get(c.stem)
        if existing is None or (existing.edge_row is None and c.edge_row is not None):
            by_stem[c.stem] = c
    groups: dict[str, list] = {}
    for c in by_stem.values():
        groups.setdefault(c.position, []).append(c)
    order = sorted(groups)
    cursor = {p: 0 for p in order}

    chosen = []
    progressed = True
    while len(chosen) < max_per_clip_view and progressed:
        progressed = False
        for p in order:
            if len(chosen) >= max_per_clip_view:
                break
            i = cursor[p]
            if i < len(groups[p]):
                chosen.append(groups[p][i])
                cursor[p] = i + 1
                progressed = True
    return chosen


def scan_clip(video_id, clip_path, views, *, fps: float = DETECT_FPS):
    """Decode one clip and scan every moment it produced.

    One decode serves both views and both colours, exactly as
    ``harvest.pool.clip_moments`` does for the overhead panels. Returns
    ``(candidates, moments)`` -- ``moments`` is the same ``(absolute_t,
    {view: crop})`` list ``clip_moments`` returned, handed back so a caller
    that wants to write a JPEG for a chosen moment does not have to decode
    the clip a second time.

    ``clip_moments`` decodes ``bgr24`` (matching ``cv2.imwrite``'s own
    channel order, which is what a chosen moment is written with), but
    ``longview``'s colour mask reads channel 0 as red -- ``detect/longview.
    py``'s own ``decode`` asks ffmpeg for ``rgb24``, and ``tests/synth.py``
    says so outright. Handing ``scan_moments`` the raw BGR crops would swap
    red and yellow silently, so it gets an RGB view built for this call only;
    the BGR ``moments`` returned to the caller are untouched.
    """
    from curling_score.ingest import frames as F

    rects = {name: tuple(view.rect) for name, view in views.items()}
    pts_start = F.stream_start_s(clip_path)
    moments = pool.clip_moments(clip_path, pts_start, rects, fps)
    if not moments:
        return [], moments
    clip_start_s = moments[0][0]
    rgb_moments = [(t, {name: crop[..., ::-1] for name, crop in crops.items()})
                  for t, crops in moments]
    candidates = scan_moments(video_id, rgb_moments, views, clip_start_s)
    return candidates, moments


def build_video_pool(video_id, clip_paths, video_views, out_dir, *,
                     fps: float = DETECT_FPS,
                     max_per_clip_view: int = MAX_PER_CLIP_VIEW,
                     jpeg_quality: int = JPEG_QUALITY):
    """Every candidate one video's clips can offer, with a capped set written out.

    Returns ``(candidates, stats)``. ``candidates`` is only the moments that
    got a JPEG -- ``sideframes.select`` chooses from exactly this list, so a
    frame it picks always has pixels on disk for ``stage_build`` to copy. It
    used to be every moment scanned, written or not: 1197 clips x 24 s x 5 fps
    x 2 views is about 287,000 moments, of which ``pick_writes`` (via
    ``MAX_PER_CLIP_VIEW``, above) keeps roughly 19,000 -- banking the larger
    number let ``select`` choose a frame ``pick_writes`` had discarded, and
    ``stage_build`` could only report it missing. ``stats`` still counts over
    everything scanned, not just what was written: ``clips`` and ``moments``
    seen, frames ``written``, and ``refusals`` by ``longview.KEYS`` entry -- a
    count of windows, one clip-view-colour at a time, not of the many
    candidate rows that window's refusal gets stamped onto. That is the
    refusal taxonomy's job -- what the detector saw -- and narrowing it to
    what got written would break the diagnostic.

    The labels written alongside a JPEG are the detector's opinion, not the
    dataset -- see :func:`_label_for`. A moment the detector refused on its
    own -- no proposal found for it -- gets no box at all, but a moment
    sitting inside a *window* ``find_in_frames`` refused as a whole can still
    carry one: the refusal is stamped as ``outcome`` onto every moment in
    that window regardless of what any one of them shows, and roughly half
    the final set is chosen for exactly that outcome, so this is not a rare
    case.
    """
    import cv2

    from curling_score.harvest import sideviews

    views = dict(sideviews.usable_views(video_views))
    out_path = Path(out_dir) / video_id
    out_path.mkdir(parents=True, exist_ok=True)

    all_candidates: list[SideCandidate] = []
    stats = {"clips": 0, "moments": 0, "written": 0, "refusals": {}}

    for clip_path in clip_paths:
        candidates, moments = scan_clip(video_id, clip_path, views, fps=fps)
        if not moments:
            continue
        stats["clips"] += 1
        stats["moments"] += len(moments)

        # `scan_moments` stamps one window's whole outcome onto every moment
        # inside it, so distinct (view, color, outcome) triples -- rather
        # than every candidate row bearing that outcome -- is a count of
        # windows, not of the many moments each one covers.
        for view_name, color, outcome in {(c.view, c.color, c.outcome)
                                          for c in candidates}:
            if outcome and outcome != longview.KEY_OK:
                stats["refusals"][outcome] = stats["refusals"].get(outcome, 0) + 1

        by_view: dict[str, list] = {}
        for c in candidates:
            by_view.setdefault(c.view, []).append(c)

        for view_name, view_cands in by_view.items():
            crop_at = {t: crops[view_name] for t, crops in moments
                      if view_name in crops}
            for c in pick_writes(view_cands, max_per_clip_view):
                img = crop_at.get(c.t_abs)
                if img is None:
                    continue
                stem = stem_for(video_id, c.view, c.t_abs)
                cv2.imwrite(str(out_path / f"{stem}.jpg"), img,
                           [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                stats["written"] += 1
                all_candidates.append(c)

    all_candidates.sort(key=lambda c: (c.t_abs, c.stem))
    return all_candidates, stats


# How far past the hog line a delivery is still worth a frame. Three metres is
# about 52 image rows at the club's fitted geometry -- enough to hold the
# "past" bin without running down into the foreground, where perspective
# stretches a stone across half the frame.
PAST_HOG_M = 3.0


def ice_bounds(view) -> tuple[int, int]:
    """The rows a delivery crossing the hog line can occupy.

    The first attempt let ``longview.candidates`` propose anywhere in the
    view. The racks of stones stored beside the sheet are red and in every
    frame, and they were the entire 10:1 red skew in the resulting set --
    furniture, not play.

    This is a **depth** bound, not the lateral one first proposed.
    ``SideView`` maps image rows to metres *along* the sheet and carries no
    lateral scale whatever, so there is no sideline to derive; but the racks
    do not sit beside the sheet in image terms, they sit *above* the house on
    the platform behind it. Measured on real frames from VXU9xwmugRg they run
    rows ~365-405, against a fitted tee at ~435 and hog at ~520.

    So the band runs from the tee to ``PAST_HOG_M`` past the hog line. The tee
    is the top rather than the back line (row ~415) or the hack (row ~398)
    because only the tee clears the racks with any margin -- the hack line
    computes to a row *inside* the measured rack band, and would have excluded
    nothing. It is also the right bound for what this set is for: a stone still
    behind the house is not near the line, and this pool exists to cluster
    frames at the line.
    """
    top = view.row_for(0.0)                                  # the tee
    bottom = view.row_for(C.TEE_TO_HOGLINE_M + PAST_HOG_M)
    return int(round(top)), int(round(bottom))


# A stone is a squat disc: 0.114 m tall against 0.284 m across, a height to
# width ratio of 0.40. A person standing or crouching on the ice is not, and
# with the racks excluded they are what is left to be mistaken for one.
#
# Measured over 492 on-ice proposals from 30 real windows, the ratio is plainly
# bimodal -- a mode at 0.4-0.6 sitting on the physical figure, a trough at
# 1.0-2.0 holding 27 proposals, and a second mode at 2.0-3.0 with a long tail
# out past 15. This sits in that trough, at 3.75x the physical ratio, so it can
# only reject something far too tall to be granite.
STONE_ASPECT_MAX = 1.5


def _proposal_on_the_ice(win, color: str, view, bounds):
    """The proposal nearest the hog line that could actually be a stone there.

    Two filters, and both come from looking at real output rather than from
    reasoning about it. The row band drops the racks of stones stored behind
    the house. The aspect bound drops the players: with the racks gone, the
    boxes that were still not on stones were on people wearing the scanned
    colour -- most often the crouching sweeper, who stands at the very rows a
    delivery occupies, so no row bound could separate them. Shape does.
    """
    top, bottom = bounds
    props = [p for p in longview.candidates(win, color,
                                            longview.STONE_WIDTH_AT_HOG_PX)
             if top <= p.edge_row <= bottom
             and (p.edge_row - p.top_row) <= STONE_ASPECT_MAX * p.body_px]
    if not props:
        return None
    return min(props, key=lambda p: abs(p.edge_row - view.hog_row))


def frames_for_window(moments, view, window, *, n_near: int, n_far: int
                      ) -> list[SideCandidate]:
    """Pick the frames worth keeping from one delivery's window.

    ``moments`` is ``(t_abs, crop)`` for this window's own camera, already
    decoded, and the crops must be **RGB** -- ``longview.colour_mask`` reads
    channel 0 as red. ``ingest.frames`` decodes ``bgr24``, so a caller feeding
    it straight from there must flip, exactly as ``scan_clip`` does; get this
    backwards and every red delivery is scanned as yellow and the other way
    about. ``window`` is a :class:`harvest.sideshots.Window`: the shot list
    says a delivery crossed the line inside it, which is what makes this pool
    different from the blind scan it replaces -- there, ~97% of sampled
    moments held no delivery at all.

    Frames are ranked by ``|edge_row - hog_row|``, not by time. That is what
    makes the two window widths comparable: a release-anchored window is 4.5 s
    and a rest-anchored fallback is 12 s, and a fixed offset into the second
    would rarely be the crossing. ``n_near`` comes off the top of that ranking
    and ``n_far`` off the approach -- frames short of the line, which is where
    a detector has to commit before the paint helps it.

    A window in which nothing was proposed anywhere on the ice still yields
    ``n_near`` frames from its centre, with ``labels=()``. The shot list says a
    stone was there; a frame the classical detector cannot read is precisely
    the frame the replacement most needs, and dropping it would rebuild the
    ds3 trap of validating against the old detector's opinion.

    ``clip_start_s`` is the window's own ``t0``, so ``sideframes.select``'s
    ``MAX_PER_CLIP`` treats one delivery as one clip and no single throw can
    fill a bin with near duplicates of itself.
    """
    if not moments:
        return []

    bounds = ice_bounds(view)
    shifted = _shifted(view)
    times = [t for t, _ in moments]
    frames = [img for _, img in moments]
    crossing = longview.find_in_frames(frames, shifted, window.color, times)

    scored = []
    for t, img in moments:
        win = np.asarray(img, dtype=np.float32)
        prop = _proposal_on_the_ice(win, window.color, view, bounds)
        if prop is None:
            continue
        scored.append((abs(prop.edge_row - view.hog_row), t, win, prop))

    def row_for(t, win, prop):
        if prop is None:
            return SideCandidate(
                video_id=window.video_id, view=window.camera, t_abs=t,
                clip_start_s=window.t0, position="clear", outcome=crossing.key,
                color="", crowding=0, edge_row=None, labels=())
        return SideCandidate(
            video_id=window.video_id, view=window.camera, t_abs=t,
            clip_start_s=window.t0,
            position=position_of(prop.edge_row, view.hog_row),
            outcome=crossing.key, color=window.color,
            crowding=crowding(win, prop.cx, prop.edge_row,
                              longview.STONE_WIDTH_AT_HOG_PX),
            edge_row=prop.edge_row,
            labels=(_label_for(prop, window.color, win.shape),))

    if not scored:
        # Nothing readable anywhere on the ice: keep the window's middle.
        mid = len(moments) // 2
        lo = max(0, mid - n_near // 2)
        return [row_for(t, None, None) for t, _ in moments[lo:lo + n_near]]

    out: list[SideCandidate] = []
    taken: set[float] = set()
    for _d, t, win, prop in sorted(scored, key=lambda s: s[0])[:n_near]:
        out.append(row_for(t, win, prop))
        taken.add(t)

    approach = [s for s in scored
                if s[3].edge_row < view.hog_row and s[1] not in taken]
    # Furthest back first: the frames where the stone is still short of the
    # line, which the near pass by construction never reaches.
    for _d, t, win, prop in sorted(approach, key=lambda s: -s[0])[:n_far]:
        out.append(row_for(t, win, prop))

    out.sort(key=lambda c: c.t_abs)
    return out
