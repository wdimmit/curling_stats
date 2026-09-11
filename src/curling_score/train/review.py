"""A contact sheet for reviewing suspected missed deliveries.

Delivery detection finds roughly 60% of an end's stones, and improving it needs
ground truth we do not have. This turns the constraint checks into something a
person can actually work through: each suspected miss becomes one strip of
frames spanning the window, with a link into the video at that moment.

Reviewing a four-hour stream is impossible; reviewing forty short windows is an
evening.
"""

import html as _html

import math

import cv2
import numpy as np

LABEL_H = 18


def filmstrip_times(start_s, end_s, count=8):
    """Times spread across a window, without duplicates."""
    if end_s <= start_s:
        return [start_s]
    span = end_s - start_s
    # Never ask for frames closer together than the video can distinguish.
    count = max(2, min(count, int(span * 4) or 2))
    step = span / (count - 1)
    seen, out = set(), []
    for i in range(count):
        t = round(start_s + i * step, 2)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# A stone's whole flight, from crossing into the panel to coming to rest,
# lasts under ten seconds. Frames further apart than this cannot show the
# delivery happening at all -- only that the house differs either side of the
# gap -- so the window that decides where to look is worth splitting rather
# than stretching. A "long gap" window ran to 227 s, which at nine frames is
# 28 s a frame.
MAX_SPACING_S = 14.0
# A delivery is only visible while it travels, which is roughly three to five
# seconds from the hog line to rest. Sampling a window at 14 s -- or even at
# the 3.6 s a 30 s window used to give -- can put the entire delivery between
# two frames, which is how a strip built to answer "was a delivery missed here"
# ends up unable to show one. Measured on g1e4: red absent at 3151.6, already
# at rest at 3155.1. So windows that have to show motion are sampled fine
# enough to catch a stone at least twice in flight.
FLIGHT_MAX_SPACING_S = 1.5
# Kinds whose whole question is whether a stone moved through the window. A
# score window only has to show the house at rest, so it stays coarse and
# cheap.
FLIGHT_KINDS = ("miss", "suspect", "appear", "conflict")


def split_window(start_s, end_s, frames_per_window: int,
                 max_spacing_s: float = MAX_SPACING_S):
    """Consecutive sub-windows, none sampled more coarsely than the limit."""
    span = end_s - start_s
    reach = max_spacing_s * (frames_per_window - 1)
    if span <= reach or reach <= 0:
        return [(start_s, end_s)]
    n = int(span / reach) + 1
    step = span / n
    return [(start_s + i * step, start_s + (i + 1) * step) for i in range(n)]


# Drawn over a detection so the blob underneath stays visible: an outline
# rather than a filled marker, and the label off to the side.
MARK_RING_PX = 9
MARK_BGR = {"red": (60, 60, 235), "yellow": (40, 200, 235)}
# Floor on the drawn box so a tiny detection is still visible at strip scale.
MARK_MIN_SIDE_PX = 7


def annotate(frame, detections, calib):
    """Box every detection, so what the model saw can be looked at directly.

    The model was trained on labels the classical colour detector produced, so
    it inherited that detector's idea of what a stone looks like -- including
    anything else on the ice in team colours. Seeing the boxes is the only way
    to tell a stone from a red shoe.

    The box is drawn at the detection's own area, but do not read anything into
    its size. Training labels all take one box per frame from
    ``box_px_for``, which returns ``2 * STONE_RADIUS_M * px_per_m`` and ignores
    the detection entirely, so the model learned to emit a near-constant box:
    measured over one window, real stones and false positives alike came out
    21.2-22.3 px on a side. Position, motion and confidence separate them;
    size does not.
    """
    out = frame.copy()
    for d in detections:
        px, py = calib.to_pixels(d.x_m, d.y_m)
        px, py = int(round(px)), int(round(py))
        colour = MARK_BGR.get(d.color, (255, 255, 255))
        # area_px is w*h for a model box, so its square root is the side of an
        # equal-area square -- the honest single number for "how big was this".
        side = max(MARK_MIN_SIDE_PX, int(round(math.sqrt(max(d.area_px, 1.0)))))
        half = side // 2
        cv2.rectangle(out, (px - half, py - half), (px + half, py + half),
                      colour, 1, cv2.LINE_AA)
        cv2.putText(out, f"{d.confidence:.2f}", (px + half + 2, py + 3),
                    cv2.FONT_HERSHEY_PLAIN, 0.6, colour, 1, cv2.LINE_AA)
    return out


