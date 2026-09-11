"""Review the labels a training set was built from.

The model can only be as good as what it was told. This one was taught that a
player's red shoes are a red stone, because the classical detector that wrote
the labels said so -- and no amount of extra data of the same kind can unteach
it. So the labels themselves need looking at, and looking at them one by one is
not practical: there are eleven thousand frames.

What makes that tractable is the same fact the detector rests on: a stone stays
where it is. A label that appears in one frame and is gone from the next was
never a stone, whatever wrote it. Ranking on that puts the bad labels first and
leaves the rest to be spot-checked.

Box size is no help here, for a reason worth writing down: `write_split` gives
every box in a frame the same size, so they carry no information about what
they enclose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from curling_score.geometry import constants as C

CLASS_NAMES = ("red", "yellow")
# How far a box may be from one frame to the next and still be the same stone.
# Saved frames are a second apart, and a stone in flight crosses roughly a
# quarter of the panel in that time, so a tight tolerance does not separate
# phantoms from stones -- it separates *moving* stones from still ones, which
# is not the question. Ranking on that flagged 507 frames in the cleaned set,
# almost all of them deliveries in flight.
LINK_TOL = 0.30
# A run of labels shorter than this never settles anywhere and never completes
# a delivery. Stones are on the ice for minutes; flicker lasts a frame or two.
MIN_RUN = 3
# A frame this far from its neighbours in count is worth a look.
COUNT_JUMP = 3
_NAME = re.compile(r"^(?P<seq>.+?)_(?P<a>\d+)_(?P<b>\d+)$")


@dataclass(frozen=True)
class Box:
    cls: int
    cx: float
    cy: float
    w: float
    h: float

    @property
    def color(self) -> str:
        return CLASS_NAMES[self.cls] if self.cls < len(CLASS_NAMES) else "?"


@dataclass(frozen=True)
class Frame:
    image: Path
    seq: str
    t: float
    boxes: tuple


def parse_name(stem: str):
    """``g2e1_b_008117_00`` -> ("g2e1_b", 8117.0), or None."""
    m = _NAME.match(stem)
    if not m:
        return None
    return m.group("seq"), float(f"{int(m.group('a'))}.{m.group('b')}")


def load_split(root, split: str):
    """Every labelled frame in a split, grouped by sequence and in time order."""
    root = Path(root)
    img_dir, lbl_dir = root / "images" / split, root / "labels" / split
    out: dict[str, list[Frame]] = {}
    for img in sorted(img_dir.glob("*.jpg")):
        parsed = parse_name(img.stem)
        if parsed is None:
            continue
        seq, t = parsed
        lbl = lbl_dir / f"{img.stem}.txt"
        boxes = []
        if lbl.exists():
            for line in lbl.read_text().split("\n"):
                parts = line.split()
                if len(parts) == 5:
                    boxes.append(Box(int(parts[0]), *(float(v) for v in parts[1:])))
        out.setdefault(seq, []).append(Frame(img, seq, t, tuple(boxes)))
    return {k: sorted(v, key=lambda f: f.t) for k, v in out.items()}


def runs(seq, tol: float = LINK_TOL):
    """Link each frame's boxes to the next frame's, nearest first.

    Not a tracker -- just enough to ask how long each label persists. A stone
    is on the ice for minutes whether it is moving or still; a label that
    exists for a frame or two was never one.
    """
    live: list[list] = []   # each run: [(frame_index, box_index), ...]
    open_runs: dict[int, int] = {}  # box index in previous frame -> run index
    for i, f in enumerate(seq):
        taken, nxt = set(), {}
        order = sorted(
            ((j, b) for j, b in enumerate(f.boxes)),
            key=lambda jb: jb[0])
        for j, b in order:
            best, best_d = None, tol
            for pj, run_i in open_runs.items():
                if run_i in taken:
                    continue
                pb = seq[i - 1].boxes[pj]
                if pb.cls != b.cls:
                    continue
                d = ((pb.cx - b.cx) ** 2 + (pb.cy - b.cy) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = run_i, d
            if best is None:
                live.append([(i, j)])
                best = len(live) - 1
            else:
                live[best].append((i, j))
            taken.add(best)
            nxt[j] = best
        open_runs = nxt
    return live


@dataclass(frozen=True)
class Finding:
    frame: Frame
    reason: str
    confidence: float
    marked: tuple  # indices into frame.boxes worth looking at
    # The frames either side. The claim being made is that a label comes and
    # goes, which cannot be judged from one frame -- the neighbours are the
    # evidence, so they are shown alongside it.
    context: tuple = ()


def find_suspect_labels(frames, min_run: int = MIN_RUN):
    """Labels that do not behave like stones, worst first."""
    out = []
    for seq in frames.values():
        short = {}
        for run in runs(seq):
            if len(run) < min_run:
                for i, j in run:
                    short.setdefault(i, []).append((j, len(run)))
        for i, f in enumerate(seq):
            near = tuple(seq[j] for j in (i - 1, i + 1) if 0 <= j < len(seq))
            if len(f.boxes) > C.STONES_PER_END:
                out.append(Finding(
                    f, f"{len(f.boxes)} stones labelled, and only "
                       f"{C.STONES_PER_END} are thrown in an end", 0.95,
                    tuple(range(len(f.boxes))), near))
                continue
            if i in short:
                marks = tuple(j for j, _n in short[i])
                span = min(n for _j, n in short[i])
                out.append(Finding(
                    f, f"{len(marks)} label(s) lasting only {span} frame(s) "
                       f"here -- either this is not a stone, or a stone that "
                       f"is one went unlabelled either side", 0.85, marks,
                    near))
                continue
            if near:
                others = [len(n.boxes) for n in near]
                if min(abs(len(f.boxes) - o) for o in others) >= COUNT_JUMP:
                    out.append(Finding(
                        f, f"{len(f.boxes)} labels against {others} either side",
                        0.6, (), near))
    return sorted(out, key=lambda x: -x.confidence)


def sample_frames(frames, every: int = 40):
    """A plain spread across the set, to catch anything systematic."""
    out = []
    for seq in frames.values():
        out.extend(seq[::every])
    return sorted(out, key=lambda f: (f.seq, f.t))


# Drawn so the pixels under a box stay visible: a rectangle, not a fill.
BOX_BGR = {0: (60, 60, 235), 1: (40, 200, 235)}
MARK_BGR = (255, 255, 255)


def draw(frame, marked=()):  # pragma: no cover - exercised via render()
    """The frame with its labels drawn, and the suspect ones ringed."""
    import cv2

    img = cv2.imread(str(frame.image))
    if img is None:
        return None
    h, w = img.shape[:2]
    for i, b in enumerate(frame.boxes):
        x0 = int((b.cx - b.w / 2) * w)
        y0 = int((b.cy - b.h / 2) * h)
        x1 = int((b.cx + b.w / 2) * w)
        y1 = int((b.cy + b.h / 2) * h)
        cv2.rectangle(img, (x0, y0), (x1, y1), BOX_BGR.get(b.cls, MARK_BGR), 1)
        if i in marked:
            cv2.circle(img, ((x0 + x1) // 2, (y0 + y1) // 2),
                       max(x1 - x0, y1 - y0), MARK_BGR, 1, cv2.LINE_AA)
    return img


def render(findings, samples, out_dir, per_strip: int = 6):
    """A page of label problems, worst first, then a plain spread."""
    import html as _html

    import cv2

    from curling_score.train.review import contact_sheet

    out_dir = Path(out_dir)
    (out_dir / "strips").mkdir(parents=True, exist_ok=True)
    items = []

    def strip(frames, marks, name):
        imgs, labels = [], []
        for f, mk in zip(frames, marks):
            im = draw(f, mk)
            if im is None:
                continue
            imgs.append(im)
            labels.append(f"{f.seq} {f.t:.1f}s  {len(f.boxes)} labels")
        sheet = contact_sheet(imgs, labels)
        if sheet is None:
            return None
        cv2.imwrite(str(out_dir / name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return name

    for i, f in enumerate(findings, 1):
        name = f"strips/bad{i:04d}.jpg"
        # In time order with the suspect frame in its place, so the label can
        # be watched appearing and vanishing.
        seq = sorted([f.frame, *f.context], key=lambda x: x.t)
        marks = [f.marked if x is f.frame else () for x in seq]
        if strip(seq, marks, name) is None:
            continue
        items.append((f"{f.frame.seq} {f.frame.t:.1f}s", f.reason,
                      f.confidence, name))
    for i in range(0, len(samples), per_strip):
        chunk = samples[i:i + per_strip]
        name = f"strips/sample{i // per_strip:04d}.jpg"
        if strip(chunk, [()] * len(chunk), name) is None:
            continue
        items.append((f"{chunk[0].seq} {chunk[0].t:.0f}s+", "sample", 0.0, name))

    body = "\n".join(
        f'''<article><header><b>#{n}</b>
    <code>{_html.escape(where)}</code>
    <span class="pill">{_html.escape(reason)}</span>
    {f'<span class="muted">{conf:.2f}</span>' if conf else ""}
    </header><img src="{img}" loading="lazy" alt=""></article>'''
        for n, (where, reason, conf, img) in enumerate(items, 1)
    )
    page = out_dir / "index.html"
    page.write_text(f"""<!doctype html>
