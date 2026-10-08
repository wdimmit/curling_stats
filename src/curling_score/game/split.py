"""The long split: hog line to hog line, timed at the paint.

Both hog lines are in view, one in each overhead panel, and a stone crosses
them at 5-10 cm per sample. The split is two line crossings and a distance.

The panels cannot say where either line is in metres. ``geometry/calibrate.py``
fits a single ``px_per_m`` from the house rings, and the along-sheet scale falls
to roughly a third of that by the top of the frame, so the paint reports at
about +4.4 to +4.7 against a real 6.4 m. None of which matters to a crossing: a
tripwire needs where the paint sits in the panel's own numbers. For a while
that was one measured constant for every panel of every video, and hand marks
read in the overhead panels showed it was up to half a second wrong -- each
panel's paint sits somewhere different. It is now found per panel, in the paint
(``geometry/hogpaint.py``), and read at the stone's own lateral position.

The throwing end's crossing comes from the long camera (``game/hogtime.py``);
the destination's from that end's panel, attached to the shot by
``game/fartime.py``. See
``docs/superpowers/specs/2026-09-22-phase3-far-tripwire-design.md``.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

# Both lines are timed at the stone's leading edge FIRST touching the paint: the
# throwing line's inside edge, ``TEE_TO_HOGLINE_M`` from that tee (what the
# phase-2 hand marks, and so the long camera, were validated against), and the
# destination line's outer edge, one line-width further out. So the leading
# edge covers tee-to-tee less one of each: 34.747 - 6.401 - 6.503 = 21.843 m.
#
# This replaced 22.229 m, which assumed the stone's centre one radius short of
# the inside edge at both ends. Only ``Split.speed_m_s`` and the pairing check
# read it; a published split's seconds never did.
BASELINE_M = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - (C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M)

# How much faster a stone may appear to be crossing the second hog line than
# the first before the pairing behind it is disbelieved. In exact arithmetic
# this is 1.0 -- a stone only ever slows -- and the slack covers two estimates
# taken 20 s apart from different cameras at different sample rates.
SPEED_TOLERANCE = 1.4

# How far apart in time the two samples either side of a line may be and still
# give a speed AT that line. A release track at 5 fps that loses the stone under
# the sweepers and picks it up again -- or picks up something else near the
# panel's edge -- seconds later still straddles the line, but the rate between
# those two is an average over half the panel. S1 10/06 e8 r12's was read
# across 3.4 s, 0.71 against an arrival of 1.01, and e7 r16's across 3.0 s at
# the live run's sample phase; both refused a good pairing by this tolerance.
# A missing speed is not a refusal: `long_split` then bounds the mean by the
# side view's speed at the hog line instead, which is in real metres. Across
# the 11-video harness 102 throwing-panel and 32 arriving-panel speeds were
# read across such gaps; dropping them gained one split and lost none.
SPEED_SPAN_MAX_S = 0.5

# How far the side view and the panel may disagree about the same crossing
# before both are disbelieved. They are independent cameras timing one painted
# line, so a real disagreement means one of them found the wrong object and
# nothing here can say which.
#
# UNMEASURED, and worth knowing that before trusting it. 0.25 was chosen as the
# acceptance bar in the design doc -- "no |t_hog - panel| above 0.25 s on the
# overlap" -- and then reused here as a runtime veto, which is not the same
# job: a bar says what would be good enough, a veto decides what ships.
#
# It is now the single largest source of refusals. scripts/split_coverage.py
# measured 25 of 55 unpublished crossings lost to this gate on one video, and
# the pre-veto disagreement over 48 paired crossings had a median of 0.261 s --
# just above the threshold, so roughly half the comparable population fails it.
# That is a threshold sitting in the middle of its own distribution, which is
# where a number does the most damage per unit of wrongness.
#
# MEASURED, 2026-09-16, exactly as this note asked. On AEqLTgM25Tc, 60 paired
# crossings with the side view proposed by ds13b:
#
#   ...0.21 0.22 0.25 0.25 0.26 0.26 0.27 0.28 0.28 0.28 0.28 0.29
#   [ nothing whatever between 0.288 and 0.801 ]
#   0.80 0.85 0.86 0.87 0.89 0.89 0.90 0.93 0.94 0.96 0.97 0.98
#
# So it IS bimodal, the valley is 0.513 s wide and empty, and 0.25 sits on the
# near cluster's shoulder rather than in the valley: 11 of 22 refusals are
# between 0.25 and 0.29, refused by a hair.
#
# And the far cluster is not a tail of mispairings. All twelve have the same
# sign -- the panel reads 0.80-0.98 s LATE every time. Three of the refused
# shots have an independent hand mark in datasets/hogmarks, and they say which
# camera is wrong:
#
#   shot          hand mark    side view      err      panel      err
#   e2s8 yellow    8454.967    8454.953   -0.014   8455.920   +0.953
#   e3s7 yellow    9410.767    9410.774   +0.007   9410.496   -0.271
#   e5s2 red      11339.033   11339.006   -0.027  11338.717   -0.316
#
# The side view is right to 0.03 s and the panel is wrong by up to 0.95 s. This
# gate is therefore discarding CORRECT crossings because an unreliable camera
# disagrees -- and the panel's tripwire sits at row 13 of a 516-row panel, at
# the very frame edge, which is where its own docstring says the scale is
# collapsing fastest.
#
# NO LONGER A VETO, as of the sync pass. The ~0.9 s cluster had a cause and it
# was the compositor: eight of the nine cached recordings have a camera pair
# out of step. A threshold cannot tell a mispairing from a clock offset, and on
# this evidence almost all of what it was rejecting was the latter.
#
# So `long_split` records the disagreement instead of obeying it, and this
# constant is what `sync_report` calls "in sync" rather than what ships. The
# discrepancy is being addressed on the camera side; until it is, a split
# carries the panel's disagreement in `panel_delta` so a reader can see it.
CROSS_CHECK_S = 0.25


@dataclass(frozen=True)
class Split:
    """One stone timed over ``baseline_m`` of sheet, hog line to hog line."""

    seconds: float
    baseline_m: float
    t_start: float       # leading edge on the throwing end's hog line (side view)
    t_end: float         # leading edge on the destination hog line (its panel)
    # 0.0 when the far crossing was observed. Positive when it was reached for,
    # in the panel's own y units, so a reader can tell an exact split from one
    # that cannot be checked against anything. See FAR_REACH_MAX_U.
    far_reach: float = 0.0
    # How far the throwing panel's own crossing was from the side view's
    # answer, or None when the panel had no reading. Kept rather than acted on:
    # while the composite's sources are out of step this measures the desync,
    # not the detector, and `scripts/ds13/sync_report.py` reads it that way.
    panel_delta: float | None = None

    @property
    def speed_m_s(self) -> float:
        """Mean speed over the baseline -- the number that reads as ice speed."""
        return self.baseline_m / self.seconds if self.seconds else 0.0


def crossing_time(track, y_line: float) -> float | None:
    """When a track passed a fixed ``y_line``, or None if it never bracketed it.

    For a line that sits at one y everywhere -- the tee line the thinking-time
    clock uses. Hog lines are not that; they use ``line_crossing``.
    """
    pts = [(float(t), float(y)) for t, _x, y in track]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if (y0 - y_line) * (y1 - y_line) <= 0 and y0 != y1:
            return t0 + (y_line - y0) * (t1 - t0) / (y1 - y0)
    return None


@dataclass(frozen=True)
class FarCrossing:
    """What one shot's two panels say about their hog lines (``game/fartime.py``).

    ``t`` is the destination crossing a split ends on, or None; ``reach`` is 0.0
    when it was observed, otherwise how far it was reached for. The rest are only
    ever checks: the panel speed across each line, for the pairing test, and the
    throwing panel's own crossing, recorded against the side view's.
    """

    t: float | None
    reach: float = 0.0
    v_far: float | None = None
    t_near_panel: float | None = None
    v_near: float | None = None


def line_crossing(track, line, *, departing: bool = False) -> float | None:
    """When a track's centre passed a panel's hog line, or None if it never did.

    ``line`` is a ``geometry.hogpaint.HogLine``, read at each sample's own
    lateral position: the paint bows in these lenses, and a curled stone
    crosses it well off the centre line.

    ``departing=True`` is the throwing panel's line, crossed from the house
    side: a departing stone's leading edge meets the paint's inside edge
    first, not the outer edge an arrival meets, so its tripwire is
    ``line.departure_y_at`` rather than ``line.y_at``.
    """
    tripwire = line.departure_y_at if departing else line.y_at
    pts = [(float(t), float(x), float(y)) for t, x, y in track or ()]
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        d0, d1 = y0 - tripwire(x0), y1 - tripwire(x1)
        if d0 * d1 <= 0 and d0 != d1:
            return t0 + d0 / (d0 - d1) * (t1 - t0)
    return None


# How far past the start of an arrival's track the destination crossing may be
# reached for, in the panel's own y units -- about two feet of ice at that scale.
#
# Measured against hand marks (`datasets/hogmarks`) on the eleven marked rocks
# the destination panel first sees already past its painted line, reaching
# back with the four-point linear fit below:
#
#     cap     n   median   worst   within 0.10 s
#     0.15    4   0.069    0.154      3/4
#     0.20    8   0.057    0.154      6/8
#     0.25   10   0.072    0.278      7/10
#
# 0.20 is where the first bad case would enter. A quadratic on eight points did
# worse against the marks (median 0.084, worst 0.163 at 0.20); it only looked
# better scored against the panel's own crossing, which is itself noisy at the
# frame edge. Eleven cases is a small sample -- see the spec's known risks.
FAR_REACH_MAX_U = 0.20

# How long before the track's first sample a reached-for crossing may be put,
# in seconds. The reach cap bounds how far back in y; this bounds how far back
# in time, which is what goes wrong when the fit's slope is nearly flat.
#
# Hand-marked reach-backs led their first sighting by at most 0.57 s. On three
# replayed games the reach-back leads formed a continuum up to 0.81 s, then
# outliers at 1.11, 1.18, 1.95, 7.48 and 39 s -- every one a track that dwelt
# or jittered near one y, so dt/dy blew up. 1.0 sits in that gap.
FAR_LEAD_MAX_S = 1.0

# How far AFTER the first sample a fit may land and still be kept, in seconds:
# one frame at the 10 fps the panels are read at. A stone first seen a few
# thousandths of a unit short of the tripwire is on the line, and a four-point
# fit through its noise can put the crossing a few hundredths late. On the
# three replayed games five such stones sat at -0.01 to -0.03 s; refusing them
# lost good splits. The crossing is then the first sighting itself -- a stone
# seen past the line cannot have crossed it later. A wrong-sign slope lands
# well over a frame late and is still refused.
FAR_LEAD_SLACK_S = 0.1

# Points used for the fit. Four is what a truncated track reliably has near the
# line, and more made the tail worse rather than better -- a quadratic over ten
# reached a 1.65 s worst case against this fit's 0.65 s.
_FAR_FIT_POINTS = 4


def far_crossing(track, line, *, max_reach: float):
    """``(t, reach)`` for an arrival crossing ``line``, reaching back a little.

    ``reach`` is 0.0 when the crossing was observed. Otherwise the track began
    past the line, and the time is extrapolated back to it with a straight line
    through the track's first four samples in time, with ``reach`` -- how far
    the first sample was past the line, read at its own lateral position --
    saying how far, so the split is marked.

    ``(None, 0.0)`` when the line is further back than ``max_reach``, when there
    is too little track to fit, or when the fit puts the crossing anything but
    a little before the first sample: not more than a frame after it
    (``FAR_LEAD_SLACK_S``; beyond that the slope has the wrong sign), and not
    more than ``FAR_LEAD_MAX_S`` before it (a stone that dwelt or jittered,
    whose nearly flat fit reaches back seconds). A fit inside the slack is
    timed at the first sample.
    """
    seen = line_crossing(track, line)
    if seen is not None:
        return seen, 0.0
    pts = sorted(((float(t), float(x), float(y)) for t, x, y in track or ()),
                 key=lambda p: p[0])
    if len(pts) < _FAR_FIT_POINTS:
        return None, 0.0
    t_first, x_first, y_first = pts[0]
    y_line = line.y_at(x_first)
    reach = y_line - y_first
    if not 0.0 < reach <= max_reach:
        return None, 0.0
    early = pts[:_FAR_FIT_POINTS]
    n = len(early)
    my = sum(p[2] for p in early) / n
    mt = sum(p[0] for p in early) / n
    den = sum((p[2] - my) ** 2 for p in early)
    if den == 0:
        return None, 0.0
    slope = sum((p[2] - my) * (p[0] - mt) for p in early) / den    # dt/dy
    t_line = mt + (y_line - my) * slope
    lead = t_first - t_line
    if not -FAR_LEAD_SLACK_S < lead <= FAR_LEAD_MAX_S:
        return None, 0.0
    return min(t_line, t_first), reach


def speed_at_line(track, line, *, departing: bool = False) -> float | None:
    """How fast a track was crossing ``line``, in the panel's own units per second.

    Not metres per second, deliberately: both hog lines are read in their own
    panel's units, so two speeds measured there are distorted alike and can be
    compared without converting either.

    ``departing=True`` is the throwing panel's line, crossed from the house
    side -- see ``line_crossing``.
    """
    tripwire = line.departure_y_at if departing else line.y_at
    pts = [(float(t), float(x), float(y)) for t, x, y in track or ()]
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        d0, d1 = y0 - tripwire(x0), y1 - tripwire(x1)
        if d0 * d1 <= 0 and d0 != d1 and 0 < t1 - t0 <= SPEED_SPAN_MAX_S:
            return abs(y1 - y0) / (t1 - t0)
    return None


def long_split(delivery, *, t_hog, v_hog, far) -> Split | None:
    """The split for one shot, or None when either end could not be timed.

    ``t_hog`` and ``v_hog`` are the throwing end, from the side view
    (``game/hogtime.py``); ``far`` is the ``FarCrossing`` that
    ``game/fartime.py`` attached to the shot. All three are keyword-only and
    REQUIRED -- pass None for "not measured". A caller that forgets one gets a
    TypeError instead of a missing split: the long-camera merge added a
    required input and updated one of this function's two callers, and the
    other silently lost every draw-through classification until 366ba20.
    """
    if delivery is None or t_hog is None or far is None or far.t is None:
        return None
    start, end = t_hog, far.t
    if end <= start:
        return None
    # The side view is the throwing end's timing source, so the throwing
    # panel's own crossing is RECORDED against it, never obeyed: most
    # recordings have a camera pair out of step, so the panel is a clock that
    # disagrees, not a second opinion about the same instant.
    panel_delta = None if far.t_near_panel is None else t_hog - far.t_near_panel
    # A stone only ever slows, so it cannot cross the far hog line faster than
    # it crossed the near one; if it appears to, the two were not the same
    # stone. Both speeds are in their own panel's units, distorted alike.
    if far.v_near and far.v_far and far.v_far > far.v_near * SPEED_TOLERANCE:
        return None
    if v_hog and (far.v_near is None or far.v_far is None):
        # The panel check above could not run -- no throwing-panel speed, or a
        # reached-for crossing with no far speed -- so bound the mean speed over
        # the baseline by the side view's speed at the near line, both in real
        # metres. A stone only slows, so this holds for any split.
        mean = BASELINE_M / (end - start)
        if mean > v_hog * SPEED_TOLERANCE:
            return None
    return Split(seconds=end - start, baseline_m=BASELINE_M, t_start=start,
                 t_end=end, far_reach=far.reach, panel_delta=panel_delta)
