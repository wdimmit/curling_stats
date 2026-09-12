"use strict";
/* Chart a game shot by shot: watch it, fix the house, name the shot, grade it.
 *
 * Nothing here mutates the detected timeline. Every edit lands in `overrides`,
 * keyed the same way `timeline.apply_overrides` keys them, and is merged over
 * the detection at render time -- so re-running the analysis never destroys
 * charting work, and charting never hides what the detector actually said.
 */

/* The SVG is painted with literal colours rather than CSS variables: var()
 * in a presentation attribute is not dependable, and a house that silently
 * fails to paint is far worse than one that does not follow the theme. */
const PAINT = { ice:"#fbfbfa", iceLine:"#c9c9c4", rail:"#8a8a85",
                twelve:"#3f9c47", four:"#3b4fa8", granite:"#9a9a95",
                graniteEdge:"#5f5f5b", red:"#d13438", yellow:"#e8b400",
                warn:"#b45309", accent:"#2b6cb0", thrown:"#ffffff" };
const R = { button:0.152, four:0.610, eight:1.219, twelve:1.829, stone:0.142,
            inHouse:1.971, hog:6.401, back:-1.829, halfWidth:2.375 };
// Keep placed stones where they can still be seen: the viewBox stops at 6.0,
// short of the hog line, and a stone dropped past it would vanish.
const LIMIT = { x:R.halfWidth - R.stone, yLo:-2.45, yHi:5.9 };
const DRAG_MIN_M = 0.03;   // below this a pointer gesture is a click, not a drag
const SAVE_DEBOUNCE_MS = 800;

/* Curl Coach's taxonomy. The detector only ever offers the four coarse
 * categories; the fine type is a statement about what was *called*, which no
 * amount of tracking recovers, so it is the charter's to give. */
const GROUPS = ["Draw", "Guard", "Hit", "Other"];
const TYPES = [
  { id:"draw",        name:"Draw",          group:"Draw"  },
  { id:"come_around", name:"Come around",   group:"Draw"  },
  { id:"freeze",      name:"Freeze",        group:"Draw"  },
  { id:"split_on",    name:"Split on",      group:"Draw"  },
  { id:"tap_up",      name:"Tap up",        group:"Draw"  },
  { id:"guard",       name:"Guard",         group:"Guard" },
  { id:"free_guard",  name:"Free guard",    group:"Guard" },
  { id:"centre_guard",name:"Centre guard",  group:"Guard" },
  { id:"corner_guard",name:"Corner guard",  group:"Guard" },
  { id:"hit",         name:"Hit",           group:"Hit"   },
  { id:"hit_stick",   name:"Hit & stick",   group:"Hit"   },
  { id:"hit_roll",    name:"Hit & roll",    group:"Hit"   },
  { id:"hit_roll_away",name:"Hit & roll away",group:"Hit" },
  { id:"double",      name:"Double",        group:"Hit"   },
  { id:"peel",        name:"Peel",          group:"Hit"   },
  { id:"raise",       name:"Raise",         group:"Hit"   },
  { id:"run_back",    name:"Run back",      group:"Hit"   },
  { id:"tick",        name:"Tick",          group:"Hit"   },
  { id:"tick_bump",   name:"Tick-bump",     group:"Hit"   },
  { id:"in_off",      name:"In-off",        group:"Hit"   },
  // Curl Coach's "non scored shots": charted, but never counted in a
  // percentage, because there was no shot to make.
  { id:"through",     name:"Throw away",    group:"Other", unscored:true },
  { id:"not_thrown",  name:"Not thrown",    group:"Other", unscored:true },
  { id:"unknown",     name:"Unknown",       group:"Other", unscored:true },
];
const TYPE = Object.fromEntries(TYPES.map(t => [t.id, t]));
const MISS_REASONS = ["heavy", "light", "narrow", "wide", "wrong turn",
                      "swept too long", "not swept enough", "wrecked on a guard",
                      "rock picked", "hogged"];

/* Served by the hosted API the page is mounted at /c/{slug}/ and told its
 * mode; served locally there is no window.CHART and everything is editable. */
const CHART = (typeof window !== "undefined" && window.CHART) || { mode:"edit", slug:null };
const READ_ONLY = CHART.mode === "view";

const state = {
  doc:null, overrides:{}, version:null, gi:0, ei:0, si:0,
  selStone:null, placeColor:"red", openGroup:null,
  showTrack:true, autoplay:true, leadIn:10,
  player:null, playerReady:false, pendingSeek:null, seekTimer:null,
  saveTimer:null, saving:false, again:false, reporting:false,
  sheet:"peek", houseMode:"", notice:null,
};

