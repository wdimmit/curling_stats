"""Time a delivery across the throwing end's hog line, in a side view.

The overhead panel loses the throw before the hog line on about 40% of
deliveries -- it looks straight down at a stone with the thrower and sweepers
standing over it. The camera at the far end is 22 m away and level with the
ice, so the sweepers are beside the stone rather than on top of it, and the
line it crosses is painted and fixed.

Most of this module is refusal. The same view shows boots, broom pads and the
stones already in play, and a mistimed split is worse than a missing one, so a
crossing has to survive every gate below or it does not exist.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Measured by hand on 27 deliveries (``datasets/hogmarks``): a stone crosses
# its hog line between 2.83 s and 5.43 s after the panel first sees it leave
# the hack. The window is wider at both ends than every mark observed.
#
# "First sees it leave the hack" was measured under the old entry gate, which
# pinned a release's first sighting within 0.6 m of the panel's back edge
# (``release.py``, throw-stage-1). That gate is gone: a release's ``t`` can
# now read up to ~1.1 m, ~0.55 s at 2 m/s, later than it used to for the same
# physical release. This window was NOT re-derived against the new anchor.
# The hog crossing itself is unaffected -- it is timed off paint, not off
# ``t`` -- so a later anchor only shrinks the gap between the two, putting
# the LOWER bound (2.0 s) at risk first; the upper bound has margin to spare.
#
# And it did: of 35 hosted throws released at 3 m/s or more, 3 crossed
# 1.77-1.86 s after their release, before a 2.0 s window opened. Nothing can
# reach the hog line, 6.4 m from the tee, in a second, so the window opens
# there; a second more of search for every throw changed nothing on the
# others (see SPEED_BOUNDS_M_S).
WINDOW_S = (1.0, 6.5)

# How wide a stone reads at the hog line, and how far from that a candidate may
# be before it is refused.
#
# UNMEASURED, both of them, and they are load-bearing: every width gate and
# every proposed label box rests on the 52. It comes from a single check
# against the reference VOD's side views during Task 4 of the long-camera plan
# -- the geometry predicted 53.6 px from a camera 40.233 m out and about 52 was
# what a stone actually spanned -- not from a distribution over sheets. The
# bounds were then set wide enough to keep every crossing that pass found.
#
# What would measure them is NOT the widths this module already records, and an
# earlier version of this comment said it was. Crossing.width_px is a median
# over a track every sample of which came through the gate below, which refuses
# any body outside WIDTH_BOUNDS * expect_px -- 26.0 to 83.2 px. The sample is
# censored by the very bounds it would be offered as evidence for, so collecting
# it can only ever confirm them. It is also a median over the whole 2.0-6.5 s
# window rather than the width AT the line, which is what
# STONE_WIDTH_AT_HOG_PX names.
#
# Measuring the centre and measuring the bounds are different jobs. The centre
# could come from the retained population, which width_px samples honestly. The
# bounds need an UNCENSORED distribution: a measuring pass with the width gate
# widened or bypassed, recording what is there before anything is refused --
# and scripts/split_coverage.py would have to be taught to record widths at
# all, since VideoStats carries no such field today.
WIDTH_BOUNDS = (0.5, 1.6)
STONE_WIDTH_AT_HOG_PX = 52.0
# A delivery crosses its hog line no slower than 1.2 m/s -- slower is a stone
# being nudged aside, or a tracker that has drifted onto one already at rest.
# The 27 hand-marked crossings, nearly all draws, put the top at 3.2 m/s, but
# a big-weight hit goes faster: 10/01 Mens sheet 2's flagged rock 16 was
# followed across the line and refused at 3.69 m/s, and 6 of 35 hosted
# throws released at 3 m/s or more were refused at 3.20-3.67 m/s. Raising the
# top only admits crossings once refused for speed; a tracker jump that reads
# faster still is refused, and the split checks both camera speeds again.
SPEED_BOUNDS_M_S = (1.2, 5.0)
# How much darker than the ice granite is, in luminance levels.
#
# The painted hog line dips about 25 levels below the ice (238 -> ~212), so
# this sits deliberately ABOVE that: a threshold below 25 would pick the paint
# up as the stone's own trailing edge, which is the one confusion it exists to
# prevent. (This comment used to say "well below it", which is backwards
# against its own value and against tests/synth.py:69-76, where the same
# reasoning is stated correctly.)
#
# The 45 itself is a reasoned estimate, not a measurement: it is comfortably
# clear of the paint's 25 and comfortably under granite's own contrast against
# ice. Measuring it would mean sampling the luminance gap between stone and ice
# across the five sheets' side views, the way _GREEN_THRESHOLD in
# geometry/sideview.py was measured against real plates.
BODY_DARKER_THAN_ICE = 45
_MIN_SAMPLES = 6

# The detector's own trailing edge is the stone's lowest dark row, which
# includes its contact shadow -- a little past where a person judged the
# leading edge to touch the paint's near side, so the raw crossing reads
# early. Measured as the median error across the 22 of 27 hand-marked
# crossings (``datasets/hogmarks``) this detector could resolve at all:
# -0.073 s. The other 5 are not scatter around that median -- they are a
# different failure (see tests/test_longview.py::TestAgainstHandMarkedCrossings
# and task-5-report.md) that this constant is not meant to paper over.
OFFSET_S = 0.073


# Stable, closed set of kinds ``Crossing.key`` takes. ``reason`` stays free
# prose for a person (it carries a speed or a candidate count); a caller that
# wants to bucket many refusals -- a coverage report -- should count on
# ``key`` instead, since two different speeds or candidate counts are the
# same *kind* of refusal and prose fragments a histogram into one row each.
KEY_OK = "ok"
KEY_NO_FRAMES = "no_frames"
KEY_NO_CANDIDATE = "no_candidate"
KEY_AMBIGUOUS = "ambiguous"
KEY_NEVER_REACHED = "never_reached"
KEY_UNSTEADY = "unsteady"
KEY_BAD_SPEED = "bad_speed"

KEYS = frozenset({
    KEY_OK, KEY_NO_FRAMES, KEY_NO_CANDIDATE, KEY_AMBIGUOUS,
    KEY_NEVER_REACHED, KEY_UNSTEADY, KEY_BAD_SPEED,
})


@dataclass(frozen=True)
class Crossing:
    """When a stone crossed the line, or why we will not say.

    ``key`` is ``reason``'s stable classification, drawn from the closed set
    ``KEYS`` -- see the note above it. Defaults to ``""`` (unclassified) so a
    ``Crossing`` built outside this module, such as a test double standing in
    for the detector, is not forced to pick one.
    """

    t: float | None
    reason: str
    key: str = ""
    # The median body width over the track. Nothing reads it today. Kept because
    # it is a cheap median over a list that already exists and is the only width
    # this module reports -- but see WIDTH_BOUNDS' comment above before treating
    # it as evidence for those bounds: it is a sample drawn from inside them.
    width_px: float = 0.0
    # The track's mean speed in REAL metres, from the perspective solve. Unlike
    # width_px this is read: `split.long_split` uses it to bound a hog-to-hog
    # split when there is no release track to check the pairing against. Real
    # metres is what makes it usable there -- the panel's own metres are not
    # trustworthy that far up the sheet, which is why the older check compared
    # two panel speeds rather than a speed against a distance.
    speed_m_s: float | None = None
    # Which key of the tracks dict was timed -- the left one when two
    # neighbours were joined. Read by `game/linetime.py` to find the stone
    # again among all the samples.
    track_key: int | None = None
    # Every accepted detection of the rock's colour in the window, as
    # (t, cx, edge_row, body_px), for the trained proposer only (Task 3).
    samples: tuple = ()

    def __bool__(self) -> bool:
        return self.t is not None


def colour_mask(win, color):
    """Which pixels of ``win`` read as ``color``'s stone handle.

    Public because ``harvest/sidepool.py`` needs the same colour test to tell
    a moment with a coloured blob under no granite (``occluded``) from a
    moment with no colour at all (``clear``); that is another package, so it
    must import this rather than carry a second copy that could drift.
    """
    r, g, b = win[:, :, 0], win[:, :, 1], win[:, :, 2]
    if color == "red":
        return (r - np.maximum(g, b) > 28) & (r > 90)
    return (np.minimum(r, g) - b > 40) & (r > 120) & (g > 110)


def runs(flags, min_len=1):
    """Contiguous ``True`` spans of ``flags`` as ``(start, stop)`` pairs.

    Public because ``harvest/sidepool.py`` needs the same run-finding to spot
    a body standing beside a stone (``crowding``); that is another package, so
    it must import this rather than carry a second copy that could drift.
    """
    out, start = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


@dataclass(frozen=True)
class Proposal:
    """One candidate stone, as a box a person can correct.

    Every edge is measured: ``body_px`` and ``edge_row`` from the granite's
    dark span, ``top_row`` from the colour handle's own topmost row. Nothing
    here assumes how tall a stone is.
    """

    cx: float
    top_row: float
    edge_row: float
    body_px: float


def candidates(win, color, expect_px):
    """Every coloured blob with a granite body of the right width under it."""
    mask = colour_mask(win, color)
    if mask.sum() < 20:
        return []
    grey = win.mean(axis=2)
    ice = np.percentile(grey, 90)
    out = []
    for x0, x1 in runs(mask.sum(axis=0) > 0):
        sub = mask[:, x0:x1]
        if sub.sum() < 20:
            continue
        # Scan from just below the handle, not from it: the handle's own
        # colour patch is itself a few pixels wide and dark enough to pass
        # for a narrow "body" on its own, which is exactly a broom pad with
        # no stone under it -- a bare colour patch, no granite.
        handle_bottom = int(np.max(np.nonzero(sub)[0]))
        handle_top = int(np.min(np.nonzero(sub)[0]))
        cx = (x0 + x1) // 2
        half = int(expect_px * 0.9)
        band = grey[:, max(0, cx - half):cx + half]
        wide = (band < ice - BODY_DARKER_THAN_ICE).sum(axis=1)
        # Contiguous from there only, and only while the dark span looks
        # stone-sized: a full-width feature nearby -- the hog line, or the
        # house's own painted rings, both wider than any stone -- fills the
        # whole band and would otherwise get counted as part of the body just
        # because it lies within the look-ahead window.
        lower, upper = expect_px * 0.45, WIDTH_BOUNDS[1] * expect_px
        rows = []
        for y in range(handle_bottom + 1,
                       min(handle_bottom + 1 + int(expect_px), band.shape[0])):
            if not lower < wide[y] <= upper:
                break
            rows.append(y)
        if not rows:
            continue
        body = float(max(wide[y] for y in rows))
        if not WIDTH_BOUNDS[0] * expect_px <= body <= WIDTH_BOUNDS[1] * expect_px:
            continue
        out.append(Proposal(float(cx), float(handle_top),
                            _sub_row(rows, wide, lower), body))
    return out


def _sub_row(rows, wide, lower):
    """Where the body's trailing edge crosses ``lower``, to the sub-pixel.

    ``rows`` is one discrete pixel row per frame; snapping to it would let a
    delivery's crossing time land exactly on a sampled frame time by nothing
    more than where the stone happened to fall on the pixel grid.
    """
    y = rows[-1]
    nxt = y + 1
    if nxt >= len(wide) or wide[nxt] >= wide[y]:
        return float(y)
    a, b = wide[y], wide[nxt]
    return y + max(0.0, min(1.0, (a - lower) / (a - b)))


def _crossing_index(track, hog_row, max_gap_s=None):
    """Index ``i`` such that ``track[i], track[i + 1]`` straddle ``hog_row``,
    no more than ``max_gap_s`` apart when that is given."""
    for i, (a, b) in enumerate(zip(track, track[1:])):
        r0, r1 = a[1], b[1]
        if (r0 <= hog_row <= r1 and r1 != r0
                and (max_gap_s is None or b[0] - a[0] <= max_gap_s)):
            return i
    return None


def _moving_and_crossed(tracks, view, max_gap_s=None):
    moving = [(k, tr) for k, tr in tracks.items()
              if len(tr) >= _MIN_SAMPLES and tr[-1][1] > tr[0][1]]
    crossed = [(k, tr) for k, tr in moving if tr[0][1] <= view.hog_row <= tr[-1][1]
               and _crossing_index(tr, view.hog_row, max_gap_s) is not None]
    return moving, crossed


def crossing_from_tracks(tracks, view, offset_s: float = None,
                         max_gap_s: float = None) -> Crossing:
    """Turn ``{key: [(t, edge_row, body_px, ...), ...]}`` into a verdict.

    Entries carry 3 or more elements and are read by index (``[0]``, ``[1]``,
    ``[2]``); a trained proposer's fourth element, the column, is never read
    here.

    Split out of :func:`find_in_frames` so a different *proposer* can be
    measured against the same gates. Everything here -- what counts as moving,
    what makes two crossers ambiguous, steadiness, the speed bound, and the
    interpolation -- is the judgement about deliveries; only the step that says
    "there is a stone at this row in this frame" differs between the colour
    scan and a trained detector. Comparing the two is only meaningful if this
    part is shared rather than reimplemented alongside it.

    ``offset_s`` is the one thing that must NOT be shared. It corrects where a
    proposer puts the stone's trailing edge against where a person judges the
    leading edge to touch the paint, and that is a property of the proposer,
    not of the gates. ``OFFSET_S`` is the colour scan's value; a trained
    detector drawing the granite's own edge has a different one, and inheriting
    this one made it read 0.073 s late on every crossing.

    ``max_gap_s`` is the same kind of thing: how far apart in time the samples
    either side of the line may be and still count as one crossing. It says
    how often the proposer sees the stone, not what a delivery is. None, the
    colour scan's, bounds nothing: that proposer loses the stone at the paint
    and under the sweepers as a matter of course, and its interpolation across
    those gaps is what the hand marks were matched against. A trained detector
    sees the granite nearly every frame, so a long gap in its track is two
    sightings, possibly of two objects, not a crossing -- see
    ``sidemodel.STRADDLE_GAP_MAX_S``.
    """
    moving, crossed = _moving_and_crossed(tracks, view, max_gap_s)
    if not crossed:
        # A stone's column can cross one of the 120-px keys' boundaries right
        # at the hog row -- AEqL game 2, end 3, rock 3 did, at column 480 --
        # and then neither half of its track straddles the line. Only when
        # nothing crossed, join each key to its neighbour and ask again; a
        # join that finds exactly one crosser is that stone.
        joined = {k: sorted(tracks[k] + tracks[k + 1]) for k in tracks if k + 1 in tracks}
        m2, c2 = _moving_and_crossed(joined, view, max_gap_s)
        if len(c2) == 1:
            moving, crossed = m2, c2
    if not moving:
        return Crossing(None, "no candidate that could be a stone in flight", KEY_NO_CANDIDATE)
    if len(crossed) > 1:
        # Step 3 of this task's brief suggested picking the crosser whose
        # body width best matches STONE_WIDTH_AT_HOG_PX and refusing only
        # when two are within 15% of each other. Tried against the three
        # hand-marked windows that actually produce two crossers: it does
        # pick the better-matching candidate, but "better of two bad
        # options" is not the same as "a stone" -- on one of the three, the
        # winner still missed by 0.90 s, far worse than refusing. Two
        # candidates this evenly matched by width are not reliably
        # distinguishable this way, so this refuses unconditionally rather
        # than publish a guess with no safety margin behind it.
        return Crossing(None, f"two candidates crossed the line ({len(crossed)})", KEY_AMBIGUOUS)
    if not crossed:
        return Crossing(None, "the stone never reached the line", KEY_NEVER_REACHED)
    key, track = crossed[0]

    idx = _crossing_index(track, view.hog_row, max_gap_s)
    if idx is None:
        return Crossing(None, "the stone never reached the line", KEY_NEVER_REACHED)
    # Steadiness only has to hold up to the crossing itself, and only in the
    # samples immediately around it: what the same x-bin was doing several
    # seconds earlier (often still noisy while the handle first resolves out
    # of the thrower's hand) or afterwards (arriving at the house, passing
    # stones already there) has no bearing on when it passed the hog line,
    # and checking the whole track discarded good crossings over a blip
    # nowhere near the row being measured.
    lo = max(0, idx - (_MIN_SAMPLES - 2))
    local = track[lo:idx + 2]
    if any(q[1] - p[1] < -1.0 for p, q in zip(local, local[1:])):
        return Crossing(None, "the candidate did not travel steadily", KEY_UNSTEADY, track_key=key)
    span = track[-1][0] - track[0][0]
    speed = None
    if span > 0:
        metres = abs(view.metres_at(track[-1][1]) - view.metres_at(track[0][1]))
        speed = metres / span
        if not SPEED_BOUNDS_M_S[0] <= speed <= SPEED_BOUNDS_M_S[1]:
            return Crossing(None, f"speed {speed:.2f} m/s is not a delivery", KEY_BAD_SPEED, track_key=key)
    (t0, r0), (t1, r1) = track[idx][:2], track[idx + 1][:2]
    frac = (view.hog_row - r0) / (r1 - r0)
    t = t0 + frac * (t1 - t0) + (OFFSET_S if offset_s is None else offset_s)
    return Crossing(t, "ok", KEY_OK,
                    width_px=float(np.median([p[2] for p in track])),
                    speed_m_s=speed, track_key=key)


def find_in_frames(frames, view, color, times) -> Crossing:
    """Time the crossing of ``view.hog_row`` in already-decoded frames."""
    x, y, _w, _h = view.rect
    tracks: dict[int, list] = {}
    for frame, t in zip(frames, times):
        win = np.asarray(frame, dtype=np.float32)[y:, x:]
        for p in candidates(win, color, STONE_WIDTH_AT_HOG_PX):
            key = int(p.cx // 120)        # a stone never moves 120 px sideways
            tracks.setdefault(key, []).append((t, p.edge_row, p.body_px))
    return crossing_from_tracks(tracks, view)


# Decoder threads for one window. ffmpeg's own choice is one per core, which
# on the GPU worker made each short window slower (484 ms against 402 at four)
# and six at once slower still (2.4 windows a second against 2.8): three
# workers read two windows ahead each, on 32 threads. Decoding is exact at
# any count.
FFMPEG_THREADS = 4

# A recording is entered this many whole seconds before the keyframe it needs
# (`_lead`), for an input seek that lands a little past where it was sent.
KEY_MARGIN_S = 1
# How far a keyframe listing reaches past what was asked: one listing covers
# most of an end's windows, which are asked for in order.
KEY_LIST_AHEAD_S = 600.0
# Listings kept per file, newest last; a live lane runs for hours.
KEY_LISTS_KEPT = 32
# Longest gap between keyframes the listing must span to know the one before a
# window; the club's streams put them at most 5 s apart.
KEY_GAP_MAX_S = 6.0

# The bytes a recording is known by: its first packets, which carry its own
# timestamps, and which a recording that grows never changes.
KEY_HEAD_BYTES = 4096

_keyframes = {}     # str(path) -> (head, [(from_s, to_s, sorted keyframe times)])


def _head(video) -> bytes:
    try:
        with open(video, "rb") as f:
            return f.read(KEY_HEAD_BYTES)
    except OSError:
        return b""


def _keyframe_before(video, t0: float):
    """The last keyframe at or before ``t0`` in a recording, from a listing
    kept per recording (`frames.keyframe_times`), or None where none is known.

    Kept by the recording's first bytes as well as its path: a live lane's
    recording is deleted when its stream ends, and the next stream on that
    sheet records to the same path on another clock. Its inode is no help --
    the filesystem hands a freed one straight back out."""
    import bisect

    from curling_score.ingest.frames import keyframe_times

    head = _head(video)
    known = _keyframes.get(str(video))
    if known is None or known[0] != head:
        known = _keyframes[str(video)] = (head, [])
    spans = known[1]
    span = next((s for s in spans if s[0] <= t0 - KEY_GAP_MAX_S and t0 <= s[1]), None)
    if span is None:
        lo = t0 - KEY_GAP_MAX_S - 10.0
        times, read_to = keyframe_times(video, lo, t0 + KEY_LIST_AHEAD_S)
        span = (lo, read_to, times)
        spans.append(span)
        del spans[:-KEY_LISTS_KEPT]
    i = bisect.bisect_right(span[2], t0 + 1e-6)
    return span[2][i - 1] if i else None


def _lead(video, t0: float) -> float:
    """Seconds to enter ``video`` before ``t0``: none for an indexed MP4.

    A live recording is MPEG-TS, which has no index: the input seek can land
    past the keyframe before ``t0``, and the decoder then drops everything up
    to the next one -- measured on a live recording, a 4 s window came back
    starting 2 s late with 62 of its 122 frames. Entering a flat 10 s early
    (`frames.TS_SEEK_LEAD_S`) always reaches it, at the cost of decoding those
    10 s for every window; a live end asks for ~90 windows. So the recording's
    keyframes are listed and it is entered just before the one it needs.

    The lead stays whole seconds. The ``fps`` filter takes its grid from the
    first frame it is given, and only a whole-second shift moves that frame by
    a whole number of grid steps at every rate the passes read at (30, 10, 5,
    4 fps), so the frames picked are the ones the 10 s lead picked.
    """
    from curling_score.ingest.frames import seek_lead

    lead = seek_lead(video)
    if not lead:
        return 0.0
    try:
        k = _keyframe_before(video, t0)
    except Exception:  # noqa: BLE001 - a listing only saves time; the flat lead always works
        log.info("keyframes of %s around %.1f s not listed; entering %.0f s early",
                 video, t0, lead, exc_info=True)
        return lead
    if k is None:
        return lead
    return float(min(lead, math.ceil(t0 - k) + KEY_MARGIN_S))


def _seek(video, t0: float, lead: float | None = None) -> list:
    """ffmpeg arguments that open ``video`` at exactly ``t0``.

    A finished MP4 is indexed, and an input seek lands on the keyframe before
    ``t0`` and decodes forward to it. A recording is entered ``lead`` seconds
    early (`_lead`) and trimmed exactly on the output side, which gives the
    same frames the MP4 does.
    """
    if lead is None:
        lead = _lead(video, t0)
    if not lead:
        return ["-ss", f"{t0}", "-i", str(video)]
    start = max(0.0, t0 - lead)
    return ["-ss", f"{start}", "-i", str(video), "-ss", f"{t0 - start}"]


def _command(video, rect, t0, t1, fps, lead=None) -> list:
    x, y, w, h = rect
    # crop's dimensions are otherwise rounded down to the input's chroma
    # subsampling (yuv420p halves both axes), silently handing back an even
    # width or height one px short of what was asked for. Every side view's
    # rect comes from wherever the overhead strip's edge lands, which is odd
    # as often as even, so this bites about half of them: real footage did,
    # at rect width 813, and the reshape below failed on the size mismatch.
    # exact=1 crops to the exact pixel count regardless of chroma alignment.
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", str(FFMPEG_THREADS),
            *_seek(video, t0, lead),
            "-t", f"{t1 - t0 + 0.05}",
            "-vf", f"crop={w}:{h}:{x}:{y}:exact=1,fps={fps}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


def _popen(command):
    """ffmpeg with its output on a 1 MB pipe -- or the default pipe, where the
    kernel will not grow one: past a user's pipe-buffer allowance it refuses
    anyone without CAP_SYS_RESOURCE, and every container's root is one user."""
    kw = dict(stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    try:
        return subprocess.Popen(command, pipesize=1 << 20, **kw)
    except PermissionError:
        return subprocess.Popen(command, **kw)


def _run(command, rect, t0, fps, t1=None):
    """Run a decode command and frame its output. Only ever a subprocess, so it
    may run on any thread: PyAV is never touched here.

    The frames are read straight into one array sized for the window, through
    a 1 MB pipe. ``subprocess.run`` read them 32 KB at a time in a Python loop
    and joined the pieces -- up to ~430 MB a 30 fps window, holding the GIL
    the main thread wants -- 713 ms against 504 for a 5.5 s window here."""
    _x, _y, w, h = rect
    frame = w * h * 3
    span = 0.0 if t1 is None else max(0.0, t1 - t0)
    room = (math.ceil((span + 0.05) * fps) + 2) * frame
    buf = np.empty(room, dtype=np.uint8)
    view, got, more = memoryview(buf), 0, []
    with _popen(command) as proc:
        while True:
            if got < room:
                n = proc.stdout.readinto(view[got:])
                if not n:
                    break
                got += n
            else:
                chunk = proc.stdout.read(1 << 20)
                if not chunk:
                    break
                more.append(chunk)
    data = buf[:got]
    if more:
        data = np.concatenate([data, np.frombuffer(b"".join(more), dtype=np.uint8)])
    if len(data) % frame:
        raise ValueError(f"ffmpeg gave {len(data)} bytes, not whole {w}x{h} frames")
    frames = data.reshape(-1, h, w, 3)
    return frames, [t0 + i / fps for i in range(frames.shape[0])]


def _short(frames, t0, t1, fps) -> bool:
    """Fewer frames than the window holds by more than a rounding: a recording
    entered past the keyframe it needed, or a window running off its end."""
    return len(frames) < math.floor((t1 - t0) * fps) - 1


def _decode_now(video, rect, t0, t1, fps, lead):
    """One window decoded here and now; a recording entered on its listed
    keyframe that comes back short is read again from the flat lead, so a
    listing that is wrong costs time, never frames."""
    from curling_score.ingest.frames import seek_lead

    if t1 < t0:
        # Nothing to read: ffmpeg refuses a negative duration and writes no frames.
        _x, _y, w, h = rect
        return np.empty((0, h, w, 3), dtype=np.uint8), []
    frames, times = _run(_command(video, rect, t0, t1, fps, lead), rect, t0, fps, t1)
    flat = seek_lead(video)
    if lead and lead < flat and _short(frames, t0, t1, fps):
        frames, times = _run(_command(video, rect, t0, t1, fps, flat), rect, t0, fps, t1)
    return frames, times


# Windows decoded ahead of the one a pass is reading, at once. One ffmpeg on
# a short window leaves most of the CPU idle -- its start, its seek and its
# keyframe are serial -- and on the GPU worker the passes still waited ~18 s
# an end on one at a time.
AHEAD = 2


class _ReadAhead:
    """Windows a pass will want soon, decoding on a thread of their own.

    `soon` starts one; `decode` with exactly the same arguments collects it.
    The command, keyframe listing and all, is made on the caller's thread;
    the thread only waits on ffmpeg, a process of its own.
    """

    # Windows decoded ahead and not yet collected; the oldest beyond this is
    # dropped. A window is up to ~1 GB of full-view frames (a 12 s search at
    # 30 fps), though most are 150-400 MB.
    MAX_WAITING = AHEAD + 1

    def __init__(self):
        from collections import OrderedDict
        from concurrent.futures import ThreadPoolExecutor

        self.pool = ThreadPoolExecutor(max_workers=AHEAD, thread_name_prefix="longview")
        self.waiting = OrderedDict()
        # What it bought: windows collected ready-decoded, the time spent
        # waiting for them, and the windows no plan foresaw, decoded then.
        self.ahead, self.waited_s, self.then, self.then_s = 0, 0.0, 0, 0.0

    def soon(self, key):
        if key in self.waiting:
            return
        video, rect, t0, t1, fps = key
        lead = _lead(video, t0)
        self.waiting[key] = self.pool.submit(_decode_now, video, rect, t0, t1, fps, lead)
        while len(self.waiting) > self.MAX_WAITING:
            _key, dropped = self.waiting.popitem(last=False)
            dropped.cancel()

    def take(self, key):
        got = self.waiting.pop(key, None)
        if got is None:
            return None
        tic = time.monotonic()
        try:
            return got.result()
        finally:
            self.ahead += 1
            self.waited_s += time.monotonic() - tic

    def decoded_then(self, seconds):
        self.then += 1
        self.then_s += seconds

    def summary(self) -> str:
        return (f"{self.ahead + self.then} windows, {self.ahead} read ahead "
                f"(waited {self.waited_s:.1f} s), {self.then} decoded then ({self.then_s:.1f} s)")

    def close(self):
        for got in self.waiting.values():
            got.cancel()
        self.waiting.clear()
        self.pool.shutdown(wait=True)


_reading = None     # the read-ahead of the passes running now, if any


@contextmanager
def reading_ahead():
    """While open, windows announced with `soon` decode in the background and
    `decode` collects them. Opened around an end's side-view passes, which
    otherwise left the GPU idle through every one of their ~90 decodes."""
    global _reading
    before, _reading = _reading, _ReadAhead()
    try:
        yield _reading
    finally:
        ahead, _reading = _reading, before
        ahead.close()


def to_bgr(rgb):
    """A decoded window's RGB pixels as the BGR the side models read: the
    same bytes as ``np.ascontiguousarray(rgb[:, :, ::-1])``, but OpenCV's
    channel swap rather than numpy's strided copy -- 0.63 ms against 1.94 a
    600 x 810 crop, about nine thousand crops an end."""
    import cv2

    if rgb.size == 0:
        return np.ascontiguousarray(rgb[:, :, ::-1])
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _key(video, rect, t0, t1, fps):
    return (str(video), tuple(rect), float(t0), float(t1), float(fps))


def soon(video, view, t0: float, t1: float, fps: float = 30.0) -> None:
    """Say this window of ``view`` (a side view, or its rect) will be decoded
    soon. Exactly the arguments `decode` will be given, or it is simply
    decoded then; a no-op with no read-ahead open."""
    if _reading is not None:
        _reading.soon(_key(video, getattr(view, "rect", view), t0, t1, fps))


class Plan:
    """A pass's windows in the order it will decode them, the next `AHEAD` of
    them started as each is taken: ``decode`` for a pass that takes one.

    Planned from what the pass reads its windows from, before it starts. A
    window the pass asks for that is not in the plan is decoded then; one in
    the plan it never asks for costs a decode and nothing else.
    """

    def __init__(self, video, windows):
        self.video = video
        self.keys = [_key(video, rect, t0, t1, fps) for rect, t0, t1, fps in windows]
        self.next = 0
        self.started = set()

    def decode(self, video, rect, t0, t1, fps=30.0):
        key = _key(video, rect, t0, t1, fps)
        if key in self.keys[self.next:]:
            self.next = self.keys.index(key, self.next) + 1
        for i in range(self.next, min(self.next + AHEAD, len(self.keys))):
            if i not in self.started:
                self.started.add(i)
                if _reading is not None:
                    _reading.soon(self.keys[i])
        return decode(video, rect, t0, t1, fps)


_keeping = None     # the lane keeper offered every decode now, if any


@contextmanager
def keeping(keeper):
    """While open, every window `decode` returns is offered to ``keeper``
    (`lanekeep.LaneKeeper`), which keeps a strip of the lane from it. Opened
    around an end's hog and delivery reads so the release pass finds the
    thrower's hands without decoding the camera again."""
    global _keeping
    before, _keeping = _keeping, keeper
    try:
        yield keeper
    finally:
        _keeping = before


def decode(video, rect, t0: float, t1: float, fps: float = 30.0):
    """Frames of one window, cropped to the side view. About 0.25 s a call."""
    got = _decode_window(video, rect, t0, t1, fps)
    if _keeping is not None and len(got[0]):
        _keeping.offer(rect, got[0], got[1], fps)
    return got


def _decode_window(video, rect, t0, t1, fps):
    if _reading is None:
        return _decode_now(video, rect, t0, t1, fps, _lead(video, t0))
    got = _reading.take(_key(video, rect, t0, t1, fps))
    if got is not None:
        return got
    tic = time.monotonic()
    got = _decode_now(video, rect, t0, t1, fps, _lead(video, t0))
    _reading.decoded_then(time.monotonic() - tic)
    return got


def find_crossing(video, view, color: str, t0: float, t1: float,
                  fps: float = 30.0) -> Crossing:
    """Seek, decode and time one crossing."""
    frames, times = decode(video, view.rect, t0, t1, fps)
    if not len(frames):
        return Crossing(None, "no frames decoded", KEY_NO_FRAMES)
    shifted = dataclasses.replace(view, rect=(0, 0, view.rect[2], view.rect[3]))
    return find_in_frames(frames, shifted, color, times)
