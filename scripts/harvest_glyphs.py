"""Harvest the board's printed 1-14 row as self-labelled digit training data.

Template matching was measured and failed -- 46% on cards, 0 of 11 distinct
cards read consistently, and 73% even on *printed* digits across frames (spec
section *Why template matching failed*). So `read_digit` is backed by a trained
classifier instead, and this writes its training set.

The training data is free. The printed 1-14 row is in every board frame whether
or not any cards are hung, and it is self-labelling: the digit at slot *k* is
*k*. Sampling the cached VOD therefore yields nine labelled glyphs per readable
frame with no human labelling at all.

Only slots 1..9 are kept: slots 10..14 hold two digits in one slot width, and a
card digit is never two digits, so they are not valid training examples.

The sample time travels with every glyph so that a train/validate split can
hold out whole *frames*. Splitting on individual glyphs would put augmented
copies of the same physical glyph on both sides of the split and make the
validation score meaningless.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from curling_score.game import scoreboard as SB
from curling_score.ingest import frames as F

# A card digit is a single digit, so the two-digit slots 10..14 are not valid
# examples of anything a card can carry.
MAX_LABEL = 9


def sample(video, t, window_s=90.0, max_frames=40):
    """One de-occluded board image at ``t``, or None.

    Median-stacked keyframes, exactly as `read_board_at` reads the board in
    production, so the glyphs trained on are the glyphs that will be read.
    """
    lo, hi = max(0.0, t - window_s), t + window_s
    imgs = []
    for ts, img in F.keyframe_sweep(video, start_s=lo, end_s=hi):
        if ts < lo:
            continue
        if len(imgs) >= max_frames:
            break
        imgs.append(img)
    return SB.median_frame(imgs) if imgs else None


def harvest(video, times, window_s=90.0, max_frames=40, verbose=True):
    """Labelled glyphs from every readable board frame among ``times``."""
    xs, ys, ts, groups = [], [], [], []
    for t0 in times:
        img = sample(video, t0, window_s=window_s, max_frames=max_frames)
        if img is None:
            if verbose:
                print(f"t={int(t0):05d}: no frames", flush=True)
            continue
        geom = SB.find_board(img)
        if geom is None:
            if verbose:
                print(f"t={int(t0):05d}: no board", flush=True)
            continue
        if not SB.is_readable(img, geom):
            if verbose:
                print(f"t={int(t0):05d}: board obstructed", flush=True)
            continue

        # `templates` already crops exactly these glyphs, keyed 1..14, and
        # normalises them the same way a card glyph is normalised before it is
        # read. Reusing it is what keeps training and inference on one crop.
        tmpl = SB.templates(img, geom)
        # How crisply the whole printed row reads, kept per frame rather than
        # filtered on here: a partly occluded row can still clear
        # `is_readable`, and a mislabelled glyph is worse than a missing one,
        # so training gets to set the bar itself.
        n_groups = SB._printed_digit_groups(img, geom)
        for k in range(1, MAX_LABEL + 1):
            xs.append(np.asarray(tmpl[k], dtype=np.float32))
            ys.append(k)
            ts.append(float(t0))
            groups.append(int(n_groups))
        if verbose:
            print(f"t={int(t0):05d}: {MAX_LABEL} glyphs, "
                  f"printed groups={n_groups}", flush=True)

    if not xs:
        raise SystemExit("no readable board frames found")
    return (np.stack(xs), np.asarray(ys, np.int16),
            np.asarray(ts, np.float64), np.asarray(groups, np.int16))


def check(x, y, t_s, groups):
    """Everything the set has to be true for, asserted rather than eyeballed."""
    assert x.ndim == 3 and x.shape[1:] == SB.GLYPH_SHAPE, x.shape
    assert x.dtype == np.float32, x.dtype
    assert len(x) == len(y) == len(t_s) == len(groups)
    assert not np.isnan(x).any(), "glyphs hold NaN"
    assert np.isfinite(x).all(), "glyphs hold inf"
    assert y.min() >= 1 and y.max() <= MAX_LABEL, (y.min(), y.max())

    counts = {int(k): int((y == k).sum()) for k in range(1, MAX_LABEL + 1)}
    # Every frame contributes exactly one of each label, so the classes must be
    # exactly balanced. Anything else means a slot was dropped somewhere.
    assert len(set(counts.values())) == 1, counts
    frames = len(np.unique(t_s))
    assert len(x) == frames * MAX_LABEL, (len(x), frames)
    return counts, frames


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--out", default="datasets/board-glyphs/glyphs.npz")
    ap.add_argument("--step-s", type=float, default=60.0)
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--end-s", type=float, default=14400.0)
    ap.add_argument("--window-s", type=float, default=90.0,
                    help="half-width of the median-stacking window")
    ap.add_argument("--max-frames", type=int, default=40)
    args = ap.parse_args()

    n = int((args.end_s - args.start_s) // args.step_s) + 1
    times = [args.start_s + i * args.step_s for i in range(n)]

    x, y, t_s, groups = harvest(args.video, times, window_s=args.window_s,
                                max_frames=args.max_frames)
    counts, frames = check(x, y, t_s, groups)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, x=x, y=y, t_s=t_s, groups=groups,
                        window_s=np.float64(args.window_s),
                        video=str(args.video))
    print(json.dumps({"glyphs": int(len(x)), "frames": int(frames),
                      "per_class": counts,
                      "bytes": out.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