def contact_sheet(frames, labels):
    """Tile frames left to right with a caption under each."""
    if not frames:
        return None
    h = max(f.shape[0] for f in frames)
    tiles = []
    for frame, label in zip(frames, labels):
        pad = np.zeros((h + LABEL_H, frame.shape[1], 3), np.uint8)
        pad[: frame.shape[0], : frame.shape[1]] = frame
        cv2.putText(pad, str(label), (3, h + 13), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(pad)
    return np.hstack(tiles)


def render_html(rows, video_id):
    """A single page listing every window worth a look."""
    items = []
    for i, r in enumerate(rows, 1):
        kind = r.get("kind", "miss")
        colour = r.get("expected_color")
        if kind == "suspect":
            expect = f'<span class="pill {colour}">{colour} &mdash; real throw?</span>'
        elif kind == "score":
            expect = (
                f'<span class="pill {colour}">last stone was {colour}</span>'
                if colour else ""
            )
        elif kind == "appear":
            expect = (
                f'<span class="pill {colour}">was a {colour} thrown here?</span>'
                if colour else ""
            )
        elif kind == "conflict":
            expect = (
                f'<span class="pill {colour}">two {colour}s, one stone</span>'
                if colour else ""
            )
        else:
            expect = (
                f'<span class="pill {colour}">expect {colour}</span>'
                if colour else ""
            )
        t = int(r["start_s"])
        items.append(
            f'''<article>
  <header>
    <b>#{i}</b>
    <code class="wid">{_html.escape(str(r.get("wid", "")))}</code>
    <span class="muted">game {r["game"]} &middot; end {r["end"]} &middot; {r["house"]} house</span>
    <span class="pill reason {kind}">{_html.escape(r["reason"])}</span>
    {f'<span class="muted">part {r["part"]}</span>' if r.get("part") else ""}
    {expect}
    <span class="muted">{"trust" if kind == "score" else "confidence"} {r["confidence"]:.2f}</span>
    <span class="grow"></span>
    <span class="muted">{r["start_s"]:.0f}&ndash;{r["end_s"]:.0f}s
      ({r["end_s"] - r["start_s"]:.0f}s)</span>
    <a href="https://youtu.be/{video_id}?t={t}" target="_blank" rel="noopener">watch &rarr;</a>
  </header>
  {f'<div class="muted note">{_html.escape(r["note"])}</div>' if r.get("note") else ""}
  <img src="{_html.escape(r["image"])}" loading="lazy" alt="frames {r["start_s"]:.0f}s">
  <label><input type="checkbox" data-i="{i}"> {
        "this is a real delivery" if kind == "suspect"
        else "the house here matches the score" if kind == "score"
        else "a stone really was delivered here" if kind == "appear"
        else "the dropped candidate was the real stone" if kind == "conflict"
        else "there is a delivery here"}</label>
</article>'''
        )
    body = "\n".join(items) or "<p>Nothing flagged.</p>"
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Suspected missed deliveries</title>
<style>
  :root {{ --bg:#f7f7f5; --panel:#fff; --ink:#1a1a1a; --muted:#6b6b6b;
           --line:#e0e0dc; --accent:#2b6cb0; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#16171a; --panel:#1e2024; --ink:#ececec; --muted:#9aa0a6;
    --line:#31343a; --accent:#6ea8fe; }} }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
          font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  h1 {{ font-size:17px; margin:0; }}
  .top {{ padding:14px 20px; border-bottom:1px solid var(--line);
          background:var(--panel); position:sticky; top:0; }}
  article {{ background:var(--panel); border:1px solid var(--line);
             border-radius:10px; margin:16px 20px; padding:12px; }}
  header {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap;
            margin-bottom:8px; }}
  .grow {{ flex:1 1 auto; }}
  .muted {{ color:var(--muted); }}
  .pill {{ padding:2px 8px; border-radius:99px; font-size:12px;
           border:1px solid var(--line); }}
  .pill.suspect {{ background:#b45309; color:#fff; border-color:transparent; }}
  .pill.score {{ background:#2b6cb0; color:#fff; border-color:transparent; }}
  .pill.conflict {{ background:#7c3aed; color:#fff; border-color:transparent; }}
  .pill.appear {{ background:#0f766e; color:#fff; border-color:transparent; }}
  .note {{ margin-bottom:6px; font-size:13px; }}
  .wid {{ font-size:12px; color:var(--muted); background:rgba(0,0,0,.06);
          padding:1px 6px; border-radius:4px; }}
  .pill.red {{ background:#d13438; color:#fff; border-color:transparent; }}
  .pill.yellow {{ background:#e8b400; color:#1a1a1a; border-color:transparent; }}
  img {{ width:100%; height:auto; display:block; border-radius:6px;
         image-rendering:crisp-edges; }}
  label {{ display:inline-block; margin-top:8px; color:var(--muted); }}
  a {{ color:var(--accent); }}
</style>
<div class="top">
  <h1>Review &mdash; {len(rows)} window(s)</h1>
  <div class="muted">Each strip spans one window, oldest frame first. A stone
    moving down the sheet across the strip is a delivery.
    <b>score</b> windows are the house each end's score was read from &mdash;
    check it matches, and that no stone was thrown after it. These carry
    <b>trust</b> rather than confidence, and the least trusted come first;
    <b>miss</b> windows are places a delivery is probably missing;
    <b>suspect</b> windows are detections that probably are not real throws;
    <b>conflict</b> windows hold two candidates of one colour where the rules
    allow only one &mdash; both are on screen, so say if the wrong one was
    dropped;
    <b>appear</b> windows are stones believed thrown without any flight being
    seen &mdash; the only way to catch a guard thrown short, and the only way a
    stone parked at the delivery end gets mistaken for a throw.
    Tick what you confirm &mdash; ticks are kept in this browser. Refer to a
    window by the grey name next to its number (<code>g1e5@4196</code>) rather
    than by the number itself, which moves as detection improves.</div>
</div>
{body}
<script>
  const KEY = "curling-misses";
  const saved = JSON.parse(localStorage.getItem(KEY) || "{{}}");
  for (const box of document.querySelectorAll("input[type=checkbox]")) {{
    box.checked = !!saved[box.dataset.i];
    box.onchange = () => {{
      saved[box.dataset.i] = box.checked;
      localStorage.setItem(KEY, JSON.stringify(saved));
    }};
  }}
</script>
"""


def build(video_path, setups, rows, out_dir, video_id, frames_per_window=9,
          progress=None, detector=None):
    """Render a contact sheet per window plus an index page.

    ``rows`` are miss candidates carrying ``game``/``end``/``house`` and the
    window bounds; ``setups`` maps house name to its panel crop.

    Pass ``detector`` to box every detection on each frame. Without it the
    strips show bare ice, which says where to look but not what the model
    thought it saw -- and the difference between a missed stone and a stone
    detected in the wrong place is invisible.
    """
    from pathlib import Path

    from curling_score.ingest import frames as F

    out_dir = Path(out_dir)
    (out_dir / "strips").mkdir(parents=True, exist_ok=True)

    # Most likely problem first, so #1 is worth someone's attention and the
    # tail can be abandoned without missing the important ones. Parts of one
    # window stay together because the split happens after the sort.
    #
    # Sorted on `priority`, not on confidence, because confidence does not mean
    # the same thing for every kind. For a miss, a suspect or a conflict a high
    # number means "there is probably something wrong here"; for a score window
    # it means the opposite -- that the score can be trusted. Sorting those
    # together put the most reliable scores at the top of a list of problems.
    rows = sorted(rows, key=lambda r: (-float(r.get("priority",
                                                    r.get("confidence", 0.0))),
                                       r.get("game", 0), r.get("end", 0),
                                       r.get("start_s", 0.0)))

    # Split next, so a long window becomes several legible strips rather than
    # one whose frames are too far apart to show a delivery.
    expanded = []
    for r in rows:
        spacing = (FLIGHT_MAX_SPACING_S if r.get("kind") in FLIGHT_KINDS
                   else MAX_SPACING_S)
        parts = split_window(r["start_s"], r["end_s"], frames_per_window,
                             max_spacing_s=spacing)
        for k, (lo, hi) in enumerate(parts, 1):
            row = {**r, "start_s": round(lo, 1), "end_s": round(hi, 1)}
            if len(parts) > 1:
                row["part"] = f"{k} of {len(parts)}"
            expanded.append(row)
    rows = expanded

    done = []
    total = len(rows)
    for i, r in enumerate(rows, 1):
        # Rendering is the long phase -- one decode pass per window -- and it
        # says nothing while it works, which reads as a hang.
        if progress and (i == 1 or i % 20 == 0 or i == total):
            progress(f"  rendering strip {i}/{total}")
        setup = setups[r["house"]]
        # Named for the window it shows, not its position in the list, so a
        # rebuild reuses what it already rendered. Decoding the frames is the
        # slow part by a wide margin, and the reasons and ordering shown
        # alongside them get revised far more often than the frames do.
        # A name for the window itself, so it can be referred to across
        # rebuilds. Position on the page is not usable for that: the ordering
        # is by priority and shifts whenever detection changes, so "#3" meant
        # two different windows an hour apart.
        wid = f"g{r.get('game', 0)}e{r.get('end', 0)}@{int(r['start_s'])}"
        r = {**r, "wid": wid}
        name = (f"strips/g{r.get('game', 0)}e{r.get('end', 0)}"
                f"-{int(r['start_s'])}-{int(r['end_s'])}.jpg")
        if (out_dir / name).exists():
            done.append({**r, "image": name})
            continue
        times = filmstrip_times(r["start_s"], r["end_s"], frames_per_window)
        picked, labels = [], []
        lo, hi = times[0], times[-1] + 0.2
        wanted = list(times)
        for t, img in F.window(video_path, lo, hi, 10.0, crop=setup.rect):
            if wanted and t >= wanted[0] - 0.05:
                picked.append(img)
                labels.append(f"{t:.1f}s")
                wanted.pop(0)
            if not wanted:
                break
        if detector is not None and picked:
            # One batch per window rather than a call per frame: the model is
            # far faster given several panels at once.
            per_frame = detector.find_stones_batch(picked, setup.calib)
            picked = [annotate(img, dets, setup.calib)
                      for img, dets in zip(picked, per_frame)]
            labels = [f"{lab}  {sum(1 for d in ds if d.color == 'red')}R"
                      f"/{sum(1 for d in ds if d.color == 'yellow')}Y"
                      for lab, ds in zip(labels, per_frame)]
        sheet = contact_sheet(picked, labels)
        if sheet is None:
            continue
        cv2.imwrite(str(out_dir / name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
        done.append({**r, "image": name})

    (out_dir / "index.html").write_text(render_html(done, video_id))
    return out_dir / "index.html", len(done)
