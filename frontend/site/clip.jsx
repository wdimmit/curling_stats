/* A clip of one throw with the broom and lines drawn over it -- the practice
 * pages' and My shots'. `src` is the API path that answers with a short-lived
 * link ({url}); `overlay` is the worker's per-piece list (practice/overlay.py).
 * A `burned` clip has its marks in its own pixels (game/clipburn.py), so a
 * saved copy keeps them: nothing is drawn over it, and they cannot be hidden.
 * A link that has expired under an open page is asked for once more. */
import { useEffect, useRef, useState } from "react";
import { authedFetch } from "./auth.js";
import { dotsShown, linkFresh, overlayAt } from "../core/practice.mjs";

const LINES_KEY = "practice.lines";
// A slide dot's kind (before or after the release) to the line it lies on.
const DOT_LINE = { before: "aim", after: "thrown" };
// A clip's picture, as the worker scales it (practice/clips.py CLIP_SIZE).
const CLIP_W = 600, CLIP_H = 810;

// Signed links to clips, by the API path they came from: { url, download, at }.
const links = new Map();

async function clipLink(src, again = false) {
  const hit = links.get(src);
  if (!again && linkFresh(hit, Date.now())) return hit;
  const res = await authedFetch(src);
  if (!res.ok) return null;
  const { url, download } = await res.json();
  const got = { url, download, at: Date.now() };
  links.set(src, got);
  return got;
}

// How the lines are drawn, in the order the switch over the clip steps
// through them: thin and a little see-through, then bold enough to read on a
// phone at the rink, then none.
const LINE_STYLES = ["light", "bold", "off"];
const LINE_STYLE_NAMES = { light: "Light", bold: "Bold", off: "Off" };
// Each style's dots: the dot's radius and its light disc's, in clip pixels.
const DOT_R = { light: [1.25, 2.25], bold: [2.25, 3.5] };

/* The style last chosen. "on", from before there were styles, is light. */
function savedLines() {
  try {
    const got = localStorage.getItem(LINES_KEY);
    return LINE_STYLES.includes(got) ? got : got === "off" ? "off" : "light";
  } catch { return "light"; }
}

function saveLines(style) {
  try { localStorage.setItem(LINES_KEY, style); } catch { /* private window */ }
}

/* The broom, the lines and the slide's dots over the playing clip, for the
 * piece on screen (practice/overlay.py): points are fractions of the picture,
 * which the box holds at the clip's own shape; a dot shows once the clip
 * reaches it. Read every frame while it plays; React skips the render while
 * the piece and the dots shown are the same. */
function ClipLines({ overlay, video, style }) {
  const [piece, setPiece] = useState(() => overlayAt(overlay, 0));
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const v = video.current;
    if (!v) return undefined;
    let frame = 0;
    const update = () => {
      const p = overlayAt(overlay, v.currentTime);
      setPiece(p);
      setShown(dotsShown(p, v.currentTime));
    };
    const loop = () => { update(); frame = requestAnimationFrame(loop); };
    const play = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(loop); };
    const stop = () => { cancelAnimationFrame(frame); update(); };
    const on = { play, pause: stop, ended: stop, seeked: update, loadedmetadata: update };
    Object.entries(on).forEach(([e, f]) => v.addEventListener(e, f));
    if (v.paused) update(); else play();
    return () => {
      cancelAnimationFrame(frame);
      Object.entries(on).forEach(([e, f]) => v.removeEventListener(e, f));
    };
  }, [overlay, video]);
  if (!piece) return null;
  const pts = line => line.map(([u, v]) => `${(u * CLIP_W).toFixed(1)},${(v * CLIP_H).toFixed(1)}`)
    .join(" ");
  // Every edge goes down before any line, so where the two lines run together
  // one's dark edge never covers the other.
  const marks = [["aim", piece.aim], ["thrown", piece.thrown]].filter(([, line]) => line);
  const broom = piece.broom && { cx: piece.broom[0] * CLIP_W, cy: piece.broom[1] * CLIP_H, r: 9 };
  const dots = (piece.dots ?? []).slice(0, shown);
  return (
    <svg className={`clip-lines ${style}`} viewBox={`0 0 ${CLIP_W} ${CLIP_H}`} preserveAspectRatio="none"
         aria-hidden="true">
      {marks.map(([kind, line]) => <polyline key={`${kind}-edge`} points={pts(line)}
                                             className={`edge ${kind}-edge`} />)}
      {broom && <circle {...broom} className="edge" />}
      {marks.map(([kind, line]) => <polyline key={kind} points={pts(line)} className={kind} />)}
      {broom && <circle {...broom} className="broom" />}
      {/* The dots sit about a ring's width apart, so every light ring goes
          down first, as a disc under all of them: no dot covers another's. */}
      {dots.map(([, u, v], i) => (
        <circle key={`halo-${i}`} cx={u * CLIP_W} cy={v * CLIP_H} r={DOT_R[style][1]} className="dot-halo" />
      ))}
      {dots.map(([, u, v, kind], i) => (
        // Coloured as the line the rock is on: in the hand it travels the
        // hack-to-broom aim, once let go the thrown line.
        <circle key={`dot-${i}`} cx={u * CLIP_W} cy={v * CLIP_H} r={DOT_R[style][0]}
                className={`dot ${DOT_LINE[kind] ?? "unknown"}`} />
      ))}
    </svg>
  );
}