<meta charset="utf-8">
<title>Training labels</title>
<style>
  :root {{ --bg:#f7f7f5; --line:#ddd; --muted:#666; }}
  body {{ background:var(--bg); font:14px/1.5 system-ui, sans-serif;
          margin:0; padding:16px; }}
  article {{ background:#fff; border:1px solid var(--line); border-radius:8px;
             padding:10px; margin-bottom:12px; }}
  header {{ display:flex; gap:10px; align-items:center; margin-bottom:8px;
            flex-wrap:wrap; }}
  code {{ background:rgba(0,0,0,.06); padding:1px 6px; border-radius:4px; }}
  .pill {{ padding:2px 8px; border-radius:99px; border:1px solid var(--line); }}
  .muted {{ color:var(--muted); }}
  img {{ width:100%; height:auto; display:block; border-radius:6px;
         image-rendering:crisp-edges; }}
</style>
<h1>Training labels &mdash; {len(findings)} suspect, {len(samples)} sampled</h1>
<p class="muted">Boxes are drawn as the model was taught them: red and yellow by
class. A white ring marks a label that does not behave like a stone &mdash; it
    lasts only a frame or two. That has two readings and the ranking cannot
tell them apart: the label may be wrong, or it may be the only correct one and
its neighbours may be missing. Both are worth knowing. The sampled strips at
the end are a plain spread, to catch anything systematic the ranking would
miss.</p>
{body}
""")
    return page, len(items)



# --- rejecting labels by hand -------------------------------------------
#
# Static rules keep producing partial fixes. Every one written so far -- the
# quiet-span test, the play span, the flight rule, the edge clipping -- removed
# some bad labels and left others, and two of them removed good labels as well.
# A person can tell a stone from a knee at a glance, and there are only a few
# hundred labels worth arguing about, so let them say so directly.
#
# A rejection names the box rather than its position in a list, so it survives
# the set being rebuilt: the frame's own name plus the box centre, rounded to
# a thousandth of the image, which is far finer than two stones ever are apart.


def reject_key(frame_stem: str, box) -> str:
    return f"{frame_stem}|{box.cx:.3f},{box.cy:.3f}"


def add_key(frame_stem: str, cls: int, cx: float, cy: float) -> str:
    return f"{frame_stem}|{cx:.3f},{cy:.3f}|{cls}"


@dataclass(frozen=True)
class Edits:
    """One export: what was rejected, what was added, and what was looked at."""

    reject: tuple = ()
    add: tuple = ()
    reviewed: tuple = ()
    scope: str | None = None


def parse_edits(data):
    """Accept either a bare list of rejections or {"reject": [], "add": []}."""
    if isinstance(data, dict):
        return list(data.get("reject", [])), list(data.get("add", []))
    return list(data), []


def parse_edits_full(data) -> Edits:
    """The same file, plus the fields added later.

    ``reviewed`` records the frames a person actually looked at, which is the
    difference between a label they agreed with and one they never saw.
    ``scope`` names the dataset and split the session was for: a session on the
    validation split once leaked 110 edits into an export meant for training,
    and the file had no way to say so.

    Older exports have neither, and still load.
    """
    rejects, adds = parse_edits(data)
    reviewed = list(data.get("reviewed", [])) if isinstance(data, dict) else []
    scope = data.get("scope") if isinstance(data, dict) else None
    return Edits(tuple(rejects), tuple(adds), tuple(reviewed), scope)


def merge_edits(*payloads, scope=None) -> Edits:
    """Union several exports.

    Review happens over several sittings, on more than one machine, in two
    waves. Each session exports its own file and every file is kept, so a bad
    session can be dropped without losing the rest -- which means the thing
    that gets applied is always a merge.
    """
    reject, add, reviewed, scopes = set(), set(), set(), set()
    for payload in payloads:
        one = payload if isinstance(payload, Edits) else parse_edits_full(payload)
        reject |= set(one.reject)
        add |= set(one.add)
        reviewed |= set(one.reviewed)
        if one.scope:
            scopes.add(one.scope)
    return Edits(tuple(sorted(reject)), tuple(sorted(add)),
                 tuple(sorted(reviewed)), scope or (scopes.pop() if len(scopes) == 1 else None))


def coverage(root, split: str, reviewed) -> tuple:
    """How much of a split has actually been looked at.

    Returns ``(seen, total, unreviewed_stems)``. Worth printing before training
    on a set: a frame nobody reviewed still carries whatever the detector said.
    """
    lbl_dir = Path(root) / "labels" / split
    stems = sorted(p.stem for p in lbl_dir.glob("*.txt"))
    seen = set(reviewed)
    missing = [s for s in stems if s not in seen]
    return len(stems) - len(missing), len(stems), missing


def _read_boxes(path):
    out = []
    for line in path.read_text().split("\n"):
        parts = line.split()
        if len(parts) == 5:
            out.append(Box(int(parts[0]), *(float(v) for v in parts[1:])))
    return out


def _write_boxes(path, boxes):
    path.write_text("\n".join(
        f"{b.cls} {b.cx:.6f} {b.cy:.6f} {b.w:.6f} {b.h:.6f}"
        for b in boxes) + "\n")


def apply_edits(root, split: str, rejects, adds=(), *, keep_empty=False,
                box_for=None, reviewed=None, drop_unreviewed=False) -> dict:
    """Delete and add the named boxes in a dataset's label files.

    Additions matter as much as removals and arguably more: a stone with no
    label teaches the model that it is not a stone, which is the failure behind
    every missing delivery traced by eye.

    ``keep_empty`` keeps a frame that a reviewer stripped to nothing, with an
    empty label file, instead of deleting it. Those frames are the model's
    false positives on people, which makes them the most valuable negatives in
    the set -- deleting them throws away the very correction just made.

    ``box_for(stem) -> (w, h)`` sizes an added box. Across 120 videos there are
    120 different values of ``px_per_m``, so the fallback of borrowing the
    set's median box is wrong nearly everywhere; a caller holding the manifest
    can size it properly.

    ``drop_unreviewed`` removes frames absent from ``reviewed``. An unreviewed
    frame still carries whatever the detector said about it, and letting those
    in silently is what would make the whole exercise circular again.
    """
    root = Path(root)
    lbl_dir, img_dir = root / "labels" / split, root / "images" / split
    wanted = set(rejects)
    by_frame: dict[str, list] = {}
    for key in adds:
        stem, pos, cls = key.split("|")
        cx, cy = (float(v) for v in pos.split(","))
        by_frame.setdefault(stem, []).append((int(cls), cx, cy))

    # Added boxes take the size the caller gives, else the size every other box
    # in that frame has, else the set's median.
    sizes = []
    for lbl in lbl_dir.glob("*.txt"):
        for b in _read_boxes(lbl):
            sizes.append((b.w, b.h))
    med_w, med_h = (sorted(w for w, _ in sizes)[len(sizes) // 2],
                    sorted(h for _, h in sizes)[len(sizes) // 2]) \
        if sizes else (0.074, 0.043)

    counts = {"removed": 0, "added": 0, "rewritten": 0, "emptied": 0,
              "kept_empty": 0, "missing_frames": 0, "unreviewed": 0}

    if drop_unreviewed:
        seen = set(reviewed or ())
        for lbl in sorted(lbl_dir.glob("*.txt")):
            if lbl.stem in seen:
                continue
            lbl.unlink(missing_ok=True)
            (img_dir / f"{lbl.stem}.jpg").unlink(missing_ok=True)
            counts["unreviewed"] += 1

    stems = {p.stem for p in lbl_dir.glob("*.txt")} | set(by_frame)
    for stem in sorted(stems):
        lbl = lbl_dir / f"{stem}.txt"
        boxes = _read_boxes(lbl) if lbl.exists() else []
        keep = [b for b in boxes if reject_key(stem, b) not in wanted]
        counts["removed"] += len(boxes) - len(keep)
        sized = box_for(stem) if box_for else None
        new_boxes = []
        for cls, cx, cy in by_frame.get(stem, ()):
            if sized:
                w, h = sized
            elif boxes:
                w, h = boxes[0].w, boxes[0].h
            else:
                w, h = med_w, med_h
            new_boxes.append(Box(cls, cx, cy, w, h))
        if new_boxes and not (img_dir / f"{stem}.jpg").exists():
            counts["missing_frames"] += 1
            continue
        counts["added"] += len(new_boxes)
        final = keep + new_boxes
        if final == boxes and not (keep_empty and not final and lbl.exists()):
            continue
        if final:
            _write_boxes(lbl, final)
            counts["rewritten"] += 1
        elif keep_empty:
            lbl.write_text("")
            counts["kept_empty"] += 1
        else:
            lbl.unlink(missing_ok=True)
            (img_dir / f"{stem}.jpg").unlink(missing_ok=True)
            counts["emptied"] += 1
    return counts


def apply_rejections(root, split: str, rejects) -> tuple:
    """Backwards-compatible wrapper around `apply_edits`."""
    c = apply_edits(root, split, rejects)
    return c["removed"], c["rewritten"], c["emptied"]


@dataclass(frozen=True)
class FrameMeta:
    """What the manifest knows about a frame that the label file does not."""

    box: tuple | None = None      # normalised (w, h) for a box added here
    url: str | None = None        # deep link to this moment in the video
    kind: str = ""                # "motion" marks a frame with a stone in flight
    neighbours: tuple = ()        # paths to the frames either side, for motion
    note: str = ""


def _card(frame, out_dir, meta=None):
    """One frame's image with its labels as clickable boxes over it."""
    import html as _html
    import shutil

    out_dir = Path(out_dir)
    info = meta or FrameMeta()

    def copy_in(src):
        target = out_dir / "frames" / Path(src).name
        if not target.exists():
            shutil.copyfile(src, target)
        return f"frames/{Path(src).name}"

    name = copy_in(frame.image)
    boxes = []
    for b in frame.boxes:
        style = (f"left:{(b.cx - b.w / 2) * 100:.3f}%;"
                 f"top:{(b.cy - b.h / 2) * 100:.3f}%;"
                 f"width:{b.w * 100:.3f}%;height:{b.h * 100:.3f}%")
        key = _html.escape(reject_key(frame.image.stem, b))
        boxes.append(f'<b class="bx {b.color}" style="{style}" data-k="{key}"'
                     f' title="{b.color} {b.cx:.3f},{b.cy:.3f}"></b>')

    # A box added to an empty frame has nothing to copy a size from, and the
    # set's median is wrong nearly everywhere when it spans 120 videos with 120
    # different scales. The manifest knows the right answer.
    if info.box:
        w, h = info.box
    elif frame.boxes:
        w, h = frame.boxes[0].w, frame.boxes[0].h
    else:
        w, h = 0.074, 0.043

    link = (f' <a class="lnk" href="{_html.escape(info.url)}" target="_blank"'
            f' rel="noopener" title="open the video here">video</a>'
            if info.url else "")
    # A moving stone and a red shoe look much alike in one still, which is the
    # whole reason the flight test exists. Across three frames it is obvious.
    thumbs = ""
    if info.neighbours:
        near = "".join(f'<img src="{copy_in(n)}" loading="lazy" alt="">'
                       for n in info.neighbours)
        thumbs = f'<div class="nb">{near}</div>'
    classes = "motion" if info.kind == "motion" else ""
    empty = " &middot; <b>no labels</b>" if not frame.boxes else ""

    return (f'<figure class="{classes}" data-stem="{_html.escape(frame.image.stem)}">'
            f'<figcaption><label><input type="checkbox" class="seen"> done</label> '
            f'<code>{_html.escape(frame.seq)}</code> '
            f'{frame.t:.1f}s &middot; {len(frame.boxes)} labels{empty}{link}</figcaption>'
            f'<div class="wrap" data-stem="{_html.escape(frame.image.stem)}"'
            f' data-w="{w:.6f}" data-h="{h:.6f}">'
            f'<img src="{name}" loading="lazy" alt="">'
            + "".join(boxes) + "</div>" + thumbs + "</figure>")


_CLICK_CSS = """
  :root { --bg:#f7f7f5; --line:#ddd; --muted:#666; --seen:#2e9e5b; }
  body { background:var(--bg); font:14px/1.5 system-ui, sans-serif;
         margin:0; padding:16px 16px 96px; }
  .top { position:sticky; top:0; background:var(--bg); padding:8px 0 12px;
         border-bottom:1px solid var(--line); z-index:5; }
  h1 { margin:0 0 4px; font-size:18px; }
  .muted { color:var(--muted); }
  a { color:#1a5fb4; }
  /* 300px so a panel renders at about 1:1 -- these crops are ~297 px wide and
     a stone is ~18 px across, so shrinking them is how a missing stone gets
     missed. The reviewer's job is to spot what is not labelled. */
  #grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
          gap:12px; margin-top:12px; }
  figure { margin:0; background:#fff; border:1px solid var(--line);
           border-radius:8px; padding:8px; border-left:4px solid transparent; }
  figure.done { border-left-color:var(--seen); }
  figure.motion { background:#fffdf3; }
  figure.motion figcaption::after { content:" \2708"; color:#b8860b; }
  figcaption { font-size:12px; color:var(--muted); margin-bottom:6px;
               display:flex; gap:6px; align-items:baseline; flex-wrap:nowrap;
               white-space:nowrap; overflow:hidden; }
  figcaption code { overflow:hidden; text-overflow:ellipsis; }
  figcaption label { display:flex; gap:3px; align-items:center; cursor:pointer; }
  .wrap { position:relative; line-height:0; }
  img { width:100%; height:auto; border-radius:4px; }
  .nb { display:flex; gap:4px; margin-top:4px; line-height:0; }
  .nb img { width:calc(50% - 2px); opacity:.75; border-radius:3px; }
  .bx { position:absolute; border:2px solid; cursor:pointer; }
  .bx.red { border-color:#e23b3b; }
  .bx.yellow { border-color:#e8b400; }
  .bx:hover { box-shadow:0 0 0 2px rgba(0,0,0,.35); }
  .bx.out { border-style:dashed; opacity:.45;
            background:repeating-linear-gradient(45deg,
              rgba(0,0,0,.35) 0 3px, transparent 3px 6px); }
  .bx.new { border-width:3px; box-shadow:0 0 0 1px #fff inset; }
  .mode { display:flex; gap:6px; align-items:center; }
  .mode button.on { background:#1a1a1a; color:#fff; border-color:#1a1a1a; }
  .wrap.adding { cursor:crosshair; }
  .bar { position:fixed; left:0; right:0; bottom:0; background:#fff;
         border-top:1px solid var(--line); padding:10px 16px;
         display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  button { font:inherit; padding:6px 12px; border-radius:6px;
           border:1px solid var(--line); background:#fff; cursor:pointer; }
  button.primary { background:#1a1a1a; color:#fff; border-color:#1a1a1a; }
  .pages { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
  .pages a { padding:4px 8px; border:1px solid var(--line); border-radius:6px;
             background:#fff; text-decoration:none; }
  .pages a.full { border-color:var(--seen); color:var(--seen); }
  progress { width:220px; height:14px; }
  table { border-collapse:collapse; margin-top:10px; font-size:13px; }
  td, th { border:1px solid var(--line); padding:4px 10px; text-align:left; }
"""

_CLICK_JS = """
  // Three kinds of state, kept apart: a rejection names an existing box, an
  // addition names a place a stone was and no label is, and "seen" names a
  // frame a person actually looked at. The first two are keyed by frame and
  // centre so they survive the set being rebuilt.
  //
  // Scoped to this dataset and split. Sharing one key across pages let a
  // session on the validation split leak 110 edits into an export meant for
  // the training split -- harmless, since those frames are not in it, but the
  // file then says something it does not mean.
  const SCOPE = document.body.dataset.scope || "default";
  const TOTAL = Number(document.body.dataset.total || 0);
  const RKEY = "curling-label-rejects:" + SCOPE;
  const AKEY = "curling-label-adds:" + SCOPE;
  const SKEY = "curling-label-seen:" + SCOPE;
  const load = (k) => new Set(JSON.parse(localStorage.getItem(k) || "[]"));
  let rejects = load(RKEY), adds = load(AKEY), seen = load(SKEY);
  let mode = "reject";

  // Read-modify-write, not write-the-whole-set. With the review split over ten
  // pages a second open tab would otherwise overwrite whatever the first had
  // done since it loaded.
  function mutate(key, fn) {
    const cur = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
    fn(cur);
    localStorage.setItem(key, JSON.stringify([...cur]));
    return cur;
  }
  function reload() {
    rejects = load(RKEY); adds = load(AKEY); seen = load(SKEY); paint();
  }
  window.addEventListener("storage", (ev) => {
    if (ev.key === RKEY || ev.key === AKEY || ev.key === SKEY) reload();
  });

  function markSeen(stem, on) {
    seen = mutate(SKEY, (s) => (on === false ? s.delete(stem) : s.add(stem)));
  }

  function renderAdds() {
    for (const el of document.querySelectorAll(".bx.new")) el.remove();
    for (const key of adds) {
      const [stem, pos, cls] = key.split("|");
      const wrap = document.querySelector('.wrap[data-stem="' + CSS.escape(stem) + '"]');
      if (!wrap) continue;
      const [cx, cy] = pos.split(",").map(Number);
      const w = Number(wrap.dataset.w), h = Number(wrap.dataset.h);
      const b = document.createElement("b");
      b.className = "bx new " + (cls === "0" ? "red" : "yellow");
      b.style.left = ((cx - w / 2) * 100).toFixed(3) + "%";
      b.style.top = ((cy - h / 2) * 100).toFixed(3) + "%";
      b.style.width = (w * 100).toFixed(3) + "%";
      b.style.height = (h * 100).toFixed(3) + "%";
      b.dataset.add = key;
      b.title = "added " + (cls === "0" ? "red" : "yellow") + " - click to undo";
      wrap.appendChild(b);
    }
  }
  function paint() {
    for (const el of document.querySelectorAll(".bx:not(.new)"))
      el.classList.toggle("out", rejects.has(el.dataset.k));
    renderAdds();
    let onPage = 0;
    const hide = document.getElementById("hide").checked;
    for (const fig of document.querySelectorAll("figure")) {
      const done = seen.has(fig.dataset.stem);
      fig.classList.toggle("done", done);
      const cb = fig.querySelector(".seen");
      if (cb) cb.checked = done;
      fig.hidden = hide && done;
      if (done) onPage++;
    }
    const total = TOTAL || document.querySelectorAll("figure").length;
    document.getElementById("count").textContent =
      seen.size + " / " + total + " reviewed \u00b7 " +
      rejects.size + " rejected \u00b7 " + adds.size + " added";
    const bar = document.getElementById("prog");
    if (bar) { bar.max = total; bar.value = seen.size; }
    const pg = document.getElementById("pagedone");
    if (pg) pg.textContent = onPage + "/" +
      document.querySelectorAll("figure").length + " on this page";
    for (const w of document.querySelectorAll(".wrap"))
      w.classList.toggle("adding", mode !== "reject");
  }
  function setMode(m) {
    mode = m;
    for (const b of document.querySelectorAll(".mode button"))
      b.classList.toggle("on", b.dataset.mode === m);
    paint();
  }

  document.getElementById("grid").addEventListener("click", (ev) => {
    const cb = ev.target.closest(".seen");
    if (cb) { markSeen(cb.closest("figure").dataset.stem, cb.checked); paint(); return; }
    if (ev.target.closest(".lnk")) return;
    const fig = ev.target.closest("figure");
    const added = ev.target.closest(".bx.new");
    if (added) {
      adds = mutate(AKEY, (s) => s.delete(added.dataset.add));
      if (fig) markSeen(fig.dataset.stem);
      paint(); return;
    }
    const boxEl = ev.target.closest(".bx");
    if (boxEl) {
      const k = boxEl.dataset.k;
      rejects = mutate(RKEY, (s) => (s.has(k) ? s.delete(k) : s.add(k)));
      // An edit is proof the frame was looked at.
      if (fig) markSeen(fig.dataset.stem);
      paint(); return;
    }
    if (mode === "reject") return;
    const wrap = ev.target.closest(".wrap");
    if (!wrap) return;
    const r = wrap.getBoundingClientRect();
    const cx = (ev.clientX - r.left) / r.width;
    const cy = (ev.clientY - r.top) / r.height;
    if (cx < 0 || cx > 1 || cy < 0 || cy > 1) return;
    const cls = mode === "red" ? "0" : "1";
    const key = wrap.dataset.stem + "|" + cx.toFixed(3) + "," + cy.toFixed(3)
                + "|" + cls;
    adds = mutate(AKEY, (s) => s.add(key));
    if (fig) markSeen(fig.dataset.stem);
    paint();
  });

  for (const b of document.querySelectorAll(".mode button"))
    b.addEventListener("click", () => setMode(b.dataset.mode));
  document.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea")) return;
    if (ev.key === "r") setMode("red");
    else if (ev.key === "y") setMode("yellow");
    else if (ev.key === "Escape") setMode("reject");
  });

  // The throughput lever. Most frames are right, so the common action has to
  // be one click for the page, not one click per frame.
  document.getElementById("allseen").addEventListener("click", () => {
    seen = mutate(SKEY, (s) => {
      for (const f of document.querySelectorAll("figure")) s.add(f.dataset.stem);
    });
    paint();
  });
  document.getElementById("hide").addEventListener("change", paint);

  document.getElementById("dl").addEventListener("click", () => {
    const payload = {scope: SCOPE, reject: [...rejects], add: [...adds],
                     reviewed: [...seen]};
    const blob = new Blob([JSON.stringify(payload, null, 1)],
                          {type: "application/json"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    // Stamped, because ~/Downloads/label-edits.json quietly overwriting itself
    // is how one session's work gets attributed to another split.
    const now = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 13);
    a.download = "label-edits-" + SCOPE.replace(/[^A-Za-z0-9]+/g, "-")
                 + "-" + now + ".json";
    a.click();
  });
  document.getElementById("clear").addEventListener("click", () => {
    if (confirm("Forget every rejection, addition and tick for " + SCOPE + "?")) {
      localStorage.removeItem(RKEY);
      localStorage.removeItem(AKEY);
      localStorage.removeItem(SKEY);
      reload();
    }
  });
  setMode("reject");
  paint();
"""

_HELP = (
    "Click a box to reject that label; click again to keep it. To add one, pick"
    " a colour below (or press <b>r</b>/<b>y</b>, <b>Esc</b> to stop) and click"
    " where the stone is; click an added box to undo it. Tick <b>done</b> when"
    " you have looked at a frame -- editing one ticks it for you, and"
    " <b>mark page done</b> ticks the rest. Only frames you have ticked go into"
    " the set. Nothing changes on disk until you export and run"
    " <code>curling-score labels --apply</code>. Your choices live in this"
    " browser, so you can come back to it."
)


def _bar(nav: str = "") -> str:
    return (
        '<div class="bar">\n  <b id="count">0 reviewed</b>\n'
        '  <progress id="prog" value="0" max="1"></progress>\n'
        '  <span class="muted" id="pagedone"></span>\n'
        '  <span class="mode">add:'
        ' <button data-mode="reject">off</button>'
        ' <button data-mode="red">red</button>'
        ' <button data-mode="yellow">yellow</button></span>\n'
        '  <button id="allseen">mark page done</button>\n'
        '  <label><input type="checkbox" id="hide"> hide done</label>\n'
        '  <button class="primary" id="dl">Export edits</button>\n'
        '  <button id="clear">Clear all</button>\n'
        f"  {nav}\n</div>\n"
    )


def _page_html(title, scope, total, cards, heading, nav="", extra=""):
    import html as _html

    return (
        '<!doctype html>\n<meta charset="utf-8">\n'
        f"<title>{_html.escape(title)}</title>\n"
        '<link rel="stylesheet" href="review.css">\n'
        f'<body data-scope="{_html.escape(scope)}" data-total="{total}">\n'
        '<div class="top">\n'
        f"  <h1>{_html.escape(title)}</h1>\n"
        f'  <div class="muted">{heading}</div>\n{extra}'
        "</div>\n"
        f'<div id="grid">\n{cards}\n</div>\n'
        + _bar(nav)
        + '<script src="review.js"></script>\n'
    )


def render_clickable(frames_to_show, out_dir, title="Label fixing",
                     scope="default", per_page: int = 200, meta=None):
    """A page where every label is a box you can click to reject.

    The boxes are HTML positioned over the image rather than drawn into it, so
    a click lands on one specific label. Edits live in the browser and are
    exported as JSON for `curling-score labels --apply` to act on, so nothing
    changes on disk until asked.

    This exists because the static rules kept producing partial fixes. Each one
    written -- the quiet-span test, the play span, the flight rule, the edge
    clipping -- removed some bad labels and left others, and two of them removed
    good labels as well. A person tells a stone from a knee at a glance.

    Above ``per_page`` frames the review is split across pages with a contents
    page in front, because a two-thousand-frame set is one 2.5 MB document with
    eight thousand absolutely-positioned children otherwise. All pages share one
    set of storage keys, so an export from any of them holds the whole session.

    ``meta`` maps a frame stem to a :class:`FrameMeta`: the box size to give an
    added stone, a deep link to the moment in the video, and for a frame with a
    stone in flight, the frames either side.
    """
    import html as _html

    out_dir = Path(out_dir)
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)
    (out_dir / "review.css").write_text(_CLICK_CSS)
    (out_dir / "review.js").write_text(_CLICK_JS)

    frames = list(frames_to_show)
    total = len(frames)
    meta = meta or {}

    if total <= per_page:
        cards = "\n".join(_card(f, out_dir, meta.get(f.image.stem)) for f in frames)
        page = out_dir / "index.html"
        page.write_text(_page_html(title, scope, total, cards, _HELP))
        return page, total

    chunks = [frames[i:i + per_page] for i in range(0, total, per_page)]
    names = [f"page-{i + 1:04d}.html" for i in range(len(chunks))]
    for i, (chunk, name) in enumerate(zip(chunks, names)):
        cards = "\n".join(_card(f, out_dir, meta.get(f.image.stem)) for f in chunk)
        nav = '<span class="muted"><a href="index.html">contents</a>'
        if i:
            nav += f' &middot; <a href="{names[i - 1]}">prev</a>'
        if i + 1 < len(names):
            nav += f' &middot; <a href="{names[i + 1]}">next</a>'
        nav += "</span>"
        first, last = i * per_page + 1, i * per_page + len(chunk)
        head = f"Frames {first}-{last} of {total}. {_HELP}"
        (out_dir / name).write_text(
            _page_html(f"{title} \u2014 page {i + 1}", scope, total, cards, head, nav))

    rows = []
    for i, (chunk, name) in enumerate(zip(chunks, names)):
        first, last = i * per_page + 1, i * per_page + len(chunk)
        stems = ",".join(_html.escape(f.image.stem) for f in chunk)
        rows.append(f'<tr><td><a href="{name}">page {i + 1}</a></td>'
                    f"<td>frames {first}-{last}</td>"
                    f'<td class="pd" data-stems="{stems}">-</td></tr>')
    index = out_dir / "index.html"
    index.write_text(
        '<!doctype html>\n<meta charset="utf-8">\n'
        f"<title>{_html.escape(title)}</title>\n"
        '<link rel="stylesheet" href="review.css">\n'
        f'<body data-scope="{_html.escape(scope)}" data-total="{total}">\n'
        '<div class="top">\n'
        f"  <h1>{_html.escape(title)}</h1>\n"
        f'  <div class="muted">{total} frames over {len(chunks)} pages. {_HELP}'
        "</div>\n</div>\n"
        '<p><progress id="prog" value="0" max="1"></progress> '
        '<b id="count">0 reviewed</b></p>\n'
        f"<table><tr><th>page</th><th>frames</th><th>reviewed</th></tr>\n"
        + "\n".join(rows) + "\n</table>\n"
        "<script>\n"
        '  const SCOPE = document.body.dataset.scope || "default";\n'
        '  const TOTAL = Number(document.body.dataset.total || 0);\n'
        '  const SKEY = "curling-label-seen:" + SCOPE;\n'
        "  function refresh() {\n"
        '    const seen = new Set(JSON.parse(localStorage.getItem(SKEY) || "[]"));\n'
        '    for (const td of document.querySelectorAll(".pd")) {\n'
        '      const stems = td.dataset.stems.split(",");\n'
        "      const n = stems.filter((s) => seen.has(s)).length;\n"
        '      td.textContent = n + " / " + stems.length;\n'
        '      td.closest("tr").querySelector("a").classList\n'
        '        .toggle("full", n === stems.length);\n'
        "    }\n"
        '    const bar = document.getElementById("prog");\n'
        "    bar.max = TOTAL; bar.value = seen.size;\n"
        '    document.getElementById("count").textContent =\n'
        '      seen.size + " / " + TOTAL + " reviewed";\n'
        "  }\n"
        '  window.addEventListener("storage", refresh);\n'
        '  window.addEventListener("focus", refresh);\n'
        "  refresh();\n"
        "</script>\n"
    )
    return index, total
