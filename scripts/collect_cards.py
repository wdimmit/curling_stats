"""Collect real wall-card digit training data across many VODs, one at a time.

The classifier trained on the board's self-labelled printed 1-14 row reads
held-out *printed* glyphs at 99.3% and real *cards* at 4 of 11 -- digit 2 at 0
of 17, confidently misread as 7. Both preprocessing explanations were ruled out
by experiment and `_glyph_ink` is already shared by the two paths, so the card
typeface genuinely differs from the printed one and printed glyphs alone cannot
teach it. That leaves collecting real cards, and one VOD holds only about a
dozen *distinct physical* cards however many frames it is sampled at -- the
reference video gave 69 rows off 10 distinct cards. So the data has to come from
many videos, which is what this does.

Why it is a separate script from `harvest_board.py` rather than a flag on it:
this one owns the *download* side, and the download side is where the
constraints live.

* **Disk.** This box has ~7.5 GB free and each ds12 VOD is ~2 GB, so the videos
  cannot be queued -- one is fetched, harvested, and deleted before the next is
  touched. Free space is asserted before every download, and the run stops
  cleanly rather than filling the disk.
* **Only what we fetched is deleted.** `~/.cache/curling_score/videos/` is a
  shared cache; the reference VOD in it is what the slow tests read. A video
  already cached when this script started is left alone, and the delete refuses
  any path that is not the `<video_id>.mp4` this iteration just downloaded.
* **Politeness.** Sequential, never concurrent, and `attempts=1` so a bot check
  aborts the run instead of sleeping through `BACKOFF_S` three times. A burst
  of requests from this address once got the whole public IP blocked for a
  quarter of an hour; the remedy for a block is a person, not a retry.
* **Resume.** Labels are written per video, so an interruption costs the video
  in flight rather than the run. A video whose directory already holds rows is
  skipped unless `--force`.

The `end` field is written null. The digit on a card is read by hand off the
contact sheets (`--sheets`), exactly as in Task 1: whole card rows upscaled with
the slot grid drawn over them, because a per-card crop at this pitch is
ambiguous -- a tile is barely wider than the slot pitch, so a crop loose enough
to hold a card hung off-centre also holds its neighbour's digit.
"""

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import cv2

from curling_score.game import scoreboard as SB
from curling_score.ingest import cache as C
from curling_score.ingest import source as S

# The shared cache holds one VOD that this task did not download and must not
# remove: the slow tests and other sessions read it. Belt and braces -- the
# delete already refuses anything it did not itself fetch.
PROTECTED = {"VXU9xwmugRg"}

# A ds12 VOD is ~2 GB and the harvest writes ~30 MB of PNGs beside it. Stop with
# room to spare rather than discover the limit by wedging the box.
MIN_FREE_BYTES = 4 * 1024**3


