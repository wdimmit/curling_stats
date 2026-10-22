/* The members' labelling page's arithmetic: boxing stones on a still (where a
 * click lands, what a dragged corner may do) and marking the release on a
 * strip of frames (below), and the answers the server will take
 * (service/labelling.py check_answer). No DOM, so node tests it.
 *
 * The state is the boxes on the picture and one status per start the
 * computer proposed: untouched, kept, changed or deleted. A box that began as
 * a start carries its index in `start`; one the member drew has null. The
 * computer was unsure of exactly one start (`flagged`), and the member has to
 * say what they think of that one before they can finish. Every function
 * returns a new state; none changes the one it is given. */

// Labelling wants a mouse and a keyboard, and a picture big enough to box in.
export const MIN_WIDTH_PX = 1000;
// The server's smallest box edge (labelling.py MIN_BOX_PX).
export const MIN_BOX_PX = 3;
export const CANT_TELL = { cant_tell: true };

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const tenth = v => Math.round(v * 10) / 10;

/* The state for a fresh item: its starts as boxes, none acted on. */
export function startBoxes(item) {
  const boxes = item.starts.map((s, i) => ({
    x0: s.x0, y0: s.y0, x1: s.x1, y1: s.y1,
    color: s.color === "yellow" ? "yellow" : "red", start: i }));
  return { boxes, starts: item.starts.map(() => "untouched"),
           flagged: item.starts.findIndex(s => s.flagged) };
}

/* The index of the topmost box (the last drawn) holding the point, allowing
 * `pad` of slack all round, or -1. */
export function boxAt(state, x, y, pad = 0) {
  for (let i = state.boxes.length - 1; i >= 0; i--) {
    const b = state.boxes[i];
    if (x >= b.x0 - pad && x <= b.x1 + pad && y >= b.y0 - pad && y <= b.y1 + pad) return i;
  }
  return -1;
}

const CORNERS = { nw: ["x0", "y0"], ne: ["x1", "y0"], sw: ["x0", "y1"], se: ["x1", "y1"] };

/* The corner of a box within `pad` of the point, topmost box first:
 * {i, corner}, or null. A box smaller than pad on a side still has all four. */
export function cornerAt(state, x, y, pad) {
  for (let i = state.boxes.length - 1; i >= 0; i--) {
    const b = state.boxes[i];
    let best = null, bestD = pad * pad;
    for (const [corner, [kx, ky]] of Object.entries(CORNERS)) {
      const d = (b[kx] - x) ** 2 + (b[ky] - y) ** 2;
      if (d <= bestD) { best = { i, corner }; bestD = d; }
    }
    if (best) return best;
  }
  return null;
}

/* The box with one corner dragged to (x, y): kept on the image, and at least
 * MIN_BOX_PX from the opposite corner rather than turned inside out. */
export function moveCorner(box, corner, x, y, w, h) {
  const [kx, ky] = CORNERS[corner];
  const out = { ...box };
  const ox = kx === "x0" ? box.x1 : box.x0, oy = ky === "y0" ? box.y1 : box.y0;
  out[kx] = kx === "x0" ? clamp(x, 0, ox - MIN_BOX_PX) : clamp(x, ox + MIN_BOX_PX, w);
  out[ky] = ky === "y0" ? clamp(y, 0, oy - MIN_BOX_PX) : clamp(y, oy + MIN_BOX_PX, h);
  return out;
}

/* The box shifted by (dx, dy), stopping at the image's edge with its size kept. */
export function moveBox(box, dx, dy, w, h) {
  const bw = box.x1 - box.x0, bh = box.y1 - box.y0;
  const x0 = clamp(box.x0 + dx, 0, w - bw), y0 = clamp(box.y0 + dy, 0, h - bh);
  return { ...box, x0, y0, x1: x0 + bw, y1: y0 + bh };
}

/* A box dragged out on empty ice from one point to another, or null if it
 * would be under MIN_BOX_PX either way (a click, not a box). */