/* The key to the lines, over the clip's top corner: a sample of each mark
 * beside its name. The dots only when the clip has them. */
function LineKey({ dots }) {
  return (
    <ul className="clip-key" aria-label="Key to the lines">
      <li><svg viewBox="0 0 18 8" aria-hidden="true">
        <line x1="1" y1="4" x2="17" y2="4" className="thrown" /></svg>Thrown</li>
      <li><svg viewBox="0 0 18 8" aria-hidden="true">
        <line x1="1" y1="4" x2="17" y2="4" className="aim" /></svg>Hack to broom</li>
      <li><svg viewBox="0 0 18 8" aria-hidden="true">
        <circle cx="9" cy="4" r="3" className="broom" /></svg>Broom</li>
      {dots && <li><svg viewBox="0 0 18 8" aria-hidden="true">
        {[3, 9, 15].map(x => <circle key={`h${x}`} cx={x} cy="4" r="2.4" className="dot-halo" />)}
        <circle cx="3" cy="4" r="1.6" className="dot aim" />
        <circle cx="9" cy="4" r="1.6" className="dot aim" />
        <circle cx="15" cy="4" r="1.6" className="dot thrown" /></svg>Rock</li>}
    </ul>
  );
}

export function ClipPlayer({ src, overlay, burned = false, autoPlay = false }) {
  const [link, setLink] = useState(null);
  const [again, setAgain] = useState(false);
  const [lines, setLines] = useState(savedLines);
  const video = useRef(null);
  useEffect(() => {
    let on = true;
    setLink(null);
    setAgain(false);
    clipLink(src).then(l => { if (on) setLink(l); });
    return () => { on = false; };
  }, [src]);
  if (!link?.url) return null;
  const retry = () => {
    if (again) return;
    setAgain(true);
    clipLink(src, true).then(setLink);
  };
  const step = () => {
    const next = LINE_STYLES.at((LINE_STYLES.indexOf(lines) + 1) % LINE_STYLES.length);
    setLines(next);
    saveLines(next);
  };
  const shown = lines !== "off";
  const dots = overlay?.some(p => p.dots?.length);
  return (
    <>
      <div className="clip-box" key={link.url}>
        <video ref={video} className="practice-clip" src={link.url} controls playsInline
               muted={autoPlay} autoPlay={autoPlay} preload="metadata" onError={retry} />
        {shown && overlay && !burned && <ClipLines overlay={overlay} video={video} style={lines} />}
        {shown && overlay && !burned && <LineKey dots={dots} />}
        {overlay && !burned && (
          <button className="clip-switch" onClick={step}>
            Lines: {LINE_STYLE_NAMES[lines]}</button>
        )}
      </div>
      {burned && (
        <p className="clip-tools muted">
          {link.download && <><a href={link.download} download>⬇ Download</a>{" "}</>}
          Solid: the line it was thrown on · dashed: hack to broom · ring: the broom · dots: the
          rock from the hack to past the hog line
        </p>
      )}
    </>
  );
}