def _harvest_board():
    """The sibling script, loaded by path: `scripts/` is not an import package.

    Its `sample` (keyframe window -> median stack) and `contact_sheet` are the
    harvest, and they are what a second copy here would get subtly wrong.
    """
    path = Path(__file__).resolve().parent / "harvest_board.py"
    spec = importlib.util.spec_from_file_location("harvest_board", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HB = _harvest_board()


def free_bytes(path="/") -> int:
    return shutil.disk_usage(path).free


def select(catalogue: Path, dates, sheets, limit=None):
    """The videos to work, shortest first within a date.

    Shorter is strictly better here: every VOD yields about the same handful of
    distinct cards, so a shorter one is the same data for less download.
    """
    meta = json.loads(catalogue.read_text())
    vids = [v for v in meta["videos"]
            if (not dates or v["date"] in dates)
            and (not sheets or v["sheet"] in sheets)]
    vids.sort(key=lambda v: (v["date"], v["duration_s"], v["sheet"]))
    return vids[:limit] if limit else vids


def harvest(video_path, vid, date, sheet, out_dir, sheets_dir, times):
    """Sample the board across one video; return (rows, states).

    ``states`` keeps every sample point including the ones that yield no cards.
    A blank board is not a failed sample -- it is the boundary between the two
    games a VOD usually holds, and the accumulation check has to be applied
    within a game, so the blanks are recorded rather than dropped.
    """
    rows, states, first = [], [], None
    for t0 in times:
        img = HB.sample(video_path, t0)
        if img is None:
            states.append({"t_s": t0, "status": "no_frames"})
            print(f"  t={int(t0):05d}: no frames", flush=True)
            continue
        if first is None:
            first = img
        geom = SB.find_board(img)
        if geom is None:
            states.append({"t_s": t0, "status": "no_board"})
            print(f"  t={int(t0):05d}: no board", flush=True)
            continue
        if not SB.is_readable(img, geom):
            states.append({"t_s": t0, "status": "obstructed"})
            print(f"  t={int(t0):05d}: board obstructed", flush=True)
            continue
        reading = SB.read_slots(img, geom)
        if reading.is_blank():
            states.append({"t_s": t0, "status": "blank"})
            print(f"  t={int(t0):05d}: blank board", flush=True)
            continue

        name = f"board_t{int(t0):05d}.png"
        cv2.imwrite(str(out_dir / name), img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        if sheets_dir:
            HB.contact_sheet(img, geom, sheets_dir / f"{vid}_t{int(t0):05d}.png")
        states.append({"t_s": t0, "status": "cards", "frame": name,
                       "yellow": sorted(reading.yellow),
                       "red": sorted(reading.red)})
        for color, slots in (("yellow", reading.yellow), ("red", reading.red)):
            for slot in sorted(slots):
                rows.append({"video_id": vid, "date": date, "sheet": sheet,
                             "frame": name, "t_s": t0, "color": color,
                             "slot": slot, "end": None})
        print(f"  {name}: yellow={sorted(reading.yellow)} "
              f"red={sorted(reading.red)}", flush=True)

    # A video that yields nothing is the one case where the evidence is about
    # to be deleted: w_idtwUuEpM returned "no board" on all 23 samples and the
    # VOD was gone before anyone could ask why. One downscaled JPEG answers
    # "is the board out of shot, or is `find_board` missing it?" for 40 KB
    # instead of a 1.5 GB re-download.
    if not rows and first is not None:
        small = cv2.resize(first, None, fx=0.5, fy=0.5,
                           interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out_dir / "nothing-found.jpg"), small,
                    [cv2.IMWRITE_JPEG_QUALITY, 70])
        print("  wrote nothing-found.jpg", flush=True)
    return rows, states


def hand_labelled(labels: Path) -> int:
    if not labels.exists():
        return 0
    return sum(1 for r in json.loads(labels.read_text())
               if r.get("end") is not None)


def drop_video(path: Path, vid: str) -> None:
    """Remove a video this run downloaded, and nothing else.

    Three separate reasons to refuse, because the cost of getting this wrong is
    someone else's cache: the name must be the id just fetched, the file must
    sit in a `videos/` directory, and the id must not be a protected one.
    """
    if vid in PROTECTED:
        raise RuntimeError(f"refusing to delete protected video {vid}")
    if path.name != f"{vid}.mp4" or path.parent.name != "videos":
        raise RuntimeError(f"refusing to delete unexpected path {path}")
    size = path.stat().st_size if path.exists() else 0
    path.unlink(missing_ok=True)
    print(f"  deleted {path} ({size / 1e9:.2f} GB)", flush=True)


def games_of(states) -> dict:
    """Map each frame name to the index of the game it belongs to.

    The board is wiped between games and a VOD usually holds two, so the
    accumulation check has to run inside a game -- across a wipe, cards
    *disappear*, which is the one thing that check forbids. Only a confidently
    blank board ends a game: an obstructed or unfound board says nothing about
    whether the cards are still hanging, so it cannot be a boundary.
    """
    game, wiped, out = 0, False, {}
    for s in states:
        if s["status"] == "blank":
            wiped = True
        elif s["status"] == "cards":
            # Bumped when play resumes, not on each blank sample: a wipe is
            # sampled several times over, and counting each one would number
            # the games 0, 4, 9 and make the index unreadable.
            if wiped and out:
                game += 1
            wiped = False
            out[s["frame"]] = game
    return out


def load_hand(path: Path) -> tuple:
    """The hand reading: the cards, and the detections that are not cards.

    A plain list is all cards. The object form carries ``reject`` as well, for
    frames where `read_slots` found a "card" that a person's head was.
    """
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data, []
    return data["cards"], data.get("reject", [])


def expand(cards, rows, games, reject=()) -> list:
    """Attach the hand-read digit to every row.

    One physical card is one ``(game, colour, slot)``: within a game a team's
    card at slot *k* says "this team's total reached k", and it is hung once and
    never touched again, so the same digit is right in every frame it appears
    in. That is what makes the hand reading affordable -- about a dozen cards
    per video rather than a hundred-odd rows -- and it is the propagation the
    third self-consistency check then re-tests independently.

    ``reject`` is per *frame*, not per card, and it has to be: spectators lean
    on the boards, and `is_readable` only guards the printed row, which sits
    above them -- so a row of heads in the red band reads as a row of cards
    while the board itself still passes as readable. On s_iPqkT02q8 that put
    eight phantom cards in one frame. The same slot can be a phantom in one
    frame and a real card in another (red 5 there is a head at t=4500 and a
    genuine 5 at t=6300), so a per-card key could not express it.

    Rejected windows are kept, labelled ``"no_card"``, rather than dropped.
    They are exactly what a presence gate has to refuse, and they cost nothing:
    a reader that wants digits filters on the label it already has to filter.
    """
    hand = {(c["game"], c["color"], c["slot"]): c["end"] for c in cards}
    no = {(x["frame"], x["color"], s) for x in reject for s in x["slots"]}
    out, missing = [], set()
    for r in rows:
        if (r["frame"], r["color"], r["slot"]) in no:
            out.append({**r, "end": "no_card"})
            continue
        key = (games.get(r["frame"]), r["color"], r["slot"])
        if key not in hand:
            missing.add(key)
            continue
        out.append({**r, "end": hand[key]})
    if missing:
        raise SystemExit(f"no hand reading for {sorted(missing)}")
    return out


def check(rows, games) -> tuple:
    """The four self-consistency checks. Returns (errors, notes).

    A wrong label is worse than a missing one: it does not just fail to teach,
    it teaches the wrong thing, and nothing downstream can tell it from a right
    one. These are cheap and they catch the two mistakes a human reading 20 px
    digits actually makes -- reading the slot instead of the digit, and reading
    a neighbour's glyph.

    A card that vanishes for one sample and comes back is a *note*, not an
    error, and the distinction is not a convenience. `read_slots` judges a slot
    occupied by its intra-slot brightness range, so a spectator standing in
    front of a card flattens that range and the card goes unread -- checked by
    eye on L7mK9r5gio0 t=2700, where the red 2 is plainly still hanging behind
    somebody's yellow jacket. No mislabelling can cause a dropout and a dropout
    cannot put a wrong digit in the set: the frame simply contributes no row for
    that card. The errors are the ones a bad hand reading would produce.
    """
    bad, notes = [], []
    # Only rows carrying an actual end number can be ordered or counted:
    # "illegible" has no place in a sequence and "no_card" is not a card.
    legible = [r for r in rows if isinstance(r["end"], int)]

    frames = sorted({r["frame"] for r in rows})
    for frame in frames:
        here = [r for r in legible if r["frame"] == frame]
        # 1: sorted by end, a team's slots strictly increase.
        for color in ("yellow", "red"):
            mine = sorted((r["end"], r["slot"]) for r in here
                          if r["color"] == color)
            slots = [s for _, s in mine]
            ends = [e for e, _ in mine]
            if slots != sorted(slots) or len(set(slots)) != len(slots):
                bad.append(f"{frame} {color}: slots {slots} not increasing "
                           f"with ends {ends}")
        # 2: an end belongs to one team, so it cannot appear on both.
        ys = {r["end"] for r in here if r["color"] == "yellow"}
        rs = {r["end"] for r in here if r["color"] == "red"}
        both = ys & rs
        if both:
            bad.append(f"{frame}: end(s) {sorted(both)} on both teams")

    by_game = {}
    for r in legible:
        by_game.setdefault(games.get(r["frame"]), []).append(r)
    for game, mine in sorted(by_game.items(), key=lambda kv: (kv[0] is None,
                                                              kv[0])):
        # 3: within a game the board only accumulates -- a card never vanishes
        # and never changes its digit.
        seen = {}
        prev_keys = set()
        for t in sorted({r["t_s"] for r in mine}):
            keys = set()
            for r in (x for x in mine if x["t_s"] == t):
                k = (r["color"], r["slot"])
                keys.add(k)
                if seen.setdefault(k, r["end"]) != r["end"]:
                    bad.append(f"game {game} t={t:.0f} {k}: digit changed "
                               f"{seen[k]} -> {r['end']}")
            gone = prev_keys - keys
            if gone:
                notes.append(f"game {game} t={t:.0f}: card(s) {sorted(gone)} "
                             f"not detected (occlusion; still hung)")
            prev_keys |= keys
        # 4: the ends of a game are 1..N with nothing missing.
        ends = sorted({r["end"] for r in mine})
        if ends and ends != list(range(1, len(ends) + 1)):
            # A blanked end scores nothing and so hangs no card, which would
            # leave a real gap. Flagged as an error anyway, because the far
            # likelier cause is a misread digit and the two are told apart by
            # looking, not by assuming.
            bad.append(f"game {game}: ends {ends} are not 1..N")
    return bad, notes


def pack(rows, out: Path) -> None:
    """Cut every labelled row's raw card window out of its frame into one npz.

    The frames themselves are not what training consumes -- the model sees a
    ~24x22 px window -- and Task 1's 21 committed 1080p PNGs were already
    25 MB, so ten videos of them would put a quarter of a gigabyte into git for
    ever. The windows are four orders of magnitude smaller for the same
    training value.

    Raw, not normalised: `card_window` is what `_card_glyph` starts from, so a
    later change to the tile segmentation, the ink localisation or
    GLYPH_SHAPE -- all three of which have moved once already -- can be applied
    to this set without re-downloading 15 GB of video.

    Windows vary in height by a pixel or two between frames, so they are
    zero-padded into one array with an explicit ``shape`` column: patch *i* is
    ``patches[i][:shape[i, 0], :shape[i, 1]]``. Padded rather than an object
    array so the file loads without ``allow_pickle``.
    """
    import numpy as np

    geoms, grays, patches, kept = {}, {}, [], []
    for r in rows:
        key = (r["video_id"], r["frame"])
        if key not in grays:
            img = cv2.imread(str(out / r["video_id"] / r["frame"]))
            if img is None:
                raise SystemExit(f"missing frame {key}; re-harvest before --merge")
            grays[key] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            geoms[key] = SB.find_board(img)
        window = SB.card_window(grays[key], geoms[key], r["color"], r["slot"])
        if window is None:
            print(f"  ! no window for {key} {r['color']}{r['slot']}", flush=True)
            continue
        patches.append(np.ascontiguousarray(window))
        kept.append(r)

    h = max(p.shape[0] for p in patches)
    w = max(p.shape[1] for p in patches)
    stack = np.zeros((len(patches), h, w), np.uint8)
    shapes = np.zeros((len(patches), 2), np.int16)
    for i, p in enumerate(patches):
        stack[i, :p.shape[0], :p.shape[1]] = p
        shapes[i] = p.shape

    def col(name, dtype):
        return np.array([r[name] for r in kept], dtype=dtype)

    np.savez_compressed(
        out / "cards.npz",
        patches=stack, shape=shapes,
        video_id=col("video_id", "<U11"), date=col("date", "<U10"),
        sheet=col("sheet", np.int16), frame=col("frame", "<U24"),
        t_s=col("t_s", np.float32), color=col("color", "<U6"),
        slot=col("slot", np.int16),
        # A string column, because "illegible" is a real label here: those
        # rows are what a reject gate has to catch.
        end=np.array([str(r["end"]) for r in kept], dtype="<U9"),
    )
    size = (out / "cards.npz").stat().st_size
    print(f"{len(kept)} patches up to {h}x{w} px -> {out / 'cards.npz'} "
          f"({size / 1024:.0f} KiB)")


def samples(rows, out: Path, per_video: int = 2) -> None:
    """Keep a couple of whole frames per video in git for checking by eye.

    Two, not more: a 1080p board frame is ~1.3 MB, so this is the one part of
    the set whose size is worth counting. The two are the *fullest* frame, which
    carries every card that video ever posted, and the *sparsest*, which shows
    an early board -- picking the two by card count rather than by time keeps
    them different from each other, where three consecutive samples of one
    unchanged state would have shown the same thing three times.

    Everything else is regenerable from the VOD and is gitignored.
    """
    dest = out / "samples"
    dest.mkdir(exist_ok=True)
    for old in dest.glob("*.png"):
        old.unlink()
    by_video = {}
    for r in rows:
        counts = by_video.setdefault(r["video_id"], {})
        counts[r["frame"]] = counts.get(r["frame"], 0) + 1
    for vid, frames in sorted(by_video.items()):
        order = sorted(frames.items(), key=lambda kv: (kv[1], kv[0]))
        chosen = {order[-1][0]}                    # the fullest
        if per_video > 1:
            chosen.add(order[0][0])                # and the sparsest
        for frame in sorted(chosen)[:per_video]:
            shutil.copyfile(out / vid / frame, dest / f"{vid}_{frame}")
    print(f"{len(list(dest.glob('*.png')))} sample frames in {dest}")


def merge(out: Path) -> int:
    """Expand every video's hand reading, check it, and write the dataset."""
    all_rows, failed = [], False
    for vdir in sorted(p for p in out.iterdir() if p.is_dir()):
        rows_path, cards_path = vdir / "labels.json", vdir / "cards.json"
        if not rows_path.exists():
            continue
        rows = json.loads(rows_path.read_text())
        if not cards_path.exists():
            print(f"{vdir.name}: not yet hand-read, skipped", flush=True)
            continue
        states = json.loads((vdir / "states.json").read_text())
        games = games_of(states)
        hand, reject = load_hand(cards_path)
        labelled = expand(hand, rows, games, reject)
        bad, notes = check(labelled, games)
        rows_path.write_text(json.dumps(labelled, indent=2) + "\n")
        distinct = len({(games.get(r["frame"]), r["color"], r["slot"])
                        for r in labelled if isinstance(r["end"], int)})
        phantom = sum(1 for r in labelled if r["end"] == "no_card")
        print(f"{vdir.name}: {len(labelled)} rows, {distinct} distinct cards, "
              f"{phantom} no_card, {len(bad)} errors, {len(notes)} notes",
              flush=True)
        for b in bad:
            print(f"  ! {b}", flush=True)
        for n in notes:
            print(f"  - {n}", flush=True)
        failed = failed or bool(bad)
        all_rows.extend(labelled)

    (out / "labels.json").write_text(json.dumps(all_rows, indent=2) + "\n")
    print(f"\n{len(all_rows)} rows written to {out / 'labels.json'}")
    if all_rows:
        pack(all_rows, out)
        samples(all_rows, out)
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true",
                    help="expand the hand readings in each video's cards.json "
                         "into labels.json, run the self-consistency checks, "
                         "and write the combined dataset; harvests nothing")
    ap.add_argument("--catalogue", default="datasets/ds12/videos.json")
    ap.add_argument("--out", default="datasets/board-cards-train")
    ap.add_argument("--sheets", default=None,
                    help="scratch dir for the contact sheets read by hand")
    ap.add_argument("--dates", default=None, help="comma-separated ds12 dates")
    ap.add_argument("--sheet-numbers", default=None,
                    help="comma-separated sheet numbers (default: all)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--step-s", type=float, default=300.0)
    ap.add_argument("--start-s", type=float, default=300.0)
    ap.add_argument("--end-s", type=float, default=None,
                    help="default: the catalogued duration")
    ap.add_argument("--keep-video", action="store_true",
                    help="do not delete after harvesting (disk permitting)")
    ap.add_argument("--force", action="store_true",
                    help="re-harvest a video whose labels already carry "
                         "hand-filled digits, discarding them")
    args = ap.parse_args()

    if args.merge:
        return merge(Path(args.out))

    dates = [d.strip() for d in args.dates.split(",")] if args.dates else None
    sheets = ([int(s) for s in args.sheet_numbers.split(",")]
              if args.sheet_numbers else None)
    todo = select(Path(args.catalogue), dates, sheets, args.limit)
    if not todo:
        raise SystemExit("no videos matched the selection")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sheets_dir = Path(args.sheets) if args.sheets else None
    if sheets_dir:
        sheets_dir.mkdir(parents=True, exist_ok=True)

    print(f"{len(todo)} videos selected: "
          + ", ".join(f"{v['video_id']}({v['date']} s{v['sheet']})"
                      for v in todo), flush=True)

    failures = 0
    for i, v in enumerate(todo, start=1):
        vid = v["video_id"]
        vdir = out / vid
        labels = vdir / "labels.json"
        done = hand_labelled(labels)
        if done and not args.force:
            print(f"[{i}/{len(todo)}] {vid}: {done} hand labels present, "
                  f"skipping (--force to discard)", flush=True)
            continue
        if labels.exists() and not args.force:
            print(f"[{i}/{len(todo)}] {vid}: already harvested, skipping",
                  flush=True)
            continue

        already = C.is_cached(vid)
        if not already:
            free = free_bytes()
            if free < MIN_FREE_BYTES:
                print(f"[{i}/{len(todo)}] {vid}: only {free / 1e9:.2f} GB free, "
                      f"below the {MIN_FREE_BYTES / 1e9:.0f} GB floor; stopping "
                      f"cleanly with {len(todo) - i + 1} videos unfetched",
                      flush=True)
                return 2
            print(f"[{i}/{len(todo)}] {vid}: downloading "
                  f"({free / 1e9:.2f} GB free)", flush=True)
        else:
            print(f"[{i}/{len(todo)}] {vid}: already cached", flush=True)

        try:
            # attempts=1: a bot check aborts the run for a person to look at,
            # rather than sleeping out 300+600+1200 s and asking again.
            path = C.ensure_cached(S.canonical_url(vid), attempts=1)
        except C.BlockedError as exc:
            print(f"BLOCKED on {vid}: {exc}\n"
                  f"Stopping. {len(todo) - i + 1} videos unfetched.", flush=True)
            return 3
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds
            # Not a bot check -- a 403 part-way through a fragment, most
            # likely. Move on to the next video rather than lose the run to
            # one bad fetch, but never re-ask for the same one, and give up
            # entirely after two in a row: at that point it is not this video,
            # it is us, and asking a third time is how an address gets blocked.
            failures += 1
            print(f"FAILED on {vid} ({failures} in a row): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            if failures >= 2:
                print(f"Two consecutive download failures; stopping with "
                      f"{len(todo) - i} videos unfetched.", flush=True)
                return 4
            continue
        failures = 0

        try:
            vdir.mkdir(parents=True, exist_ok=True)
            end_s = args.end_s if args.end_s is not None else v["duration_s"]
            n = int((end_s - args.start_s) // args.step_s) + 1
            times = [args.start_s + k * args.step_s for k in range(n)]
            rows, states = harvest(path, vid, v["date"], v["sheet"], vdir,
                                   sheets_dir, times)
            # Written before the video goes, so an interruption between the two
            # costs a re-download at worst and never the harvest.
            labels.write_text(json.dumps(rows, indent=2) + "\n")
            (vdir / "states.json").write_text(json.dumps(states, indent=2) + "\n")
            frames = len({r["frame"] for r in rows})
            print(f"  {vid}: {len(rows)} cards over {frames} frames", flush=True)
        finally:
            if not already and not args.keep_video:
                drop_video(path, vid)

        print(f"  free now {free_bytes() / 1e9:.2f} GB", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
