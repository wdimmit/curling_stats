/* The YouTube player, deliberately outside React.
 *
 * `YT.Player` replaces the element it is given with a cross-origin iframe and
 * talks to it over postMessage. It cannot be React state, and it must never be
 * recreated: the phone layout hides the video behind the house editor's opaque
 * background rather than unmounting it, precisely because a remount costs the
 * player and the seek. So this is a module singleton with an idempotent
 * mount() -- which also makes it safe under StrictMode's double effect.
 *
 * `#player` is created here rather than rendered by React. If React held a
 * vdom child for that slot, one reconciliation of #video's children would
 * remove the iframe YT put there; with no children in the vdom there is
 * nothing to reconcile, permanently.
 */
let player = null;           // the YT.Player, or "none" once it has failed
let ready = false;
let pending = null;          // a seek asked for before the player could take it
let container = null;

/* Written into #video rather than rendered by React. `.fallback` is styled
 * `height:100%` to fill the video box, and React must own no children there:
 * YT.Player replaces the element it is given with an iframe, so one
 * reconciliation of #video's children would take the player with it. */
function fail(why) {
  if (player) return;
  player = "none";
  if (!container) return;
  const box = document.createElement("div");
  box.className = "fallback";
  box.textContent = `Video cannot be embedded here (${why}).`;
  box.appendChild(document.createElement("br"));
  box.append("Use the YouTube link beside each shot.");
  container.replaceChildren(box);
}

export function mount(el, videoId, { autoplay = () => true } = {}) {
  if (player || !el) return;                 // already mounted, or already failed
  container = el;

  const slot = document.createElement("div");
  slot.id = "player";
  el.appendChild(slot);

  window.onYouTubeIframeAPIReady = () => {
    player = new YT.Player("player", {
      videoId,
      playerVars: { rel: 0, playsinline: 1, modestbranding: 1, origin: location.origin },
      events: {
        onReady: () => {
          ready = true;
          if (pending !== null) { const t = pending; pending = null; seek(t, autoplay()); }
        },
        onError: e => fail(`player error ${e.data}`),
      },
    });
  };

  const s = document.createElement("script");
  s.src = "https://www.youtube.com/iframe_api";
  s.onerror = () => fail("script blocked");
  document.head.appendChild(s);
  setTimeout(() => { if (!player) fail("timed out"); }, 8000);
}

export function seek(t, autoplay) {
  if (player === "none") return;
  if (!ready) { pending = t; return; }
  player.seekTo(t, true);
  if (autoplay) player.playVideo();
}

export function togglePlay() {
  if (!player || player === "none" || !ready) return;
  player.getPlayerState() === 1 ? player.pauseVideo() : player.playVideo();
}
