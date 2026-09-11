"""Turn clips into labelled candidate frames.

One decode pass per clip serves both houses: the panels are two crops of the
same frame, so decoding twice would double the only part of this that costs
anything. Measured on the 3070 -- 1.1 s to find and calibrate a video's panels,
7 s to decode its ten clips at 5 fps, 10 s to run the detector over the result.
Call it half an hour for the season.

Sampling at 5 fps rather than at the two moments we actually want is what makes
the motion pass possible: a stone crossing the panel is only visible for about
four seconds, and it has to be *tracked* to be told from a person walking. The
grid frames then come out of the same pass for nothing.
"""

from pathlib import Path

from curling_score.harvest import motion
from curling_score.train import dataset

DETECT_FPS = 5.0
GRID_PER_CLIP = 2
MOTION_FRAMES = 3
JPEG_QUALITY = 92


def grid_moments(times, n: int):
    """``n`` moments spread through a clip, avoiding its very edges.

    The first moments are the keyframe lead -- video from before the window
    anyone asked for -- and the last can be short. Sampling strictly inside
    keeps both out.
    """
    ordered = sorted(times)
    if not ordered or n < 1:
        return []
    if len(ordered) <= n:
        return ordered
    return [ordered[round((i + 1) * len(ordered) / (n + 1))] for i in range(n)]


def nearest_moments(wanted, times):
    """Snap wanted times onto moments that were actually decoded."""
    ordered = sorted(times)
    if not ordered:
        return []
    out = set()
    for w in wanted:
        out.add(min(ordered, key=lambda t: abs(t - w)))
    return sorted(out)


def neighbours_of(t, times):
    """The decoded moments either side of ``t``, for motion context."""
    ordered = sorted(times)
    try:
        i = ordered.index(t)
    except ValueError:
        return (None, None)
    return (ordered[i - 1] if i > 0 else None,
            ordered[i + 1] if i + 1 < len(ordered) else None)


def clip_moments(clip_path, pts_start_s: float, rects, fps: float = DETECT_FPS):
    """``(absolute_t, {panel: crop})`` across one clip, decoding once.

    Times are absolute source time. A clip's own timeline cannot name a frame
    or link to the video, so it is never used past this point.
    """
    import av
    import numpy as np

    from curling_score.ingest import frames as F

    out = []
    with av.open(str(clip_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        offset = F._stream_start(stream)
        step, wanted = 1.0 / fps, None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base) - offset
            if wanted is None:
                wanted = t
            if t < wanted - 1e-9:
                continue
            img = frame.to_ndarray(format="bgr24")
            crops = {}
            for name, (x, y, w, h) in rects.items():
                crops[name] = np.ascontiguousarray(img[y:y + h, x:x + w])
            out.append((round(t + pts_start_s, 3), crops))
            while wanted <= t + 1e-9:
                wanted += step
    return out


def build_video_pool(video_id, clip_paths, setup, detector, out_dir, *,
                     fps: float = DETECT_FPS, grid_per_clip: int = GRID_PER_CLIP,
                     motion_frames: int = MOTION_FRAMES,
                     jpeg_quality: int = JPEG_QUALITY, extra_detector=None):
    """Every candidate frame one video can offer, written out with its labels.

    Returns ``(candidates, stats)``. Grid frames come from fixed points in each
    clip; motion frames come from tracking within it. A moment that is both is
    kept once, as motion -- the flight is why it is wanted.

    The labels written here are the detector's opinion. They are a starting
    point for a person, not the dataset: nothing enters the set until a human
    has ticked the frame.

    ``extra_detector`` unions a second model's detections into that first guess
    (via ``dataset.merge_detections``, confidence-sorted so the surer reading
    wins a collision). It exists for a set built to *compare* two models: label
    it with one of them and its own misses are the ones the reviewer is least
    likely to notice, which quietly flatters it. A union hides neither.
    """
    import cv2

    from curling_score.harvest.candidates import Candidate
    from curling_score.geometry import lighting

    out_dir = Path(out_dir) / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    panels = {p.name: p for p in setup.panels.values() if p.calib is not None}
    rects = {name: tuple(p.rect) for name, p in panels.items()}
    found, stats = [], {"clips": 0, "moments": 0, "flights": 0, "written": 0}

    for clip_path in clip_paths:
        from curling_score.ingest import frames as F

        pts_start = F.stream_start_s(clip_path)
        moments = clip_moments(clip_path, pts_start, rects, fps)
        if not moments:
            continue
        stats["clips"] += 1
        stats["moments"] += len(moments)
        times = [t for t, _ in moments]
        clip_start = times[0]

        for name, panel in panels.items():
            crops = [crop[name] for _t, crop in moments]
            dets = detector.find_stones_batch(crops, panel.calib)
            if extra_detector is not None:
                other = extra_detector.find_stones_batch(crops, panel.calib)
                dets = [dataset.merge_detections(a, b) for a, b in zip(dets, other)]
            seq = list(zip(times, dets))

            flights = motion.find_flights(seq)
            stats["flights"] += len(flights)
            wanted = {}  # t -> (kind, flight_id)
            for t in grid_moments(times, grid_per_clip):
                wanted[t] = ("grid", None)
            for fid, flight in enumerate(flights):
                for t in nearest_moments(
                        motion.flight_times(flight, motion_frames), times):
                    wanted[t] = ("motion", fid)  # a flight outranks the grid

            by_time = dict(seq)
            crop_at = {t: c for (t, c) in zip(times, crops)}
            box = dataset.box_px(panel.calib)
            for t, (kind, fid) in sorted(wanted.items()):
                img = crop_at[t]
                # A panel with its lights off is a black rectangle: it teaches
                # nothing and costs a review click. Rare in practice -- 4 of
                # 418 candidates measured, because the clips stop short of the
                # end of the night, when sheets go dark as they finish.
                if not lighting.is_playable(img):
                    stats["dark"] = stats.get("dark", 0) + 1
                    continue
                h, w = img.shape[:2]
                these = by_time.get(t, [])
                labs = [lab for lab in
                        (dataset.to_label(d, box, w, h, clip=True) for d in these)
                        if lab is not None]
                stem = f"{video_id}_{name[0]}_{t:09.2f}".replace(".", "_")
                cv2.imwrite(str(out_dir / f"{stem}.jpg"), img,
                            [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                saved = []
                if kind == "motion":
                    for near in neighbours_of(t, times):
                        if near is None:
                            continue
                        nstem = f"{video_id}_{name[0]}_{near:09.2f}".replace(".", "_")
                        cv2.imwrite(str(out_dir / f"{nstem}.jpg"), crop_at[near],
                                    [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                        saved.append(nstem)
                found.append(Candidate(
                    video_id=video_id, panel=name, t_abs=t, clip_start_s=clip_start,
                    kind=kind,
                    n_red=sum(1 for d in these if d.color == "red"),
                    n_yellow=sum(1 for d in these if d.color == "yellow"),
                    flight_id=fid,
                    lighting=lighting.classify(img).value,
                    labels=tuple(labs),
                    box_wh=(box / w, box / h),
                    neighbours=tuple(saved)))
                stats["written"] += 1

    found.sort(key=lambda c: (c.t_abs, c.panel))
    return found, stats
