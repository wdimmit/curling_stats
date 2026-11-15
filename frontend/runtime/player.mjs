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
let pending = null;          // a seek asked for before the player could take it: {t, autoplay, id}
let container = null;
// The game's own video, and the one in the player now: a shot thrown before
// its stream began (or after it ended) plays from a neighbouring stream.
let ownId = null;
let currentId = null;
let overlay = null;          // "No stream for this shot", over the player

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
  ownId = currentId = videoId;

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
          if (pending !== null) {
            const { t, autoplay: play, id } = pending;
            pending = null;
            if (id && id !== currentId) show(id, t, play ?? autoplay());
            else seek(t, play ?? autoplay());
          }
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
  // Queued with the caller's own autoplay: a rock restored from a link or the
  // session seeks before the iframe is ready and asks not to start playing,
  // which the "play on jump" preference must not override.
  if (!ready) { pending = { t, autoplay }; return; }
  player.seekTo(t, true);
  if (autoplay) player.playVideo();
}

/* Play from ``t`` of video ``id``: the game's own (a seek), or a neighbouring
 * stream's, loaded in the same player -- never remounted. */
export function show(id, t, autoplay) {
  if (player === "none") return;
  noStream(false);
  if (!id || id === currentId) { seek(t, autoplay); return; }
  if (!ready) { pending = { t, autoplay, id }; return; }
  currentId = id;
  const args = { videoId: id, startSeconds: t };
  if (autoplay) player.loadVideoById(args); else player.cueVideoById(args);
}

/* A shot no stream shows: said over the player, and the video paused. */
export function noStream(on = true) {
  if (!container) return;
  if (!overlay) {
    if (!on) return;
    overlay = document.createElement("div");
    overlay.className = "nostream";
    overlay.textContent = "No stream for this shot.";
    container.appendChild(overlay);
  }
  overlay.hidden = !on;
  if (on) pause();
}

/* Whether the player shows the game's own video: a list that follows the
 * playhead, or a pause after each rock, means nothing on another. */
export function onOwnVideo() {
  return currentId === ownId;
}

/* Where the playhead is, for a list that follows along. Guarded exactly as
 * seek() is: before the iframe answers, and after it has failed, there is no
 * time to report -- and null says so rather than 0, which is a real position
 * and would drag a following list back to the top of the end. */
export function currentTime() {
  if (!player || player === "none" || !ready) return null;
  const t = player.getCurrentTime();
  return typeof t === "number" && Number.isFinite(t) ? t : null;
}

/* Whether it is actually playing. A paused video should not pull the list
 * around under a thumb that stopped it deliberately. */
export function isPlaying() {
  if (!player || player === "none" || !ready) return false;
  return player.getPlayerState() === 1;
}

export function pause() {
  if (!player || player === "none" || !ready) return;
  player.pauseVideo();
}

export function togglePlay() {
  if (!player || player === "none" || !ready) return;
  player.getPlayerState() === 1 ? player.pauseVideo() : player.playVideo();
}
