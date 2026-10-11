/* A clip of one throw with the broom and lines drawn over it -- the practice
 * pages' and My shots'. `src` is the API path that answers with a short-lived
 * link ({url}); `overlay` is the worker's per-piece list (practice/overlay.py).
 * A link that has expired under an open page is asked for once more. */
import { useEffect, useRef, useState } from "react";
import { authedFetch } from "./auth.js";
import { linkFresh, overlayAt } from "../core/practice.mjs";

const LINES_KEY = "practice.lines";
// A clip's picture, as the worker scales it (practice/clips.py CLIP_SIZE).
const CLIP_W = 600, CLIP_H = 810;

// Signed links to clips, by the API path they came from: { url, at }.
const links = new Map();

async function clipLink(src, again = false) {
  const hit = links.get(src);
  if (!again && linkFresh(hit, Date.now())) return hit.url;
  const res = await authedFetch(src);
  if (!res.ok) return null;
  const { url } = await res.json();
  links.set(src, { url, at: Date.now() });
  return url;
}

function savedLines() {
  try { return localStorage.getItem(LINES_KEY) !== "off"; } catch { return true; }
}

function saveLines(on) {
  try { localStorage.setItem(LINES_KEY, on ? "on" : "off"); } catch { /* private window */ }
}

/* The broom and lines over the playing clip, for the piece on screen
 * (practice/overlay.py): points are fractions of the picture, which the box
 * holds at the clip's own shape. Read every frame while it plays; React
 * skips the render while the piece is the same. */
function ClipLines({ overlay, video }) {
  const [piece, setPiece] = useState(() => overlayAt(overlay, 0));
  useEffect(() => {
    const v = video.current;
    if (!v) return undefined;
    let frame = 0;
    const update = () => setPiece(overlayAt(overlay, v.currentTime));
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
  const drawn = (line, kind) => line && [
    <polyline key={`${kind}-under`} points={pts(line)} className="under" />,
    <polyline key={kind} points={pts(line)} className={kind} />];
  return (
    <svg className="clip-lines" viewBox={`0 0 ${CLIP_W} ${CLIP_H}`} preserveAspectRatio="none"
         aria-hidden="true">
      {drawn(piece.aim, "aim")}
      {drawn(piece.thrown, "thrown")}
      {piece.broom && <>
        <circle cx={piece.broom[0] * CLIP_W} cy={piece.broom[1] * CLIP_H} r="13" className="under" />
        <circle cx={piece.broom[0] * CLIP_W} cy={piece.broom[1] * CLIP_H} r="13" className="broom" />
      </>}
    </svg>
  );
}

export function ClipPlayer({ src, overlay, autoPlay = false }) {
  const [url, setUrl] = useState(null);
  const [again, setAgain] = useState(false);
  const [lines, setLines] = useState(savedLines);
  const video = useRef(null);
  useEffect(() => {
    let on = true;
    setUrl(null);
    setAgain(false);
    clipLink(src).then(u => { if (on) setUrl(u); });
    return () => { on = false; };
  }, [src]);
  if (!url) return null;
  const retry = () => {
    if (again) return;
    setAgain(true);
    clipLink(src, true).then(setUrl);
  };
  const toggle = () => { setLines(!lines); saveLines(!lines); };
  return (
    <>
      <div className="clip-box" key={url}>
        <video ref={video} className="practice-clip" src={url} controls playsInline
               muted={autoPlay} autoPlay={autoPlay} preload="metadata" onError={retry} />
        {lines && overlay && <ClipLines overlay={overlay} video={video} />}
      </div>
      {overlay && (
        <p className="clip-tools muted">
          <button onClick={toggle}>{lines ? "Hide lines" : "Show lines"}</button>
          {lines && <> Solid: the line it was thrown on · dashed: hack to broom · ring: the broom</>}
        </p>
      )}
    </>
  );
}
