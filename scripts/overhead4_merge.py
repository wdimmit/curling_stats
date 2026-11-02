#!/usr/bin/env python
"""Build overhead-4's training set: overhead-3's stones plus the members' player boxes.

    python scripts/overhead4_merge.py --base /data/wdd/curling/overhead-3/folds/all \\
        --players datasets/member-labels/DATE/players \\
        [--hard-stones .../boxes/agreed [--hard-settled box-edits.json]] \\
        [--leave-out left-out.txt] [--pilot] --out /data/wdd/curling/overhead-4/folds/all
    python scripts/overhead4_merge.py settle --export edits.json --players .../players

--base is a YOLO folder with `images/` and `labels/` (the laptop's overhead-3
fold `all` has `images/train` and `images/val`, symlinks into ds11's set and
the logo crops; a frame is copied, not linked, and the train/val folders carry
over). Fold `all`'s players are published from its `images/train` only: its
`images/val` is ds11's val, published on its own as fold `val` (label_players.py
refuses a val frame under another fold), and both go in the merge under --base. --players is `labels_export.py --kind player`'s `players/` folder, or
its `labelled/` inside it. The output has the same layout, with a `data.yaml`
for the three classes: 0 red, 1 yellow, 2 player.

A frame's label is its stone lines (classes 0 and 1; any class 2 already in the
file is dropped) plus the frame's player lines. The player labels are found by
the frame's stem (`origin.stem` in players.jsonl): item ids are opaque. A stem
labelled in two places is fine if the two labels agree and an error if they don't.
A label file of no lines is a frame reviewed with nobody on the ice, and is kept.
A base frame with no stone label file at all is an error (it would be taught as
background), as is an --out whose images/ or labels/ already hold files.

Training on a partly reviewed frame is the thing this refuses: a base frame with
no player label is an error naming it, unless its stem is in --leave-out (one
stem per line; the frames the owner could not settle), which drops it and
counts it. --pilot drops the unlabelled frames and counts them instead, for an
early run on what is reviewed so far, and refuses an --out containing
`overhead-4`, so a pilot set can never be the real one.

--hard-stones is `labels_export.py --kind box`'s `boxes/agreed/` folder, for the
hard batch (scripts/player_mine.py, published as `hard-1`). A hard frame goes
into train when its stones were agreed by the members (or settled by the owner,
below) and it has a player label; a hard frame short of either is dropped and
counted (`hard_no_stones`, `hard_no_player`).

The hard batch's stones go up as unflagged box items holding overhead-3's own
reads, so two members accepting them untouched is a rubber stamp
(labelagree.box_verdict) and the item is disputed, never agreed. The owner
settles those in the box export's `boxes/disputed/` editor; --hard-settled is
that editor's export (`boxes`: item id -> [[cls, cx, cy, w, h]], `reviewed`),
read with `boxes/disputed/disputed.jsonl` (next to agreed/, or --hard-disputed)
to find each item id's stem. Only reviewed items count, with their class 0 and 1
boxes as the stone label; a stem agreed already keeps the agreed boxes. They are
counted in `hard_settled` as well as `hard`.

The frames the owner settles in the box editor (`players/to_user/`) come back
as an editor export. `settle` turns each frame it marks reviewed into an entry
of `players/labelled/`, class 2 only (an editor frame also carries the stones),
and prints the stems still unsettled, to go in --leave-out. Run it again after
a later export; frames already settled are left as they are.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

# overhead-3's own names for 0 and 1 (its data.yaml), then player.
NAMES = ("red_stone", "yellow_stone", "player")
PLAYER = "2"
SUFFIXES = (".jpg", ".jpeg", ".png")


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def _labelled(players: Path) -> Path:
    """The folder holding players.jsonl: ``players`` or its `labelled/`."""
    if (players / "players.jsonl").is_file():
        return players
    if (players / "labelled" / "players.jsonl").is_file():
        return players / "labelled"
    raise ValueError(f"no players.jsonl in {players} or {players / 'labelled'}")


def player_labels(players: Path) -> dict[str, list[str]]:
    """The class-2 lines of each labelled frame, by stem."""
    root = _labelled(players)
    out: dict[str, list[str]] = {}
    for n, line in enumerate(root.joinpath("players.jsonl").read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        stem = (row.get("origin") or {}).get("stem")
        if not stem:
            raise ValueError(f"players.jsonl line {n} ({row.get('id')}) has no origin.stem")
        path = root / "labels" / f"{row['id']}.txt"
        if not path.is_file():
            raise ValueError(f"{row['id']} is in players.jsonl but {path} is missing")
        lines = [ln for ln in _lines(path.read_text()) if ln.split()[0] == PLAYER]
        if stem in out and out[stem] != lines:
            raise ValueError(f"{stem} is labelled twice with different players ({row['id']} "
                             "is the second); settle which is right")
        out[stem] = lines
    return out


def agreed_stones(hard: Path) -> dict[str, tuple[Path, list[str]]]:
    """``{stem: (image, stone lines)}`` of the box export's agreed frames."""
    out = {}
    for line in hard.joinpath("agreed.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        stem = (row.get("origin") or {}).get("stem")
        label, image = hard / "labels" / f"{row['id']}.txt", hard / "images" / f"{row['id']}.jpg"
        if not stem or not label.is_file() or not image.is_file():
            continue
        out[stem] = (image, [ln for ln in _lines(label.read_text()) if ln.split()[0] in ("0", "1")])
    return out


def settled_stones(export: Path, disputed: Path) -> dict[str, tuple[Path, list[str]]]:
    """``{stem: (image, stone lines)}`` of the owner's reviewed box-editor frames.

    ``export`` is the editor's export of ``disputed`` (the box export's
    `boxes/disputed/` folder): a frame counts only if it is in ``reviewed``, and
    its class 0 and 1 boxes are its stones (none is a frame with no stones).
    Two reviewed items of one stem that differ are an error.
    """
    data = json.loads(export.read_text())
    boxes, reviewed = data.get("boxes") or {}, set(data.get("reviewed") or [])
    out: dict[str, tuple[Path, list[str]]] = {}
    for line in disputed.joinpath("disputed.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        stem = (row.get("origin") or {}).get("stem")
        image = disputed / "images" / f"{row['id']}.jpg"
        if not stem or row["id"] not in reviewed or not image.is_file():
            continue
        lines = ["{} ".format(int(b[0])) + " ".join(f"{v:.6f}" for v in b[1:5])
                 for b in boxes.get(row["id"]) or [] if int(b[0]) in (0, 1)]
        if stem in out and out[stem][1] != lines:
            raise ValueError(f"{stem} is settled twice with different stones ({row['id']} "
                             "is the second); settle which is right")
        out[stem] = (image, lines)
    return out


def _write(out: Path, rel: Path, image: Path, lines: list[str]) -> None:
    """One frame: the image copied (a link is followed) to ``rel`` under
    images/, and its label beside it under labels/."""
    (out / "images" / rel.parent).mkdir(parents=True, exist_ok=True)
    (out / "labels" / rel.parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(image, out / "images" / rel)
    (out / "labels" / rel.parent / f"{rel.stem}.txt").write_text(
        "".join(ln + "\n" for ln in lines))


def _data_yaml(out: Path) -> None:
    train = "images/train" if (out / "images" / "train").is_dir() else "images"
    val = "images/val" if (out / "images" / "val").is_dir() else train
    names = "".join(f"  {i}: {n}\n" for i, n in enumerate(NAMES))
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: {train}\nval: {val}\nnames:\n{names}")


def merge(base_images: Path, base_labels: Path, players: Path, out: Path, leave_out: set[str],
          pilot: bool = False, hard_stones: Path | None = None,
          hard_settled: Path | None = None, hard_disputed: Path | None = None) -> dict:
    """Write the merged set to ``out`` and return the counts.

    kept: frames written (hard: of them from the hard batch); unlabelled:
    base frames dropped for want of a player label (pilot only; otherwise
    one is an error); left_out: frames dropped by ``leave_out``; base: base
    frames looked at. With ``hard_stones``, hard_no_stones is the player labels
    that match neither a base frame nor an agreed (or, with ``hard_settled``, the
    owner's settled) hard frame (a hard frame whose stones nobody settled; with a
    val-only --base, the train labels too) and hard_no_player the hard frames
    with stones and no player label; hard_settled counts the kept hard frames
    whose stones are the owner's. ``hard_disputed`` is the box export's
    disputed/ folder, next to ``hard_stones``'s agreed/ unless given.
    """
    if pilot and "overhead-4" in str(Path(out).resolve()):
        raise ValueError(f"a pilot set must not go under overhead-4: {out}")
    for sub in ("images", "labels"):
        if (out / sub).is_dir() and any(p.is_file() for p in (out / sub).rglob("*")):
            raise ValueError(f"{out / sub} already holds files; frames left from an earlier "
                             "merge would be trained on. Merge into an empty folder.")
    labels = player_labels(players)
    frames = sorted(p for p in base_images.rglob("*") if p.suffix.lower() in SUFFIXES)
    if not frames:
        raise ValueError(f"no images under {base_images}")
    stems = [p.stem for p in frames]
    if len(set(stems)) != len(stems):
        raise ValueError("two base frames share a stem; players are matched by stem")
    missing = [s for s in stems if s not in labels and s not in leave_out]
    if missing and not pilot:
        raise ValueError(f"{len(missing)} base frames have no player label and are not in "
                         f"the leave-out list: {', '.join(missing[:10])}"
                         + (" ..." if len(missing) > 10 else ""))
    # A frame with no stone label file is not a frame with no stones: it would be
    # taught as background. (Logo crops ship empty files.) Usually a wrong --base.
    unread = [p.stem for p in frames if p.stem in labels and p.stem not in leave_out
              and not (base_labels / p.relative_to(base_images).with_suffix(".txt")).is_file()]
    if unread:
        raise ValueError(f"{len(unread)} base frames have no stone label file under {base_labels}: "
                         f"{', '.join(unread[:10])}" + (" ..." if len(unread) > 10 else ""))
    if hard_settled and not hard_stones:
        raise ValueError("--hard-settled goes with --hard-stones (the hard batch)")
    hard = agreed_stones(hard_stones) if hard_stones else {}
    owners = {}
    if hard_settled:
        owners = settled_stones(hard_settled, hard_disputed or Path(hard_stones).parent / "disputed")
        owners = {k: v for k, v in owners.items() if k not in hard}
        hard = {**owners, **hard}
    clash = sorted(set(hard) & set(stems))
    if clash:
        raise ValueError(f"hard frames that are base frames too: {', '.join(clash[:10])}")

    counts = dict.fromkeys(("base", "kept", "hard", "hard_settled", "unlabelled", "left_out",
                            "hard_no_stones", "hard_no_player"), 0)
    for path in frames:
        counts["base"] += 1
        if path.stem in leave_out:
            counts["left_out"] += 1
            continue
        if path.stem not in labels:
            counts["unlabelled"] += 1
            continue
        label = base_labels / path.relative_to(base_images).with_suffix(".txt")
        stones = [ln for ln in _lines(label.read_text()) if ln.split()[0] != PLAYER]
        _write(out, path.relative_to(base_images), path, stones + labels[path.stem])
        counts["kept"] += 1
    for stem in sorted((set(labels) | set(hard)) - set(stems)) if hard_stones else ():
        if stem in leave_out:
            counts["left_out"] += 1
        elif stem not in hard:
            counts["hard_no_stones"] += 1
        elif stem not in labels:
            counts["hard_no_player"] += 1
        else:
            image, stones = hard[stem]
            _write(out, Path("train") / f"{stem}{image.suffix}", image, stones + labels[stem])
            counts["kept"] += 1
            counts["hard"] += 1
            counts["hard_settled"] += stem in owners
    out.mkdir(parents=True, exist_ok=True)
    _data_yaml(out)
    return counts


def settle(export: Path, to_user: Path, labelled: Path) -> dict:
    """File the owner's editor edits as labelled players; returns the counts and
    the stems still unsettled.

    ``export`` is the box editor's export (``boxes``: item id -> [[cls, cx, cy, w, h]],
    ``reviewed``); a frame counts only if it is in ``reviewed``. Only its class 2
    boxes are kept, as labels_export writes them. A frame already in players.jsonl
    is left alone. The editor's frame names are the item ids.
    """
    data = json.loads(export.read_text())
    boxes, reviewed = data.get("boxes") or {}, set(data.get("reviewed") or [])
    index = labelled / "players.jsonl"
    have = {json.loads(ln)["id"] for ln in index.read_text().splitlines() if ln.strip()} \
        if index.is_file() else set()
    settled, unsettled, rows = 0, [], []
    for line in to_user.joinpath("to_user.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        item = row["id"]
        if item in have:
            continue
        if item not in reviewed:
            unsettled.append(row["origin"]["stem"])
            continue
        mine = [b for b in boxes.get(item) or [] if int(b[0]) == int(PLAYER)]
        (labelled / "images").mkdir(parents=True, exist_ok=True)
        (labelled / "labels").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(to_user / "images" / f"{item}.jpg", labelled / "images" / f"{item}.jpg")
        (labelled / "labels" / f"{item}.txt").write_text(
            "".join(f"{PLAYER} " + " ".join(f"{v:.6f}" for v in b[1:5]) + "\n" for b in mine))
        rows.append({"id": item, "origin": row["origin"], "members": [], "settled_by": "owner"})
        settled += 1
    with open(index, "a") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    return {"settled": settled, "unsettled": unsettled}


def _leave_out(path: Path | None) -> set[str]:
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()} if path else set()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["settle"]:
        ap = argparse.ArgumentParser(prog="overhead4_merge.py settle",
                                     description="file the owner's settled frames as labelled players")
        ap.add_argument("--export", type=Path, required=True, help="the box editor's export JSON")
        ap.add_argument("--players", type=Path, required=True,
                        help="labels_export's players/ folder (labelled/ and to_user/)")
        args = ap.parse_args(argv[1:])
        got = settle(args.export, args.players / "to_user", args.players / "labelled")
        print(f"settled {got['settled']}, {len(got['unsettled'])} still unsettled")
        for stem in got["unsettled"]:
            print(stem)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", type=Path, required=True, metavar="DIR",
                    help="a folder with images/ and labels/ (overhead-3's folds/all)")
    ap.add_argument("--players", type=Path, required=True, metavar="DIR")
    ap.add_argument("--hard-stones", type=Path, metavar="DIR",
                    help="the box export's agreed/ folder, for the hard batch")
    ap.add_argument("--hard-settled", type=Path, metavar="FILE",
                    help="the box editor's export of boxes/disputed/: the owner's stones "
                         "for hard frames the members did not agree on")
    ap.add_argument("--hard-disputed", type=Path, metavar="DIR",
                    help="the box export's disputed/ folder (default: beside --hard-stones)")
    ap.add_argument("--leave-out", type=Path, metavar="FILE", help="one stem per line")
    ap.add_argument("--pilot", action="store_true",
                    help="drop unlabelled frames instead of failing; not under overhead-4")
    ap.add_argument("--out", type=Path, required=True, metavar="DIR")
    args = ap.parse_args(argv)
    try:
        got = merge(args.base / "images", args.base / "labels", args.players, args.out,
                    _leave_out(args.leave_out), args.pilot, args.hard_stones,
                    args.hard_settled, args.hard_disputed)
    except (OSError, ValueError) as err:
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print(" ".join(f"{k} {v}" for k, v in got.items()))
    print(f"wrote {args.out}/data.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
