"""Turn a cached video into the windows worth searching for a hog crossing.

The pipeline already knows when a delivery happened -- the shot list built by
``game/shots.py`` carries a release (when the throw was seen leaving the far
panel) or, failing that, a rest time (when the delivered stone settled). This
turns that knowledge into the spans ``game/hogtime.py`` would itself search,
in the camera that watches the throwing end, so a frame extracted from a
window is one where a delivery is known to be in flight, in the right camera,
with the colour known -- unlike the 2,864 frames a blind scan produced, all
112 reviewed of which were unusable.

``windows_for_end`` is pure (no media, no cache) and does exactly what
``hogtime.time_hog_crossings`` does to pick a span, so a caller downstream
never searches a differently-shaped window than production would.
``windows_for_video`` is the impure half: it repeats the cache walk
``scripts/split_coverage.py`` already does (calibrate, build the strip proxy,
segment games and ends, detect each end's deliveries and pair its releases)
to get a shot list per end, then asks ``windows_for_end`` for each end's
windows. That walk is duplicated rather than imported -- ``split_coverage.py``
is a script, not a package, and ``analyze.analyze`` runs the whole pipeline
including stages this does not need (scoreboard, longview itself). See the
task report for the refactor this invites: three near-identical copies of the
per-end walk now exist (here, ``analyze.analyze``, ``scripts/split_coverage.py``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from curling_score import analyze as analyze_mod
from curling_score.detect import delivery as D, longview, release as R, sequence
from curling_score.game import fit, hogtime, profile, secondpass, segment, shots as shots_mod
from curling_score.geometry import layout
from curling_score.ingest import frames as F, proxy


@dataclass(frozen=True)
class Window:
    """One span of video worth searching for a delivery's throwing-end crossing.

    ``camera``/``color`` say where and what colour to look for; ``t0``/``t1``
    bound the search exactly as ``hogtime.time_hog_crossings`` would compute
    it for the same shot. ``t_release`` and ``t_rest`` are carried through for
    provenance -- which anchor the window came from, and what it was -- not
    read back to rederive anything.
    """

    video_id: str
    camera: str
    color: str
    t0: float
    t1: float
    end_number: int
    shot_number: int
    t_release: float | None
    t_rest: float | None


def _release_t(release):
    """A release's timestamp, however the object names it.

    Production ``Release`` objects (``detect/release.py``) carry it as ``.t``.
    Nothing in this codebase otherwise uses ``.t_s`` for it, but a caller
    building a lightweight stand-in might, so both are read here rather than
    forcing one convention silently past the other.
    """
    t = getattr(release, "t", None)
    return t if t is not None else getattr(release, "t_s", None)


def windows_for_end(shots, house) -> list[Window]:
    """The delivery windows for one end's shots.

    ``house`` is the key ``hogtime.CAMERA_FOR`` expects directly: the house
    the shots were thrown *from*. For an end played into ``end.house``, that
    is ``analyze.OTHER_HOUSE[end.house]`` -- callers building the cache walk
    (``windows_for_video``) apply that lookup once, outside this function, so
    the camera choice cannot drift between callers.

    For each shot: a release gives the window straight from
    ``longview.WINDOW_S``, same as production. No release but a rest time
    falls back to ``hogtime.ARRIVAL_LOOKBACK_S`` before that rest -- this
    reads ``t_rest_s`` rather than a delivery's arrival, which is the weaker
    but more available anchor a ``Shot`` always carries when it is not a
    placeholder for a delivery nobody saw. Neither anchor: no window: nothing
    stands in for a shot we cannot place in time.

    Pure: no media, no cache. ``video_id`` and ``end_number`` are left at
    placeholders -- this function is never told either -- for
    ``windows_for_video`` to fill in per end.
    """
    camera = hogtime.CAMERA_FOR[house]
    out: list[Window] = []
    for shot in shots:
        if getattr(shot, "missing", False):
            continue
        release = getattr(shot, "release", None)
        t_release = _release_t(release) if release is not None else None
        t_rest = getattr(shot, "t_rest_s", None)
        if t_release is not None:
            t0 = t_release + longview.WINDOW_S[0]
            t1 = t_release + longview.WINDOW_S[1]
        else:
            if t_rest is None or t_rest != t_rest:  # nan check, no import needed
                continue
            t0 = t_rest - hogtime.ARRIVAL_LOOKBACK_S[1]
            t1 = t_rest - hogtime.ARRIVAL_LOOKBACK_S[0]
        out.append(Window(
            video_id="",
            camera=camera,
            color=shot.color,
            t0=t0,
            t1=t1,
            end_number=0,
            shot_number=shot.number,
            t_release=t_release,
            t_rest=t_rest,
        ))
    return out


def _shots_for_end(read_path, proxy_setups, end, from_s, detector):
    """Everything ``analyze.analyze`` does for one end, short of the clock and
    the hog crossing itself.

    Lifted from ``scripts/split_coverage.py``'s ``_shots_for_end`` -- same
    walk, duplicated because that script sits outside the installed package
    and cannot be imported from here.
    """
    setup = proxy_setups[end.house]
    seq = list(sequence.detect_end(read_path, setup, end, analyze_mod.SHOT_FPS,
                                   detector, from_s=from_s))
    deliveries = [d for d in D.find_deliveries(
                      seq, view_x_limit_m=setup.view_x_limit_m,
                      view_y_min_m=setup.view_y_min_m)
                  if d.t_enter >= from_s]
    gaps = secondpass.gaps_to_search(deliveries, end.start_s, end.end_s)
    recovered = secondpass.search(seq, gaps, deliveries)
    if recovered:
        deliveries = sorted(deliveries + recovered, key=lambda d: d.t_enter)
    far = proxy_setups[analyze_mod.OTHER_HOUSE[end.house]]
    far_seq = list(sequence.detect_span(read_path, far, from_s, end.end_s,
                                        R.RELEASE_FPS, detector))
    releases, thrown_by, unaccounted = R.find_and_pair(
        far_seq, far.view_y_min_m, deliveries, seq, since=from_s)
    if unaccounted:
        deliveries = sorted(deliveries + unaccounted, key=lambda d: d.t_enter)
    kept = fit.fit_end(deliveries)
    shots = shots_mod.from_deliveries(
        kept, seq, thrown_by={id(d): r for r, d in thrown_by.items()})
    next_from = min(end.end_s, kept[-1].t_rest) if kept else end.end_s
    return shots, next_from


def windows_for_video(video_path, vid, root, views, *, detector=None,
                      progress=None) -> Iterator[Window]:
    """Every delivery window in one cached video.

    Repeats the cache walk ``scripts/split_coverage.py`` already does --
    calibrate the panels, build the strip proxy, find games and ends, detect
    each end's deliveries and pair its releases with the far panel -- and for
    each end with shots, asks ``windows_for_end`` for its windows in the
    camera that watches that end's throwing side.

    ``views`` is a mapping of camera name ("left"/"right") to whatever a
    caller uses to mean "this side view calibrated" (e.g. a
    ``geometry.sideview.SideView``, or the ``ViewInfo.view`` from
    ``harvest/sideviews.py``); an end whose throwing-end camera is missing
    from it, or maps to ``None``, contributes no windows -- there is nothing
    for Task 3 to crop frames from without a calibrated view.

    Not pure: reads (and may extend) the proxy and detection caches under
    ``root``, exactly like ``analyze.analyze`` and ``split_coverage.py`` do.
    ``detector`` defaults to the standard weights (``weights.default_path``)
    at the same settings ``split_coverage.py`` uses; pass one in to reuse a
    detector already loaded for other videos in the same run.
    """
    progress = progress or (lambda msg: None)
    video_path = Path(video_path)

    if detector is None:
        from curling_score import weights as weights_mod
        from curling_score.detect import yolo

        detector = yolo.YoloDetector(weights_mod.default_path(), conf=0.30,
                                     device=None, imgsz=448)
        detector.model.overrides["half"] = True

    calib_frames = F.sample_keyframes(video_path, count=analyze_mod.CALIB_FRAMES,
                                      stride=analyze_mod.CALIB_STRIDE)
    panels = layout.detect_panels(calib_frames)
    setups = profile.calibrate_panels(calib_frames, panels)

    strip = proxy.strip_rect(panels.top, panels.bottom)
    read_path = proxy.proxy_path(vid, strip, root)
    if not read_path.is_file():
        progress(f"{vid}: no cached proxy, building one (this is the slow path)")
        read_path = proxy.ensure_proxy(video_path, vid, strip, root=root)
    proxy_setups = analyze_mod._proxy_setups(setups, strip)

    progress(f"{vid}: building the whole-video activity profile...")
    samples = profile.build_profile(read_path, proxy_setups)
    games = segment.segment_games(samples)
    total_ends = sum(len(g.ends) for g in games)
    progress(f"{vid}: {len(games)} game(s), {total_ends} end(s) total")

    done_ends = 0
    prev_end_s = None
    for game in games:
        for end in game.ends:
            from_s = analyze_mod.run_up_from(
                prev_end_s, end.start_s, crossed_games=(end is game.ends[0]))
            progress(f"{vid} game {game.index} end {end.number} ({end.house}): "
                    f"detecting shots ({done_ends}/{total_ends} ends done so far)...")
            try:
                shots, prev_end_s = _shots_for_end(
                    read_path, proxy_setups, end, from_s, detector)
            except Exception as exc:  # noqa: BLE001 -- one bad end must not sink the walk
                progress(f"{vid} game {game.index} end {end.number}: "
                        f"FAILED to build shots: {exc!r}")
                prev_end_s = end.end_s
                done_ends += 1
                continue
            done_ends += 1
            if not shots:
                progress(f"{vid} game {game.index} end {end.number}: no shots built")
                continue

            throwing_house = analyze_mod.OTHER_HOUSE[end.house]
            camera = hogtime.CAMERA_FOR[throwing_house]
            view = views.get(camera)
            if view is None:
                progress(f"{vid} game {game.index} end {end.number}: "
                        f"no calibrated {camera} view, skipping its {len(shots)} shots")
                continue

            for w in windows_for_end(shots, throwing_house):
                yield dataclasses.replace(w, video_id=vid, end_number=end.number)