const $ = id => document.getElementById(id);
const svgEl = (tag, attrs={}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k,v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

/* ------------------------------------------------------------------ model */

const game = () => state.doc.games[state.gi];
const end  = () => game().ends[state.ei];
/* A shot is known by the number detection gave it. Moving one renumbers the
 * end, so `id` keeps the original where that has happened. */
const identity = s => s.id ?? s.number;
const rawShot = () => layout(end()).raws[state.si] ?? null;
const keyFor = (g, e, s) => `${g.index}.${e.number}.${identity(s)}`;
// An end where detection found nothing still has to render, so everything
// downstream has to cope with there being no current shot.
const shotKey = () => { const s = rawShot(); return s ? keyFor(game(), end(), s) : null; };

/* Mirror of timeline.apply_overrides, so what you see here is exactly what
 * the next analysis run will bake in. */
function merge(g, e, s) {
  if (!s) return null;
  const patch = state.overrides[keyFor(g, e, s)];
  if (!patch) return s;
  return { ...s, ...patch, corrected:true };
}
const shot = () => layout(end()).shots[state.si] ?? null;
const mergedShots = e => layout(e).shots;

/* Mirror of the rest of timeline.apply_overrides: after the patches, a shot
 * carrying `before` was thrown before the shot it names, so the end is put in
 * that order and renumbered -- thrower, label and hammer follow the number,
 * and a blank's colour follows the alternation around it. `raws` are the
 * document's own shot objects in the same order, for editing. */
const TYPICAL_GAP_S = 45;   // a club delivery about every 45 s; for guessing a blank's time
function layout(e) {
  const g = game();
  let shots = e.shots.map(s => ({ ...merge(g, e, s) }));
  let raws = e.shots;
  const ids = new Set(shots.map(identity));
  const moves = shots.filter(s => Number.isInteger(s.before) &&
                                  s.before !== identity(s) && ids.has(s.before));
  if (moves.length) {
    shots = shots.filter(s => !moves.includes(s));
    for (const s of [...moves].sort((a, b) => identity(a) - identity(b))) {
      const at = shots.findIndex(o => identity(o) === s.before);
      shots.splice(at === -1 ? shots.length : at, 0, s);
    }
    const prefix = `${g.index}.${e.number}.`;
    const fixed = new Set(Object.entries(state.overrides)
      .filter(([k, p]) => k.startsWith(prefix) && p && "color" in p)
      .map(([k]) => +k.slice(prefix.length)));
    renumber(shots, e.number, fixed);
    const byId = new Map(e.shots.map(s => [identity(s), s]));
    raws = shots.map(s => byId.get(identity(s)));
  }
  guessTimes(shots);
  return { shots, raws };
}

const throwInfo = n => {
  const k = (n + 1) >> 1;   // this team's k-th stone
  return { has_hammer:n % 2 === 0, thrower_slot:(k + 1) >> 1, rock_of_player:2 - (k % 2) };
};
const ordinal = n => n + (n % 100 >= 11 && n % 100 <= 13 ? "th"
                          : { 1:"st", 2:"nd", 3:"rd" }[n % 10] || "th");
function renumber(shots, endNo, fixed) {
  const anchors = [];
  shots.forEach((s, i) => {
    if (!s.color_inferred || fixed.has(identity(s))) anchors.push([i, s.color]);
  });
  shots.forEach((s, i) => {
    s.id = identity(s);
    s.number = i + 1;
    const t = throwInfo(i + 1);
    s.has_hammer = t.has_hammer;
    s.thrower_slot = t.thrower_slot;
    s.position = POSITIONS[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = `${ordinal(endNo)} end, ${s.position}'s ${t.rock_of_player === 1 ? "first" : "second"} rock`;
    if (s.color_inferred && anchors.length && !fixed.has(s.id)) {
      let best = anchors[0];
      for (const a of anchors) if (Math.abs(a[0] - i) < Math.abs(best[0] - i)) best = a;
      s.color = (i - best[0]) % 2 === 0 ? best[1] : (best[1] === "red" ? "yellow" : "red");
    }
  });
}

/* A blank has no timestamp. The nearest rock that has one, a typical gap per
 * shot away, is a fair place to start the video looking for it. */
function guessTimes(shots) {
  const timed = shots.map((s, i) => [i, s.t_enter_s]).filter(([, t]) => typeof t === "number");
  if (!timed.length) return;
  shots.forEach((s, i) => {
    if (typeof s.t_enter_s === "number" || typeof s.t_rest_s === "number") return;
    let best = timed[0];
    for (const a of timed) if (Math.abs(a[0] - i) < Math.abs(best[0] - i)) best = a;
    s.t_guess_s = Math.max(0, best[1] + (i - best[0]) * TYPICAL_GAP_S);
  });
}

const isBlank = s => s && (s.state_known === false || s.missing);
const typeOf = s => s ? (s.shot_type || "unknown") : "unknown";
const isGraded = s => s && typeof s.user_score === "number";

/* The phone cannot show the sheet's whole length beside the video, so it shows
 * the house centred on the tee and lets the guards fall off the bottom.
 * Centring is what keeps the 12-foot whole however short the band gets.
 * `aspect` is the rendered box's width over its height; desktop passes nothing
 * that matters, because "full" is always the view it has always had. */
function houseViewBox(mode, aspect) {
  if (mode !== "crop" || !(aspect > 0)) return "-2.6 -2.6 5.2 8.6";
  const h = Math.min(5.2 / aspect, 8.6);
  const n = x => String(+x.toFixed(3));
  return `-2.6 ${n(-h / 2)} 5.2 ${n(h)}`;
}

/* Nobody can grade a rock nobody saw. A blank's fast path is saying where it
 * was thrown and what was on the ice, so its peek carries different controls. */
const peekMode = s => isBlank(s) ? "order" : "grade";

/* Moving a rock renumbers all sixteen. On desktop the chip strip shows that
 * happening; the phone has no strip, so the change announces itself -- and
 * says where the colour came from, since `renumber` settles a blank's colour
 * from the alternation around it and the charter has no other way to know. */
function renumberNotice(before, after) {
  if (!before || !after || before.number === after.number) return null;
  const why = after.color_inferred ? `, ${after.color} by alternation` : "";
  return `End renumbered — this is now rock ${after.number}${why}`;
}

function patchShot(fields) {
  if (READ_ONLY) return;
  const key = shotKey();
  if (!key) return;
  state.overrides[key] = { ...(state.overrides[key] || {}), ...fields };
  markDirty();
  render();
}

function unpatchShot(field) {
  const key = shotKey();
  if (!key || !state.overrides[key]) return;
  delete state.overrides[key][field];
  if (!Object.keys(state.overrides[key]).length) delete state.overrides[key];
  markDirty();
  render();
}

function clearShot() {
  const key = shotKey();
  if (!key) return;
  delete state.overrides[key];
  state.selStone = null;
  markDirty();
  render();
}

/* Stones live in the patch as a whole array, because the patch is a shallow
 * merge -- so the first edit takes a copy of whatever is currently shown. */
function editStones(fn) {
  if (!shot()) return;
  const stones = (shot().stones || []).map(s => ({ ...s }));
  const next = fn(stones);
  patchShot({ stones:next ?? stones, state_known:true });
}

function stoneAt(x, y, color) {
  const d = Math.hypot(x, y);
  return { color, x, y, distance_to_tee:d, in_house:d <= R.inHouse,
           confidence:1.0, source:"manual" };
}

/* Removing a stone renumbers everything after it, so the pointer to the stone
 * that was thrown has to move with it -- otherwise deleting a rock silently
 * re-labels a different one as the shot. */
function removeStone(i) {
  const s = shot();
  if (!s) return;
  const stones = (s.stones || []).map(x => ({ ...x }));
  if (!stones[i]) return;
  stones.splice(i, 1);
  let thrown = s.delivered_stone_index;
  if (typeof thrown === "number")
    thrown = thrown === i ? null : thrown > i ? thrown - 1 : thrown;
  state.selStone = null;
  patchShot({ stones, state_known:true, delivered_stone_index:thrown ?? null });
}

/* ------------------------------------------------------------------- save */

function markDirty() {
  if (READ_ONLY) return;
  setSave("unsaved", "•");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(save, SAVE_DEBOUNCE_MS);
}

function setSave(cls, text) {
  const n = $("save");
  n.className = "pill" + (cls === "saved" ? " ok" : cls === "failed" ? " bad" : "");
  n.textContent = text;
}

async function save() {
  if (state.saving) { state.again = true; return; }
  state.saving = true;
  setSave("", "saving…");
  try {
    // The version we last saw rides along so two open tabs cannot silently
    // overwrite each other; the local server ignores it.
    const url = state.version === null ? "overrides.json" : `overrides.json?v=${state.version}`;
    const res = await fetch(url, {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body:JSON.stringify(state.overrides),
    });
    if (res.status === 409) {
      const body = await res.json();
      state.overrides = body.overrides || {};
      state.version = body.version ?? state.version;
      render();
      setSave("failed", "reloaded — someone else saved");
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json().catch(() => ({}));
    if (typeof body.version === "number") state.version = body.version;
    const t = new Date();
    setSave("saved", `saved ${String(t.getHours()).padStart(2,"0")}:` +
                     `${String(t.getMinutes()).padStart(2,"0")}`);
  } catch (err) {
    // Keep the edits in memory and say so plainly -- the download button is
    // still there, so nothing charted has to be lost.
    setSave("failed", "not saved — use ⬇");
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(save, 5000);
  } finally {
    state.saving = false;
    if (state.again) { state.again = false; markDirty(); }
  }
}

const BROWSER = typeof document !== "undefined";

if (BROWSER) addEventListener("pagehide", () => {
  if (!state.saveTimer || READ_ONLY) return;
  // A beacon cannot read the reply, so it saves unconditionally: better a
  // last-writer save than losing the last minute of grading.
  navigator.sendBeacon?.("overrides.json",
    new Blob([JSON.stringify(state.overrides)], { type:"application/json" }));
});

function download() {
  const blob = new Blob([JSON.stringify(state.overrides, null, 2)],
                        { type:"application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "overrides.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------------ house */

function sheetPoint(evt) {
  const svg = $("house");
  const p = new DOMPoint(evt.clientX, evt.clientY)
              .matrixTransform(svg.getScreenCTM().inverse());
  return { x:p.x, y:p.y };
}
const onSheet = p => Math.abs(p.x) <= R.halfWidth && p.y >= LIMIT.yLo && p.y <= R.hog;
const clampX = x => Math.max(-LIMIT.x, Math.min(LIMIT.x, x));
const clampY = y => Math.max(LIMIT.yLo, Math.min(LIMIT.yHi, y));

function drawHouse() {
  const svg = $("house");
  const s = shot();
  svg.textContent = "";

  const defs = svgEl("defs");
  const clip = svgEl("clipPath", { id:"sheetClip" });
  clip.appendChild(svgEl("rect", { x:-R.halfWidth, y:R.back,
                                   width:2*R.halfWidth, height:R.hog-R.back }));
  const hatch = svgEl("pattern", { id:"hatch", width:0.36, height:0.36,
                                   patternUnits:"userSpaceOnUse",
                                   patternTransform:"rotate(45)" });
  hatch.appendChild(svgEl("rect", { width:0.18, height:0.36,
                                    fill:PAINT.warn, opacity:0.22 }));
  defs.append(clip, hatch);
  svg.appendChild(defs);

  svg.appendChild(svgEl("rect", { id:"ice", x:-R.halfWidth, y:R.back,
      width:2*R.halfWidth, height:R.hog-R.back,
      fill:PAINT.ice, stroke:PAINT.iceLine, "stroke-width":.02 }));
  for (const [r,fill] of [[R.twelve,PAINT.twelve], [R.eight,PAINT.ice],
                          [R.four,PAINT.four], [R.button,PAINT.ice]])
    svg.appendChild(svgEl("circle", { cx:0, cy:0, r, fill }));
  for (const y of [0, R.hog])
    svg.appendChild(svgEl("line", { x1:-R.halfWidth, y1:y, x2:R.halfWidth, y2:y,
        stroke:PAINT.rail, "stroke-width":y ? 0.05 : 0.02 }));
  svg.appendChild(svgEl("line", { x1:0, y1:R.back, x2:0, y2:R.hog,
      stroke:PAINT.rail, "stroke-width":.015 }));

  if (state.showTrack) drawTrack(svg, s);

  const stones = s?.stones || [];
  const thrown = s?.delivered_stone_index;
  stones.forEach((st, i) => {
    const g = svgEl("g", { class:"stone" + (i === state.selStone ? " sel" : ""),
                           "data-i":i });
    if (i === thrown)
      g.appendChild(svgEl("circle", { cx:st.x, cy:st.y, r:R.stone + 0.07,
          fill:"none", stroke:PAINT.thrown, "stroke-width":.03,
          "stroke-dasharray":"0.09 0.07" }));
    g.appendChild(svgEl("circle", { cx:st.x, cy:st.y, r:R.stone, fill:PAINT.granite,
        stroke:PAINT.graniteEdge, "stroke-width":.02 }));
    g.appendChild(svgEl("circle", { cx:st.x, cy:st.y, r:R.stone*0.55,
        fill:st.color === "red" ? PAINT.red : PAINT.yellow }));
    if (i === state.selStone)
      g.appendChild(svgEl("circle", { cx:st.x, cy:st.y, r:R.stone + 0.05,
          fill:"none", stroke:PAINT.accent, "stroke-width":.035,
          "stroke-dasharray":"0.07 0.05" }));
    const title = svgEl("title");
    title.textContent = `${st.color} — ${(st.distance_to_tee ?? 0).toFixed(2)} m ` +
      `from the tee${st.in_house ? " (in the rings)" : ""}` +
      `${i === thrown ? " — this shot" : ""}`;
    g.appendChild(title);
    svg.appendChild(g);
  });

  if (isBlank(s)) {
    const g = svgEl("g", { "pointer-events":"none" });
    g.appendChild(svgEl("rect", { x:-R.halfWidth, y:R.back, width:2*R.halfWidth,
        height:R.hog-R.back, fill:"url(#hatch)" }));
    g.appendChild(svgEl("rect", { x:-R.halfWidth, y:R.back, width:2*R.halfWidth,
        height:R.hog-R.back, fill:"none", stroke:PAINT.warn,
        "stroke-width":.05, "stroke-dasharray":"0.25 0.18" }));
    const t = svgEl("text", { x:0, y:4.9, "text-anchor":"middle",
        "font-size":.34, fill:PAINT.warn, "font-weight":"700" });
    t.textContent = "STATE UNKNOWN — chart this shot";
    g.appendChild(t);
    svg.appendChild(g);
  }
}

/* The flight, drawn as a tail that fades in from where the camera first saw
 * the stone to where it stopped. Segments rather than one polyline so the
 * opacity can ramp without a gradient along the path. */
function drawTrack(svg, s) {
  const pts = s?.track;
  if (!pts || pts.length < 2) return;
  const g = svgEl("g", { "clip-path":"url(#sheetClip)", "pointer-events":"none" });
  const color = s.color === "red" ? PAINT.red : PAINT.yellow;
  for (let i = 0; i < pts.length - 1; i++) {
    const f = i / Math.max(1, pts.length - 2);
    g.appendChild(svgEl("line", {
      x1:pts[i][1], y1:pts[i][2], x2:pts[i+1][1], y2:pts[i+1][2],
      stroke:color, "stroke-width":0.045, "stroke-linecap":"round",
      opacity:(0.12 + 0.78 * f).toFixed(3) }));
  }
  g.appendChild(svgEl("circle", { cx:pts[0][1], cy:pts[0][2], r:0.06,
      fill:"none", stroke:color, "stroke-width":0.025, opacity:0.5 }));
  svg.insertBefore(g, svg.querySelector(".stone") || null);
}

function bindHouse() {
  const svg = $("house");
  let drag = null;

  svg.addEventListener("pointerdown", ev => {
    const g = ev.target.closest(".stone");
    const p = sheetPoint(ev);
    if (g) {
      const i = +g.dataset.i;
      drag = { i, start:p, moved:false };
      state.selStone = i;
      svg.setPointerCapture(ev.pointerId);
      render();
    } else {
      drag = { i:null, start:p, moved:false };
    }
  });

  svg.addEventListener("pointermove", ev => {
    if (!drag || drag.i === null) return;
    const p = sheetPoint(ev);
    if (!drag.moved && Math.hypot(p.x - drag.start.x, p.y - drag.start.y) < DRAG_MIN_M)
      return;
    drag.moved = true;
    const key = shotKey();
    if (!key) return;
    const stones = (shot().stones || []).map(s => ({ ...s }));
    const st = stones[drag.i];
    if (!st) return;
    st.x = clampX(p.x); st.y = clampY(p.y);
    st.distance_to_tee = Math.hypot(st.x, st.y);
    st.in_house = st.distance_to_tee <= R.inHouse;
    st.source = "manual";
    // Live feedback while dragging, committed on release.
    state.overrides[key] = { ...(state.overrides[key] || {}),
                             stones, state_known:true };
    drawHouse();
  });

  svg.addEventListener("pointerup", ev => {
    if (!drag) return;
    const p = sheetPoint(ev);
    const d = drag; drag = null;
    if (d.i !== null) {
      if (!d.moved) { render(); return; }          // a click that selected it
      if (!onSheet(p)) { removeStone(d.i); return; }  // dragged off: remove it
      markDirty(); render();
      return;
    }
    if (!onSheet(p) || Math.hypot(p.x - d.start.x, p.y - d.start.y) >= DRAG_MIN_M)
      return;
    // Empty ice: place a stone. On a blank shot the first rock of the
    // thrower's colour is, by construction, the one they just threw.
    const cur = shot();
    if (!cur) return;
    editStones(stones => {
      stones.push(stoneAt(clampX(p.x), clampY(p.y), state.placeColor));
      state.selStone = stones.length - 1;
      return stones;
    });
    if (isBlank(cur) && state.placeColor === cur.color &&
        cur.delivered_stone_index == null)
      patchShot({ delivered_stone_index:state.selStone });
  });
}

/* ------------------------------------------------------------------ video */

function loadPlayer() {
  const fail = why => {
    if (state.player) return;
    state.player = "none";
    $("video").innerHTML =
      `<div class="fallback">Video cannot be embedded here (${esc(why)}).<br>
       Use the YouTube link beside each shot.</div>`;
  };
  window.onYouTubeIframeAPIReady = () => {
    state.player = new YT.Player("player", {
      videoId:state.doc.source.video_id,
      playerVars:{ rel:0, playsinline:1, modestbranding:1, origin:location.origin },
      events:{
        onReady:() => {
          state.playerReady = true;
          if (state.pendingSeek !== null) {
            const t = state.pendingSeek; state.pendingSeek = null; seekTo(t);
          }
        },
        onError:e => fail(`player error ${e.data}`),
      },
    });
  };
  const s = document.createElement("script");
  s.src = "https://www.youtube.com/iframe_api";
  s.onerror = () => fail("script blocked");
  document.head.appendChild(s);
  setTimeout(() => { if (!state.player) fail("timed out"); }, 8000);
}

function seekTo(t) {
  if (state.player === "none") return;
  if (!state.playerReady) { state.pendingSeek = t; return; }
  state.player.seekTo(t, true);
  if (state.autoplay) state.player.playVideo();
}

function shotVideoTime(s) {
  if (!s) return null;
  if (typeof s.t_enter_s === "number")
    return Math.max(0, s.t_enter_s - state.leadIn);
  if (typeof s.t_rest_s === "number")
    return Math.max(0, s.t_rest_s - state.leadIn - 8);
  if (typeof s.t_guess_s === "number")
    return Math.max(0, s.t_guess_s - state.leadIn);
  return null;
}

// Holding an arrow key should not fire a seek per shot skipped over.
function seekCurrent() {
  clearTimeout(state.seekTimer);
  state.seekTimer = setTimeout(() => {
    const t = shotVideoTime(shot());
    if (t !== null) seekTo(t);
  }, 200);
}

/* ------------------------------------------------------------ chart panel */

function renderChart() {
  const s = shot();
  const t = typeOf(s);
  state.openGroup = state.openGroup || TYPE[t]?.group || "Draw";

  $("typeGroups").innerHTML = GROUPS.map(g =>
    `<button data-g="${g}" class="${g === state.openGroup ? "on" : ""}">${g}</button>`
  ).join("");
  [...$("typeGroups").children].forEach(n =>
    n.onclick = () => { state.openGroup = n.dataset.g; renderChart(); });

  $("typeList").innerHTML = TYPES.filter(x => x.group === state.openGroup)
    .map(x => `<button data-t="${x.id}" class="${x.id === t ? "on" : ""}">${x.name}</button>`)
    .join("");
  [...$("typeList").children].forEach(n =>
    n.onclick = () => patchShot({ shot_type:n.dataset.t, shot_type_source:"manual" }));

  const auto = s?.shot_type_source === "manual"
    ? `detector said ${esc(rawShot().shot_type || "unknown")}`
    : `auto${s?.shot_type_confidence ? ` · confidence ${s.shot_type_confidence.toFixed(2)}` : ""}`;
  $("autoType").textContent = auto;

  $("scoreBtns").innerHTML = [0,1,2,3,4].map(v =>
    `<button data-v="${v}" class="${s?.user_score === v ? "on" : ""}">${v}</button>`
  ).join("") + `<button data-v="" title="Clear the score">&mdash;</button>`;
  [...$("scoreBtns").children].forEach(n => n.onclick = () =>
    patchShot({ user_score:n.dataset.v === "" ? null : +n.dataset.v }));

  const others = mergedShots(end()).filter(x => s && identity(x) !== identity(s));
  $("moveBefore").innerHTML = `<option value="">detected order</option>` +
    others.map(x => `<option value="${identity(x)}"
      ${s?.before === identity(x) ? "selected" : ""}>before #${x.number}
      (${x.color}${isBlank(x) ? ", blank" : ""})</option>`).join("");
  $("moveBefore").disabled = !s || READ_ONLY;

  const movedBefore = others.find(x => s?.before === identity(x));
  $("orderRowValue").textContent =
    movedBefore ? `before #${movedBefore.number} (${movedBefore.color})` : "detected order";
  $("orderRow").disabled = !s || READ_ONLY;

  document.body.dataset.peek = peekMode(s);
  document.body.dataset.sheet = state.sheet;
  document.body.dataset.house = state.houseMode;

  const note = $("renumbered");
  note.textContent = state.notice || "";
  note.hidden = !state.notice;

  if (document.activeElement !== $("missReason"))
    $("missReason").value = s?.miss_reason || "";
  if (document.activeElement !== $("note")) $("note").value = s?.note || "";

  const d = s?.house_delta;
  const deltaText = !d ? "—"
    : [d.removed?.length && `${d.removed.length} out`,
       d.added?.length && `${d.added.length} in`,
       d.moved?.length && `${d.moved.length} moved`].filter(Boolean).join(", ") || "no change";
  $("detail").innerHTML = `
    <dt>Thrower</dt><dd>${esc(s?.position ?? "—")}${s ? ` (rock ${s.rock_of_player})` : ""}</dd>
    <dt>Weight</dt><dd>${s?.entry_speed_m_s != null ? s.entry_speed_m_s.toFixed(2) + " m/s" : "—"}</dd>
    <dt>Travel</dt><dd>${s?.travel_m != null ? s.travel_m.toFixed(2) + " m" : "—"}</dd>
    <dt>House</dt><dd>${deltaText}</dd>
    <dt>Evidence</dt><dd>${esc(s?.reason ?? "—")}</dd>
    <dt>Stones</dt><dd>${s?.stones?.length ?? 0}</dd>`;
}

function renderQueue() {
  const items = [];
  for (const [ei, e] of game().ends.entries())
    mergedShots(e).forEach((s, si) => {
      if (isBlank(s)) items.push({ ei, si, label:`E${e.number} · ${s.label || `shot ${s.number}`}` });
    });
  const unplaced = game().ends.reduce((n, e) => n + (e.unplaced_shots || 0), 0);
  $("queueCount").textContent = items.length;
  $("blanks").textContent = items.length
    ? `${items.length} to chart →`
    : unplaced ? `${unplaced} unaccounted for` : "all charted ✓";
  $("blanks").className = "pill " + (items.length || unplaced ? "bad" : "ok");
  $("queue").innerHTML = items.map((it, i) =>
    `<button data-i="${i}">${esc(it.label)}</button>`).join("") ||
    `<div class="muted" style="font-size:12px">Nothing needs charting.</div>`;
  [...$("queue").children].forEach(n => {
    if (n.dataset?.i === undefined) return;   // the "nothing to chart" note
    n.onclick = () => { const it = items[+n.dataset.i]; goTo(it.ei, it.si); };
  });
  return items;
}

function nextBlank() {
  const items = renderQueue();
  if (!items.length) return;
  const here = items.findIndex(it =>
    it.ei > state.ei || (it.ei === state.ei && it.si > state.si));
  const it = items[here === -1 ? 0 : here];
  goTo(it.ei, it.si);
}

function goTo(ei, si) {
  state.ei = ei; state.si = si; state.selStone = null;
  $("end").value = ei;
  render(); seekCurrent();
}

/* ----------------------------------------------------------------- render */

function render() {
  const e = end(), s = shot();
  $("label").textContent = s ? (s.label || `shot ${s.number}`) : "no shots detected";

  // Status for the phone header, where the shot chip strip does not fit.
  const chip = $("stoneChip");
  chip.textContent = s ? (isBlank(s) ? "?" : s.number) : "";
  chip.className = s ? `chip ${s.color} ${isBlank(s) ? "unknown" : ""}` : "chip";
  chip.hidden = !s;

  const flags = [];
  if (isBlank(s))
    flags.push(`This shot's house could not be read. Place the stones as they
      were, then mark it charted — a blank here is not an empty house.`);
  if (s?.missing && typeof s.t_guess_s === "number")
    flags.push(`This rock was never seen, so the video starts at a guess —
      about ${TYPICAL_GAP_S} s a shot from the nearest rock that was. If it was
      really thrown earlier in the end, move it under <strong>Order</strong>.`);
  else if (s?.color_inferred)
    flags.push(`No new stone appeared, so the thrower comes from the
      alternation rule rather than from seeing the rock arrive.`);
  if (s?.shot_type === "unknown" && !isBlank(s))
    flags.push(`Too little of the flight was seen to name the shot — pick a
      type if you can tell from the video.`);
  // A short end must never look like a whole one. Where the missing rocks
  // could not even be placed, say how many are unaccounted for.
  if (e.unplaced_shots)
    flags.push(`This end is <strong>${e.unplaced_shots} rocks short</strong> of
      the 16 that are thrown, and we could not tell where they belong — so they
      are not listed below. Shot numbering after a gap may name the wrong
      thrower.`);
  $("flags").innerHTML = flags.map(f => `<div class="warn">${f}</div>`).join("");

  const link = $("link"), t = shotVideoTime(s);
  if (s && (s.youtube_url || t !== null)) {
    link.href = t !== null
      ? `https://youtu.be/${state.doc.source.video_id}?t=${Math.floor(t)}`
      : s.youtube_url;
    link.style.visibility = "visible";
  } else link.style.visibility = "hidden";

  drawHouse();

  $("shots").innerHTML = mergedShots(e).map((sh, i) =>
    `<div class="shot ${sh.color} ${isBlank(sh) ? "unknown" : ""}
      ${isGraded(sh) ? "graded" : ""} ${i === state.si ? "sel" : ""}"
      data-i="${i}" title="${esc(sh.label || "")}">${isBlank(sh) ? "?" : sh.number}</div>`
  ).join("");
  [...$("shots").children].forEach(n =>
    n.onclick = () => { state.si = +n.dataset.i; state.selStone = null;
                        render(); seekCurrent(); });

  $("pickRed").classList.toggle("on", state.placeColor === "red");
  $("pickYellow").classList.toggle("on", state.placeColor === "yellow");
  $("prev").disabled = state.si <= 0;
  $("next").disabled = state.si >= e.shots.length - 1;
  $("delStone").disabled = state.selStone === null;
  $("markThrown").disabled = state.selStone === null;
  $("resetShot").disabled = !(shotKey() && state.overrides[shotKey()]);

  renderChart();
  renderQueue();
  scoreTable();
  if (state.reporting) renderReport();
}

function scoreTable() {
  const g = game(), ends = g.ends;
  const head = "<tr><th class='name'></th>" +
    ends.map(e => `<th>${e.number}</th>`).join("") + "<th>Tot</th></tr>";
  const rows = ["red","yellow"].map(c =>
    `<tr><td class="name"><span class="swatch" style="background:var(--${c})"></span>` +
    `${esc(g.teams[c].name || c)}</td>` +
    ends.map(e => `<td>${e.score[c] || "&ndash;"}</td>`).join("") +
    `<td><strong>${g.final[c]}</strong></td></tr>`).join("");
  $("score").innerHTML = head + rows;
}

/* ----------------------------------------------------------------- report */

const POSITIONS = ["lead", "second", "third", "skip"];

function gatherStats() {
  const out = {};
  for (const c of ["red","yellow"]) {
    out[c] = {};
    for (const p of POSITIONS) out[c][p] = { thrown:0, graded:0, sum:0, types:{} };
  }
  for (const e of game().ends)
    for (const s of mergedShots(e)) {
      const bucket = out[s.color]?.[s.position];
      if (!bucket) continue;
      const id = typeOf(s);
      const row = bucket.types[id] ||= { thrown:0, graded:0, sum:0 };
      bucket.thrown++; row.thrown++;
      if (isGraded(s) && !TYPE[id]?.unscored) {
        bucket.graded++; bucket.sum += s.user_score;
        row.graded++; row.sum += s.user_score;
      }
    }
  return out;
}

const pct = r => r.graded ? `${(100 * r.sum / (4 * r.graded)).toFixed(0)}%` : "—";
const avg = r => r.graded ? (r.sum / r.graded).toFixed(2) : "—";

function renderReport() {
  const stats = gatherStats();
  const g = game();
  const cards = ["red","yellow"].map(c => {
    const teamTotal = { thrown:0, graded:0, sum:0 };
    const players = POSITIONS.map(p => {
      const b = stats[c][p];
      teamTotal.thrown += b.thrown; teamTotal.graded += b.graded;
      teamTotal.sum += b.sum;
      if (!b.thrown) return "";
      const byGroup = GROUPS.map(grp => {
        const ids = Object.keys(b.types)
          .filter(id => (TYPE[id]?.group || "Other") === grp);
        if (!ids.length) return "";
        const sub = ids.map(id =>
          `<tr><td class="sub">${esc(TYPE[id]?.name || id)}</td>` +
          `<td>${b.types[id].thrown}</td><td>${b.types[id].graded}</td>` +
          `<td>${avg(b.types[id])}</td><td class="pct">${pct(b.types[id])}</td></tr>`
        ).join("");
        return `<tr><td class="name">${grp}</td><td colspan="4"></td></tr>` + sub;
      }).join("");
      return `<h3>${p}</h3>
        <table><tr><th class="name"></th><th>thrown</th><th>graded</th>
        <th>avg</th><th>%</th></tr>${byGroup}
        <tr class="total"><td class="name">all</td><td>${b.thrown}</td>
        <td>${b.graded}</td><td>${avg(b)}</td><td class="pct">${pct(b)}</td></tr>
        </table>`;
    }).join("");
    return `<div class="card"><h2><span class="swatch"
      style="background:var(--${c})"></span>${esc(g.teams[c].name || c)}</h2>
      ${players || '<div class="muted">No shots.</div>'}
      <table style="margin-top:12px"><tr class="total">
        <td class="name">team</td><td>${teamTotal.thrown}</td>
        <td>${teamTotal.graded}</td><td>${avg(teamTotal)}</td>
        <td class="pct">${pct(teamTotal)}</td></tr></table></div>`;
  }).join("");

  const total = ["red","yellow"].reduce((n,c) =>
    n + POSITIONS.reduce((m,p) => m + stats[c][p].thrown, 0), 0);
  const graded = ["red","yellow"].reduce((n,c) =>
    n + POSITIONS.reduce((m,p) => m + stats[c][p].graded, 0), 0);
  $("report").innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <h1>Game ${state.gi + 1} report</h1>
      <span class="muted">${graded} of ${total} shots graded</span>
      <span class="grow"></span>
      <button onclick="print()" class="noprint">Print</button>
    </div>
    ${graded < total ? `<div class="warn noprint">Percentages cover only the
      ${graded} graded shots. Ungraded shots are counted as thrown, never as
      misses.</div>` : ""}
    <div class="reportgrid" style="margin-top:12px">${cards}</div>`;
}

function toggleReport(on) {
  state.reporting = on ?? !state.reporting;
  document.body.classList.toggle("reporting", state.reporting);
  $("report").classList.toggle("show", state.reporting);
  $("reportBtn").classList.toggle("on", state.reporting);
  if (state.reporting) renderReport();
}

/* -------------------------------------------------------------- keyboard */

function typing(ev) {
  const n = ev.target;
  return n && (n.tagName === "INPUT" || n.tagName === "TEXTAREA" ||
               n.isContentEditable);
}

function onKey(ev) {
  if (typing(ev)) { if (ev.key === "Escape") ev.target.blur(); return; }
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const e = end();
  switch (ev.key) {
    case "ArrowLeft":  if (state.si > 0) goTo(state.ei, state.si - 1); break;
    case "ArrowRight": if (state.si < e.shots.length - 1) goTo(state.ei, state.si + 1); break;
    case "n": nextBlank(); break;
    case "r": state.placeColor = "red"; render(); break;
    case "y": state.placeColor = "yellow"; render(); break;
    case "t": $("showTrack").click(); break;
    case "v": { const t = shotVideoTime(shot()); if (t !== null) seekTo(t); break; }
    case "p": if (state.player && state.player !== "none")
                state.player.getPlayerState() === 1
                  ? state.player.pauseVideo() : state.player.playVideo();
              break;
    case "x": if (state.selStone !== null) $("delStone").click(); break;
    case "d": if (state.selStone !== null) $("markThrown").click(); break;
    case "c": if (state.selStone !== null) toggleStoneColor(); break;
    case "Enter": markCharted(); break;
    case "Escape": if (state.reporting) toggleReport(false); break;
    case "0": case "1": case "2": case "3": case "4":
      patchShot({ user_score:+ev.key }); break;
    default: return;
  }
  ev.preventDefault();
}

function toggleStoneColor() {
  const i = state.selStone;
  editStones(stones => {
    if (stones[i]) stones[i].color = stones[i].color === "red" ? "yellow" : "red";
    return stones;
  });
}

function markCharted() {
  patchShot({ state_known:true });
  nextBlank();
}

/* ------------------------------------------------------------------- boot */

function fillEnds() {
  $("end").innerHTML = game().ends.map((e, i) =>
    `<option value="${i}">End ${e.number} (${e.house})</option>`).join("");
  $("end").value = state.ei;
}

function restorePrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("curlchart") || "{}");
    if (typeof p.showTrack === "boolean") state.showTrack = p.showTrack;
    if (typeof p.autoplay === "boolean") state.autoplay = p.autoplay;
    if (typeof p.leadIn === "number") state.leadIn = p.leadIn;
  } catch { /* a fresh browser, or storage blocked: defaults are fine */ }
  $("showTrack").checked = state.showTrack;
  $("autoplay").checked = state.autoplay;
  $("leadin").value = state.leadIn;
}

function savePrefs() {
  try {
    localStorage.setItem("curlchart", JSON.stringify({
      showTrack:state.showTrack, autoplay:state.autoplay, leadIn:state.leadIn }));
  } catch { /* not important enough to bother the user about */ }
}

/* Exported for the node tests, which exercise the merge and the report
 * arithmetic without a browser. Harmless in one. */
if (typeof module !== "undefined" && module.exports)
  module.exports = { state, merge, keyFor, shotKey, rawShot, mergedShots, layout, identity, READ_ONLY,
                     gatherStats, pct, avg,
                     isBlank, isGraded, typeOf, shotVideoTime, TYPE, TYPES,
                     GROUPS, POSITIONS, stoneAt, R, LIMIT,
                     houseViewBox, peekMode, renumberNotice };

if (BROWSER) boot();

function boot() { Promise.all([
  fetch("timeline.json").then(r => r.json()),
  fetch("overrides.json").then(async r => {
    // The hosted API versions the overrides through an ETag; the local server
    // has none, and then saves are unconditional.
    const tag = r.headers.get("ETag");
    if (tag) { const n = parseInt(tag.replace(/"/g, ""), 10); if (!isNaN(n)) state.version = n; }
    return r.ok ? r.json() : {};
  }).catch(() => ({})),
]).then(([d, ov]) => {
  state.doc = d;
  state.overrides = (ov && typeof ov === "object" && !Array.isArray(ov)) ? ov : {};
  if (READ_ONLY) {
    document.body.dataset.mode = "view";
    for (const id of ["download", "delStone", "markThrown", "resetShot", "markCharted",
                      "orderBox", "orderRow", "recolour", "houseDone", "placeStones"]) {
      const el = $(id); if (el) el.hidden = true;   // placeStones lands in Task 7
    }
    $("save").hidden = true;
  }
  $("copyLink").onclick = async () => {
    try { await navigator.clipboard.writeText(location.href); $("copyLink").textContent = "Copied ✓"; }
    catch { prompt("Copy this link:", location.href); }
    setTimeout(() => { $("copyLink").textContent = "Copy link"; }, 1500);
  };
  const share = d.chart && d.chart.share_url;
  if (share && !READ_ONLY) {
    $("shareLink").hidden = false;
    $("shareLink").onclick = async () => {
      try { await navigator.clipboard.writeText(share); $("shareLink").textContent = "Copied ✓"; }
      catch { prompt("View-only link:", share); }
      setTimeout(() => { $("shareLink").textContent = "View-only link"; }, 1500);
    };
  }

  const src = d.source;
  $("src").innerHTML = `sheet ${esc(src.sheet ?? "?")} &middot; ` +
    `<a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.video_id)}</a>`;
  $("game").innerHTML = d.games.map((g, i) =>
    `<option value="${i}">Game ${i+1} (${g.ends.length} ends)</option>`).join("");
  $("missList").innerHTML = MISS_REASONS.map(r => `<option value="${r}">`).join("");

  $("game").onchange = ev => { state.gi = +ev.target.value; state.ei = 0;
                               state.si = 0; fillEnds(); render(); seekCurrent(); };
  $("end").onchange = ev => goTo(+ev.target.value, 0);
  $("prev").onclick = () => { if (state.si > 0) goTo(state.ei, state.si - 1); };
  $("next").onclick = () => { if (state.si < end().shots.length - 1)
                                goTo(state.ei, state.si + 1); };
  $("replay").onclick = () => { const t = shotVideoTime(shot()); if (t !== null) seekTo(t); };
  $("blanks").onclick = nextBlank;
  $("download").onclick = download;
  $("reportBtn").onclick = () => toggleReport();
  $("pickRed").onclick = () => { state.placeColor = "red"; render(); };
  $("pickYellow").onclick = () => { state.placeColor = "yellow"; render(); };
  $("delStone").onclick = () => {
    if (state.selStone !== null) removeStone(state.selStone);
  };
  $("markThrown").onclick = () => patchShot({ delivered_stone_index:state.selStone });
  $("markCharted").onclick = markCharted;
  $("resetShot").onclick = clearShot;
  $("menuBtn").onclick = () => {
    const open = document.body.dataset.menu === "open";
    document.body.dataset.menu = open ? "" : "open";
  };
  // Closing on pointerdown would hide #menu before the click could land, so the
  // control never fires. Close on click instead -- after it has acted. #playback
  // is a sibling panel, not a child of #menu, so it needs its own guard, and it
  // deliberately stays open while you adjust the settings in it.
  addEventListener("pointerdown", ev => {
    if (document.body.dataset.menu !== "open") return;
    if (ev.target.closest("#menuBtn")) return;
    if (ev.target.closest("#menu")) return;
    if (ev.target.closest("#playback")) return;
    document.body.dataset.menu = "";
  }, true);
  for (const ev of ["click", "change"])
    $("menu").addEventListener(ev, () => { document.body.dataset.menu = ""; });
  $("showTrack").onchange = ev => { state.showTrack = ev.target.checked;
                                    savePrefs(); drawHouse(); };
  $("autoplay").onchange = ev => { state.autoplay = ev.target.checked; savePrefs(); };
  $("leadin").onchange = ev => { state.leadIn = Math.max(0, +ev.target.value || 0);
                                 savePrefs(); render(); };
  $("missReason").oninput = ev => patchShot({ miss_reason:ev.target.value || null });
  $("note").oninput = ev => patchShot({ note:ev.target.value || null });
  $("moveBefore").onchange = ev => {
    // Follow the shot to its new place rather than staying on its old slot.
    const id = identity(shot());
    if (ev.target.value === "") unpatchShot("before");
    else patchShot({ before:+ev.target.value });
    const at = mergedShots(end()).findIndex(x => identity(x) === id);
    if (at >= 0 && at !== state.si) { state.si = at; render(); }
  };

  restorePrefs();
  bindHouse();
  addEventListener("keydown", onKey);
  fillEnds();
  render();
  setSave("saved", Object.keys(state.overrides).length ? "loaded" : "no edits yet");
  loadPlayer();
  seekCurrent();
}).catch(err => {
  document.body.innerHTML =
    `<main><div class="card">Could not load timeline.json — ${esc(err)}</div></main>`;
}); }
