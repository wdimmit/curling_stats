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

function savedLines() {
  try { return localStorage.getItem(LINES_KEY) !== "off"; } catch { return true; }
}

function saveLines(on) {
  try { localStorage.setItem(LINES_KEY, on ? "on" : "off"); } catch { /* private window */ }
}

/* The broom, the lines and the slide's dots over the playing clip, for the
 * piece on screen (practice/overlay.py): points are fractions of the picture,
 * which the box holds at the clip's own shape; a dot shows once the clip
 * reaches it. Read every frame while it plays; React skips the render while
 * the piece and the dots shown are the same. */
function ClipLines({ overlay, video }) {
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
    <svg className="clip-lines" viewBox={`0 0 ${CLIP_W} ${CLIP_H}`} preserveAspectRatio="none"
         aria-hidden="true">
      {marks.map(([kind, line]) => <polyline key={`${kind}-edge`} points={pts(line)}
                                             className={`edge ${kind}-edge`} />)}
      {broom && <circle {...broom} className="edge" />}
      {marks.map(([kind, line]) => <polyline key={kind} points={pts(line)} className={kind} />)}
      {broom && <circle {...broom} className="broom" />}
      {/* The dots sit about a ring's width apart, so every light ring goes
          down first, as a disc under all of them: no dot covers another's. */}
      {dots.map(([, u, v], i) => (
        <circle key={`halo-${i}`} cx={u * CLIP_W} cy={v * CLIP_H} r="2" className="dot-halo" />
      ))}
      {dots.map(([, u, v, kind], i) => (
        // Coloured as the line the rock is on: in the hand it travels the
        // hack-to-broom aim, once let go the thrown line.
        <circle key={`dot-${i}`} cx={u * CLIP_W} cy={v * CLIP_H} r="1.25"
                className={`dot ${DOT_LINE[kind] ?? "unknown"}`} />
      ))}
    </svg>
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
  const toggle = () => { setLines(!lines); saveLines(!lines); };
  const dots = overlay?.some(p => p.dots?.length);
  return (
    <>
      <div className="clip-box" key={link.url}>
        <video ref={video} className="practice-clip" src={link.url} controls playsInline
               muted={autoPlay} autoPlay={autoPlay} preload="metadata" onError={retry} />
        {lines && overlay && !burned && <ClipLines overlay={overlay} video={video} />}
      </div>
      {burned ? (
        <p className="clip-tools muted">
          {link.download && <><a href={link.download} download>⬇ Download</a>{" "}</>}
          Solid: the line it was thrown on · dashed: hack to broom · ring: the broom · dots: the
          rock from the hack to past the hog line
        </p>
      ) : overlay && (
        <p className="clip-tools muted">
          <button onClick={toggle}>{lines ? "Hide lines" : "Show lines"}</button>
          {lines && <> Solid: the line it was thrown on · dashed: hack to broom · ring: the broom
            {dots && " · dots: the rock from the hack to past the hog line"}</>}
        </p>
      )}
    </>
  );
}
