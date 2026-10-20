/* The house on screen, saved as a PNG from the ⋯ menu.
 *
 * The live <svg id="house"> is cloned rather than drawn again, so the picture
 * is exactly what the page shows -- the stones as charted, and the track, the
 * ghosts and the broom when the track is on -- whichever tab is open on the
 * phone. Only the frame is the picture's own: everything in play, back line
 * to hog line, as the phone's House tab shows it, never the charting crop.
 */
import { PAINT, houseViewBox } from "../core/index.mjs";

const WIDTH = 1080;            // px: sharp on a phone, and what chat apps keep
const PAD = 40;
const BG = "#ece8de";          // a frame the near-white ice reads against
const MUTED = "#6b6556";
const FONT = '"Source Sans 3", system-ui, -apple-system, "Segoe UI", sans-serif';

/* The sheet as a standalone SVG document. Tooltips and the editor's selection
 * ring are the page's, not the picture's. The font is named here because a
 * standalone SVG inherits none, and would set "STATE UNKNOWN" in serif. */
function sheetSvg(svg, viewBox, width, height) {
  const c = svg.cloneNode(true);
  c.removeAttribute("id");
  c.removeAttribute("style");
  c.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  c.setAttribute("viewBox", viewBox);
  c.setAttribute("width", width);
  c.setAttribute("height", height);
  c.setAttribute("font-family", FONT);
  c.querySelectorAll("title, .selring").forEach(n => n.remove());
  return new XMLSerializer().serializeToString(c);
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("the house did not draw"));
    img.src = src;
  });
}

/* One line of the caption, made smaller until it fits: a long pair of team
 * names shrinks rather than runs off the edge. */
function fitText(ctx, text, weight, size, min, maxWidth) {
  let s = size;
  ctx.font = `${weight} ${s}px ${FONT}`;
  while (s > min && ctx.measureText(text).width > maxWidth) {
    s -= 2;
    ctx.font = `${weight} ${s}px ${FONT}`;
  }
}

/* The PNG: the caption's lines over the sheet. */
export async function houseImage(svg, { title, place }) {
  const vb = houseViewBox("inplay");
  const [, , vw, vh] = vb.split(" ").map(Number);
  const sheetH = Math.round(WIDTH * vh / vw);
  const lines = [
    title && { text: title, weight: 700, size: 46, color: PAINT.accent },
    place && { text: place, weight: 600, size: 36, color: MUTED },
  ].filter(Boolean);
  const head = lines.length
    ? PAD + lines.reduce((h, l) => h + l.size, 0) + 10 * (lines.length - 1)
    : 0;

  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = head + sheetH;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.textBaseline = "top";
  let y = PAD;
  for (const l of lines) {
    fitText(ctx, l.text, l.weight, l.size, 24, WIDTH - 2 * PAD);
    ctx.fillStyle = l.color;
    ctx.fillText(l.text, PAD, y, WIDTH - 2 * PAD);
    y += l.size + 10;
  }

  const src = "data:image/svg+xml;charset=utf-8,"
    + encodeURIComponent(sheetSvg(svg, vb, WIDTH, sheetH));
  ctx.drawImage(await loadImage(src), 0, head, WIDTH, sheetH);
  return new Promise((resolve, reject) => canvas.toBlob(
    b => (b ? resolve(b) : reject(new Error("no PNG"))), "image/png"));
}

/* An iPhone or iPad, where a download lands in Files and the share sheet is
 * the way to Photos ("Save Image"). iPadOS asks as a Mac, so the touch points
 * tell. */
function apple() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent)
    || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
}

/* Kept as the phone keeps pictures. Everywhere else, a download: Android
 * files it under Downloads, where the gallery finds it, and its share sheet
 * has no plain "save". A share sheet the page may not open -- too long after
 * the tap for Safari -- falls back to the download too. */
export async function keepImage(blob, name) {
  const file = new File([blob], name, { type: "image/png" });
  if (apple() && navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file] });
      return;
    } catch (e) {
      if (e?.name === "AbortError") return;     // closed without choosing
    }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60_000);
}
