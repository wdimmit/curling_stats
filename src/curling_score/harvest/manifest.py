"""The dataset, written down.

ds8 could not be rebuilt because it was never written down: the set was
whatever ``ends[::len(ends)//4][:4]`` returned on the day, from scripts in a
``/tmp`` directory that a reboot cleared. The manifest is the fix. It names
every frame, the split it belongs to, and the labels the detector proposed,
and it goes into git.

Two properties make it worth the file. A rebuild from the same manifest and the
same clips produces the same frame names, so the hand corrections keyed to
those names still apply. And the *shortfall* is recorded next to the quota, so
the set's real composition is a number someone can read rather than the one
that was asked for.
"""

import datetime as _dt

from curling_score.harvest.candidates import Candidate, bin_of
from curling_score.train.dataset import Label


def candidate_to_json(c: Candidate) -> dict:
    return {
        "stem": c.stem,
        "video_id": c.video_id,
        "panel": c.panel,
        "t_abs": round(c.t_abs, 3),
        "clip_start_s": round(c.clip_start_s, 3),
        "kind": c.kind,
        "bin": bin_of(c),
        "n_red": c.n_red,
        "n_yellow": c.n_yellow,
        "flight_id": c.flight_id,
        "lighting": c.lighting,
        "labels": [[lab.cls, lab.cx, lab.cy, lab.w, lab.h] for lab in c.labels],
        "box_wh": list(c.box_wh),
        "neighbours": list(c.neighbours),
    }


def candidate_from_json(d: dict) -> Candidate:
    return Candidate(
        video_id=d["video_id"], panel=d["panel"], t_abs=d["t_abs"],
        clip_start_s=d["clip_start_s"], kind=d["kind"],
        n_red=d["n_red"], n_yellow=d["n_yellow"], flight_id=d.get("flight_id"),
        lighting=d.get("lighting", "lit"),
        labels=tuple(Label(int(r[0]), *(float(v) for v in r[1:])) for r in d["labels"]),
        box_wh=tuple(d.get("box_wh") or ()),
        neighbours=tuple(d.get("neighbours") or ()),
    )


def build_manifest(chosen_by_video, splits, quota, extra=None) -> dict:
    """The selected frames for every video, with what each could not supply.

    ``chosen_by_video`` maps a video id to ``(candidates, shortfall)`` as
    :func:`curling_score.harvest.candidates.select` returns them.
    """
    videos, counts, bins = {}, {"train": 0, "val": 0}, {}
    for vid in sorted(chosen_by_video):
        picked, shortfall = chosen_by_video[vid]
        split = splits[vid]  # a video with no split is a bug, not a default
        counts[split] = counts.get(split, 0) + len(picked)
        for c in picked:
            bins[bin_of(c)] = bins.get(bin_of(c), 0) + 1
        videos[vid] = {
            "split": split,
            "shortfall": dict(shortfall),
            "frames": [candidate_to_json(c) for c in picked],
        }

    doc = {
        "created": _dt.date.today().isoformat(),
        "quota": dict(quota),
        "summary": {
            "videos": len(videos),
            "frames": sum(counts.values()),
            "train_frames": counts.get("train", 0),
            "val_frames": counts.get("val", 0),
            "bins": dict(sorted(bins.items())),
            "videos_short": sorted(v for v, d in videos.items() if d["shortfall"]),
        },
        "videos": videos,
    }
    if extra:
        # Loudly, not silently. Passing extra={"videos": path} once replaced the
        # whole frame index with a string, and the failure surfaced two stages
        # later as "string indices must be integers".
        clash = sorted(set(extra) & set(doc))
        if clash:
            raise ValueError(
                f"extra would overwrite the manifest's own {', '.join(clash)}")
        doc.update(extra)
    return doc


def iter_frames(doc):
    """``(split, Candidate)`` for every selected frame, in a stable order."""
    for vid in sorted(doc["videos"]):
        entry = doc["videos"][vid]
        for row in entry["frames"]:
            yield entry["split"], candidate_from_json(row)