export function drawnBox(ax, ay, bx, by, w, h, color) {
  const x0 = clamp(Math.min(ax, bx), 0, w), x1 = clamp(Math.max(ax, bx), 0, w);
  const y0 = clamp(Math.min(ay, by), 0, h), y1 = clamp(Math.max(ay, by), 0, h);
  if (x1 - x0 < MIN_BOX_PX || y1 - y0 < MIN_BOX_PX) return null;
  return { x0, y0, x1, y1, color, start: null };
}

/* How big to draw the picture: up to twice its own size, less to fit. */
export function drawScale(w, h, availW, availH) {
  return Math.min(2, availW / w, availH / h);
}

const same = (a, b) => a.x0 === b.x0 && a.y0 === b.y0 && a.x1 === b.x1 && a.y1 === b.y1
                       && a.color === b.color;

/* Box i replaced by `box`. A starting box that is not where it was, or not
 * the colour it was, has been changed -- unless the member already said more
 * than "untouched" about it. */
export function setBox(state, i, box) {
  const boxes = state.boxes.map((b, j) => (j === i ? { ...box, start: b.start } : b));
  const was = state.boxes[i];
  const start = was.start;
  const open = start != null && ["untouched", "kept"].includes(state.starts[start]);
  if (!open || same(was, box)) return { ...state, boxes };
  const starts = state.starts.slice();
  starts[start] = "changed";
  return { ...state, boxes, starts };
}

export function setColour(state, i, color) {
  return setBox(state, i, { ...state.boxes[i], color });
}

/* Box i gone. Its start, if it had one, is deleted. */
export function removeBox(state, i) {
  const start = state.boxes[i].start;
  const boxes = state.boxes.filter((_, j) => j !== i);
  if (start == null) return { ...state, boxes };
  const starts = state.starts.slice();
  starts[start] = "deleted";
  return { ...state, boxes, starts };
}

/* "That one is right": the flagged start kept, if nothing else was said of it. */
export function keepFlagged(state) {
  if (state.flagged < 0 || state.starts[state.flagged] !== "untouched") return state;
  const starts = state.starts.slice();
  starts[state.flagged] = "kept";
  return { ...state, starts };
}

/* A box the member drew. It belongs to no start whatever it says. */
export function addBox(state, box) {
  return { ...state, boxes: [...state.boxes, { ...box, start: null }] };
}

export function canFinish(state) {
  return state.flagged < 0 || state.starts[state.flagged] !== "untouched";
}

/* What to send: a tenth of a pixel is far finer than anyone aims. */
export function boxAnswer(state) {
  return {
    cant_tell: false,
    boxes: state.boxes.map(b => ({ x0: tenth(b.x0), y0: tenth(b.y0), x1: tenth(b.x1),
                                   y1: tenth(b.y1), color: b.color, start: b.start })),
    starts: state.starts.slice(),
  };
}

/* ------------------------------------------------------- release marks
 *
 * A release item is one sprite JPEG of `n` frames laid out `cols` across, each
 * fw x fh. The member steps through them and says which is the first frame
 * with the hand clearly off the handle. Nothing the pipeline read (`o`, `yp`)
 * is used here: the member must not be led. */

export const RELEASE_MARKS = ["off", "held", "cant_tell"];

/* Where frame i sits in the sprite, in pixels from its top left. The page
 * draws it by sliding the sprite the other way (background-position). */
export function spriteOffset(item, i) {
  return { x: (i % item.cols) * item.fw, y: Math.floor(i / item.cols) * item.fh };
}

/* Frame i moved by delta, kept inside the n frames there are. */
export function step(i, delta, n) {
  return clamp(i + delta, 0, n - 1);
}

/* What to send: only "off" names a frame. */
export function releaseAnswer(mark, frame) {
  return { mark, frame: mark === "off" ? frame : null };
}

/* ------------------------------------------------------- a refused answer
 *
 * The server says 409 with a reason: "held" (someone else has the item on a
 * lease now) or "full" / "closed" (it is finished). Only the first is not
 * "finished", and the member is told which. */
export function conflictNotice(detail) {
  return detail === "held" ? "Someone else has this one now."
                           : "Someone else finished this one.";
}
