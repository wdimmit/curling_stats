/* The viewer.
 *
 * React owns what the charter is looking at. A plain module owns what the
 * server has (runtime/overridesStore). Refs own what the pointer is doing
 * (runtime/dragStore) and the video (runtime/player). The split is not
 * stylistic: each of those three is a machine whose correctness does not want
 * React's scheduling in the middle of it.
 */
import {
  useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef,
  useSyncExternalStore,
} from "react";
import {
  PHONE_QUERY,
  buildGameView, chartedNotice, cumulativeThinking, cursor as cursorOf, endKey, flagPlace,
  gatherStats, gatherThinking, formatWarning,
  identity, isBlank, isGraded, liveGame, nextBlankAfter, blankQueue, peekMode,
  renumberNotice, shotVideoTime, overrides as edit,
  stepRock, parseHash, formatHash, cursorFromHash, withHash,
} from "../core/index.mjs";
import * as store from "../runtime/overridesStore.mjs";
import * as player from "../runtime/player.mjs";
import { writeBodyState } from "../runtime/bodyState.mjs";
import { loadPrefs, savePrefs, saveCursor } from "../runtime/prefs.mjs";
import { House } from "./House.jsx";
import { DeskDetail } from "./Detail.jsx";
import { ChartPanel } from "./ChartPanel.jsx";
import { Report } from "./Report.jsx";
import { FlagDialog } from "./Flag.jsx";
import { Watch } from "./Watch.jsx";
import { useSwipe } from "./Pager.jsx";

const phone = () => matchMedia(PHONE_QUERY).matches;

/* The rock a hash names, in the game as it is now charted. The hash is written
 * from the view with the overrides applied, so it is read back against the
 * same: after a rock is moved under Order, its number in a link is its new
 * number, not the one the detector gave it. */
function linked(doc, hash) {
  const ov = store.getOverrides();
  return cursorFromHash(hash, gi => buildGameView(doc, gi, ov), doc.games.length);
}

function reducer(s, a) {
  switch (a.type) {
    case "goTo":
      return { ...s, ei: a.ei, si: a.si, selStone: null,
               notice: a.notice !== undefined ? a.notice : s.notice };
    case "game":   return { ...s, gi: a.gi, ei: 0, si: 0, selStone: null };
    case "stone":  return { ...s, selStone: a.i };
    case "set":    return { ...s, ...a.patch };
    default:       return s;
  }
}

export function App({ doc, config, cursor }) {
  const [ui, dispatch] = useReducer(reducer, null, () => {
    const prefs = loadPrefs();
    const hash = parseHash(location.hash);
    // A link names its rock; failing that, where this tab last was. A hash or
    // session cursor naming a rock the document no longer has is ignored --
    // the session's cursor is checked against the built view the same way.
    // store.start has already run (main.jsx), so the overrides are in.
    const link = linked(doc, hash);
    const savedView = cursor && doc.games[cursor.gi]
      ? buildGameView(doc, cursor.gi, store.getOverrides()) : null;
    const saved = savedView?.ends[cursor.ei]?.shots[cursor.si] ? cursor : null;
    return {
      gi: 0, ei: 0, si: 0, selStone: null, placeColor: "red", openGroup: null,
      sheet: "peek", houseMode: "", menu: undefined, reporting: false, flagging: null, notice: null,
      following: true,
      // A link or a restored session names a rock the video is not at yet:
      // seen once, on mount, to seek the player there (below). Not a pref
      // and not part of the saved cursor -- it says nothing once used.
      restored: !!(link ?? saved),
      ...prefs,
      ...(link ?? saved ?? {}),
      tab: hash.tab ?? prefs.tab,
    };
  });

  const overrides = useSyncExternalStore(store.subscribe, store.getOverrides);
  const status = useSyncExternalStore(store.subscribe, store.getStatus);

  /* One relayout of the game, which the strip, the queue, the clock and the
   * report all read off. */
  const view = useMemo(() => buildGameView(doc, ui.gi, overrides),
                       [doc, ui.gi, overrides]);
  const { shot, raw, key: shotKey } = cursorOf(view, ui.ei, ui.si);
  const series = useMemo(() => cumulativeThinking(view), [view]);
  const stats = useMemo(() => gatherStats(view), [view]);
  const think = useMemo(() => gatherThinking(view), [view]);

  /* Where the charter is on the clock: point 0 is the origin, so the cursor is
   * every rock in the ends before this one, plus this one. */
  const here = useMemo(() => {
    let at = ui.si + 1;
    for (let i = 0; i < ui.ei && i < view.ends.length; i++)
      at += view.ends[i].shots.length;
    return at;
  }, [view, ui.ei, ui.si]);

  const bodyFlags = {
    mode: config.review ? "review" : config.readOnly ? "view" : "",
    peek: peekMode(shot), sheet: ui.sheet, house: ui.houseMode,
    menu: ui.menu, reporting: ui.reporting,
    watch: config.readOnly ? ui.tab : "",
  };

  const noticeTimer = useRef(null);
  const notify = useCallback(text => {
    dispatch({ type: "set", patch: { notice: text } });
    clearTimeout(noticeTimer.current);
    if (text) noticeTimer.current = setTimeout(
      () => dispatch({ type: "set", patch: { notice: null } }), 5000);
  }, []);

  const closeFlag = useCallback(() => dispatch({ type: "set", patch: { flagging: null } }), []);

  /* --------------------------------------------------------------- actions */

  const seek = useCallback(t => { if (t !== null) player.seek(t, ui.autoplay); },
                           [ui.autoplay]);
  const seekTimer = useRef(null);
  const seekSoon = useCallback(s => {
    clearTimeout(seekTimer.current);
    // Holding an arrow key should not fire a seek per shot skipped over.
    seekTimer.current = setTimeout(() => seek(shotVideoTime(s, ui.leadIn)), 200);
  }, [seek, ui.leadIn]);

  const goTo = useCallback((ei, si, notice) => {
    dispatch({ type: "goTo", ei, si, notice });
    const at = view.ends[ei];
    seekSoon(at?.shots[si] ?? null);
  }, [view, seekSoon]);

  const patch = useCallback(fields => {
    if (config.readOnly || !shotKey) return;
    store.apply(edit.patch(store.getOverrides(), shotKey, fields), shotKey);
  }, [config.readOnly, shotKey]);

  const setSwapped = useCallback((color, on) => {
    const at = view.ends[ui.ei];
    if (config.readOnly || !at || !view.format.swappable) return;
    const key = endKey(view.game, at.end);
    const cur = store.getOverrides()[key]?.roles_swapped || {};
    store.apply(edit.patch(store.getOverrides(), key,
                           { roles_swapped: { ...cur, [color]: on } }), key);
  }, [config.readOnly, view, ui.ei]);

  const setPref = useCallback(patchObj => {
    dispatch({ type: "set", patch: patchObj });
    savePrefs({ ...ui, ...patchObj });
  }, [ui]);

  // Its own callback, not just a property of `actions`: `step` and `setTab`
  // below need to call it too, and an object literal cannot see its own
  // other properties while it is being built.
  const setFollowing = useCallback(on => dispatch({ type: "set", patch: { following: on } }), []);

  const actions = useMemo(() => ({
    goTo,
    patch,
    setSwapped,
    setBusy: store.setBusy,
    rawType: () => raw?.shot_type || "unknown",
    openGroup: g => dispatch({ type: "set", patch: { openGroup: g } }),
    setType: id => patch({ shot_type: id, shot_type_source: "manual" }),
    selectStone: i => dispatch({ type: "stone", i }),
    openHouseEditor: () => dispatch({ type: "set", patch: { houseMode: "edit" } }),
    toggleSheet: () => dispatch({ type: "set",
      patch: { sheet: ui.sheet === "open" ? "peek" : "open" } }),
    setClockOpen: open => setPref({ clockOpen: open }),
    setClockBars: bars => setPref({ clockBars: bars }),
    commitStones: stones => patch({ stones, state_known: true }),
    placeStone: stone => {
      if (!shot) return;
      const stones = [...edit.copyStones(shot), stone];
      const at = stones.length - 1;
      dispatch({ type: "stone", i: at });
      const fields = { stones, state_known: true };
      // On a blank shot the first rock of the thrower's colour is, by
      // construction, the one they just threw.
      if (isBlank(shot) && stone.color === shot.color &&
          shot.delivered_stone_index == null) fields.delivered_stone_index = at;
      patch(fields);
    },
    removeStone: i => {
      const fields = shot && edit.removeStone(shot, i);
      if (!fields) return;
      dispatch({ type: "stone", i: null });
      patch(fields);
    },
    moveBefore: value => {
      const was = shot, id = identity(was);
      const next = value === ""
        ? edit.unpatch(store.getOverrides(), shotKey, "before")
        : edit.patch(store.getOverrides(), shotKey, { before: +value });
      store.apply(next, shotKey);
      const shots = buildGameView(doc, ui.gi, next).ends[ui.ei].shots;
      const at = shots.findIndex(x => identity(x) === id);
      // Follow the shot to its new place rather than staying on its old slot.
      if (at >= 0 && at !== ui.si) dispatch({ type: "goTo", ei: ui.ei, si: at });
      notify(renumberNotice(was, at >= 0 ? shots[at] : null));
    },
    goToBar: b => { goTo(b.ei, b.si); },
    /* Watch mode. followTo moves the cursor *without* seeking: the video is
       already there -- it is what said so -- and seeking to where you already
       are would stutter the playback once a second. */
    followTo: si => dispatch({ type: "goTo", ei: ui.ei, si }),
    setFollowing,
    // A step seeks the video to the new rock (goTo, via seekSoon), so
    // following should resume, the same as a tap on a Timing row: House and
    // Detail have no "Back to rock" chip to turn it on again by hand.
    step: d => { const n = stepRock(view, ui.ei, ui.si, d); if (n) { setFollowing(true); goTo(n.ei, n.si); } },
    // The pager's end picker: the end's first rock, and the video with it.
    goToEnd: ei => { if (view.ends[ei]?.shots.length) { setFollowing(true); goTo(ei, 0); } },
    setTab: tab => { setPref({ tab }); if (tab !== "timing") setFollowing(true); },
    goToBarFromReport: b => {
      // A bar in the report is still a rock you can go and watch; going there
      // closes the report rather than leaving it over the shot it just
      // took you to.
      dispatch({ type: "set", patch: { reporting: false } });
      goTo(b.ei, b.si);
    },
  }), [goTo, patch, setSwapped, setPref, setFollowing, raw, shot, shotKey, doc, ui.gi, ui.ei, ui.si,
       ui.sheet, notify, view]);

  const houseSwipe = useSwipe(d => { if (phone()) actions.step(d); });

  /* ---------------------------------------------------------------- effects */

  useLayoutEffect(() => { writeBodyState(bodyFlags); });

  useEffect(() => {
    store.setNotice(keys => notify(chartedNotice(keys)));
  }, [notify]);

  useEffect(() => { saveCursor(config.slug, { gi: ui.gi, ei: ui.ei, si: ui.si }); },
            [config.slug, ui.gi, ui.ei, ui.si]);

  /* A linked or restored rock is where the video is, not at 0:00 -- seek there
   * once, on mount, without playing: the first press of play should choose to
   * play, not spend itself catching the video up to a cursor it never asked
   * to move. Empty deps: this is a one-time reconciliation of the video with
   * the cursor the reducer was seeded with, not a response to the cursor
   * moving afterward -- that is what stepping and seekSoon are for. */
  useEffect(() => {
    if (!ui.restored) return;
    const s = view.ends[ui.ei]?.shots[ui.si];
    if (typeof shotVideoTime(s, ui.leadIn) === "number")
      player.seek(shotVideoTime(s, ui.leadIn), false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Every surface keeps its rock in the URL, so a copied link -- or the address
  // bar -- opens where it was sent from. Only the phone's watching layout has
  // tabs, so only it names one. replaceState: stepping rocks is not history,
  // and it fires no hashchange for the listener below to answer.
  const written = useRef("");
  useEffect(() => {
    const e = view.ends[ui.ei]?.end?.number, s = view.ends[ui.ei]?.shots[ui.si]?.number;
    if (e == null || s == null) return;
    const tab = config.readOnly && phone() ? ui.tab : undefined;
    written.current = formatHash({ tab, g: ui.gi + 1, e, s });
    history.replaceState(null, "", written.current);
  }, [config.readOnly, view, ui.tab, ui.gi, ui.ei, ui.si]);

  /* A link to this chart opened in a tab already showing it changes only the
   * fragment: no reload, so nothing above runs again. Go to its rock, and put
   * the video there without playing -- the same as a link opened fresh. A
   * hash naming no rock we have leaves the cursor where it is, and the URL
   * goes back to naming it: otherwise the next Copy link would copy the
   * rock that is not there. */
  useEffect(() => {
    const onHash = () => {
      const hash = parseHash(location.hash);
      const to = linked(doc, hash);
      if (!to) {
        history.replaceState(null, "", written.current || location.pathname + location.search);
        return;
      }
      dispatch({ type: "set", patch: { ...to, selStone: null, following: true,
                                        ...(hash.tab ? { tab: hash.tab } : {}) } });
      const s = buildGameView(doc, to.gi, store.getOverrides()).ends[to.ei].shots[to.si];
      const t = shotVideoTime(s, ui.leadIn);
      if (typeof t === "number") player.seek(t, false);
    };
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, [doc, ui.leadIn]);

  /* The crop is measured, so it has to be re-measured when the box changes --
   * and a rotation that leaves the phone gate has to put the editor and the
   * sheet down, or the charter is stranded in an editor whose Done button the
   * short-screen layout never draws. */
  useEffect(() => {
    const onDown = ev => {
      if (ui.menu !== "open") return;
      if (ev.target.closest("#menuBtn") || ev.target.closest("#menu")
          || ev.target.closest("#playback")) return;
      dispatch({ type: "set", patch: { menu: "" } });
    };
    addEventListener("pointerdown", onDown, true);
    return () => removeEventListener("pointerdown", onDown, true);
  }, [ui.menu]);

  useEffect(() => {
    let t;
    const onResize = () => {
      clearTimeout(t);
      t = setTimeout(() => {
        if (!phone() && (ui.houseMode || ui.sheet !== "peek"))
          dispatch({ type: "set", patch: { houseMode: "", sheet: "peek" } });
        else dispatch({ type: "set", patch: {} });   // re-measure
      }, 120);
    };
    addEventListener("resize", onResize);
    return () => { removeEventListener("resize", onResize); clearTimeout(t); };
  }, [ui.houseMode, ui.sheet]);

  const shots = view.ends[ui.ei]?.shots ?? [];
  const queue = blankQueue(view).items;

  useEffect(() => {
    const typing = e => {
      const el = e.target;
      return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    };
    const onKey = ev => {
      // Behind the open flag dialog nothing steps, grades or seeks: the keys
      // belong to its buttons, and its textarea is covered by typing() anyway.
      if (document.getElementById("flagDialog")?.open) return;
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (typing(ev)) { if (ev.key === "Escape") ev.target.blur(); return; }
      switch (ev.key) {
        case "ArrowLeft":  if (ui.si > 0) goTo(ui.ei, ui.si - 1); break;
        case "ArrowRight": if (ui.si < shots.length - 1) goTo(ui.ei, ui.si + 1); break;
        case "n": { const it = nextBlankAfter(queue, ui.ei, ui.si);
                    if (it) goTo(it.ei, it.si); break; }
        case "r": dispatch({ type: "set", patch: { placeColor: "red" } }); break;
        case "y": dispatch({ type: "set", patch: { placeColor: "yellow" } }); break;
        case "t": setPref({ showTrack: !ui.showTrack }); break;
        case "v": seek(shotVideoTime(shot, ui.leadIn)); break;
        case "p": player.togglePlay(); break;
        case "x": if (ui.selStone !== null) actions.removeStone(ui.selStone); break;
        case "d": if (ui.selStone !== null) patch({ delivered_stone_index: ui.selStone }); break;
        case "c": if (ui.selStone !== null) {
                    const stones = edit.toggleStoneColor(shot, ui.selStone);
                    if (stones) patch({ stones, state_known: true });
                  } break;
        case "Enter": { patch({ state_known: true });
                        const it = nextBlankAfter(queue, ui.ei, ui.si);
                        if (it) goTo(it.ei, it.si); break; }
        case "Escape": dispatch({ type: "set", patch: { reporting: false } }); break;
        case "0": case "1": case "2": case "3": case "4":
          patch({ user_score: +ev.key }); break;
        default: return;
      }
      ev.preventDefault();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [ui, shots.length, queue, goTo, patch, seek, shot, actions, setPref]);

  /* ------------------------------------------------------------------ view */

  const videoRef = useRef(null);
  useEffect(() => {
    player.mount(videoRef.current, doc.source.video_id, { autoplay: () => ui.autoplay });
    // Mounted once and never torn down: YT.Player replaces the element it is
    // given with a cross-origin iframe, and a remount costs the player and
    // the seek. The phone hides it behind the editor with CSS instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const t = shotVideoTime(shot, ui.leadIn);
  const href = t !== null ? `https://youtu.be/${doc.source.video_id}?t=${Math.floor(t)}`
                          : shot?.youtube_url;

  return (
    <>
      <Header doc={doc} config={config} ui={ui} status={status} queue={queue}
              view={view} shot={shot} dispatch={dispatch} goTo={goTo} />

      <main>
        <section className="card" id="playCard">
          {/* Zero React children: YT.Player replaces what it is given, so if
              React held a child for that slot one reconciliation would remove
              the iframe. */}
          <div className="video" id="video" ref={videoRef} />
          <div className="row transport" id="transport">
            <button id="prev" title="Previous shot (←)" disabled={ui.si <= 0}
                    onClick={() => goTo(ui.ei, ui.si - 1)}>←</button>
            <button id="next" title="Next shot (→)" disabled={ui.si >= shots.length - 1}
                    onClick={() => goTo(ui.ei, ui.si + 1)}>→</button>
            <button id="replay" title="Replay from before the throw (v)"
                    onClick={() => seek(t)}>↻ Replay</button>
            <button id="markCharted" hidden={config.readOnly}
                    onClick={() => { patch({ state_known: true });
                                     const it = nextBlankAfter(queue, ui.ei, ui.si);
                                     if (it) goTo(it.ei, it.si); }}>✔ Mark charted (Enter)</button>
            <strong id="label" className="grow">
              {shot ? (shot.label || `shot ${shot.number}`) : "no shots detected"}
            </strong>
            <a id="link" target="_blank" rel="noopener" href={href}
               style={{ visibility: href ? "visible" : "hidden" }}>YouTube →</a>
          </div>
          <div className="row" id="playback">
            <label className="chk">
              <input type="checkbox" id="autoplay" checked={ui.autoplay}
                     onChange={e => setPref({ autoplay: e.target.checked })} /> play on jump
            </label>
            <label className="chk">lead-in
              <input type="number" id="leadin" min="0" max="60" step="1"
                     style={{ width: 58 }} defaultValue={ui.leadIn}
                     onChange={e => setPref({ leadIn: Math.max(0, +e.target.value || 0) })} />s
            </label>
          </div>
          <Flags doc={doc} shot={shot} trimmed={ui.ei === 0 ? doc.chart?.ends_trimmed : 0} />
          <div className="shots" id="shots">
            {shots.map((sh, i) => (
              <div key={i} data-i={i} title={sh.label || ""}
                   className={`shot ${sh.color} ${isBlank(sh) ? "unknown" : ""} `
                            + `${isGraded(sh) ? "graded" : ""} ${i === ui.si ? "sel" : ""}`}
                   onClick={() => goTo(ui.ei, i)}>
                {isBlank(sh) ? "?" : sh.number}
              </div>
            ))}
          </div>
        </section>

        {/* Always mounted: CSS decides who is visible (hidden on phones), the
            same rule that keeps the player from ever being reparented. */}
        <section className="card" id="detailCard" aria-label="Shot detail">
          <DeskDetail shot={shot} doc={doc} />
        </section>

        <section className="card" id="houseCard" {...(config.readOnly ? houseSwipe : {})}>
          <div className="tools">
            <button className={`swatchbtn red${ui.placeColor === "red" ? " on" : ""}`}
                    id="pickRed" title="Place red (r)"
                    onClick={() => dispatch({ type: "set", patch: { placeColor: "red" } })} />
            <button className={`swatchbtn yellow${ui.placeColor === "yellow" ? " on" : ""}`}
                    id="pickYellow" title="Place yellow (y)"
                    onClick={() => dispatch({ type: "set", patch: { placeColor: "yellow" } })} />
            <button id="delStone" title="Delete selected stone (x)" hidden={config.readOnly}
                    disabled={ui.selStone === null}
                    onClick={() => actions.removeStone(ui.selStone)}>⌫</button>
            <button id="markThrown" title="Mark selected as the delivered stone (d)"
                    hidden={config.readOnly} disabled={ui.selStone === null}
                    onClick={() => patch({ delivered_stone_index: ui.selStone })}>◎</button>
            <span className="grow" />
            <label className="chk">
              <input type="checkbox" id="showTrack" checked={ui.showTrack}
                     onChange={e => setPref({ showTrack: e.target.checked })} /> track
            </label>
            <button id="resetShot" title="Discard manual edits to this shot"
                    hidden={config.readOnly} disabled={!(shotKey && overrides[shotKey])}
                    onClick={() => store.apply(edit.clearKey(overrides, shotKey), shotKey)}>
              Reset
            </button>
          </div>
          <House shot={shot} shotKey={shotKey} selStone={ui.selStone}
                 houseMode={ui.houseMode} showTrack={ui.showTrack}
                 readOnly={config.readOnly} placeColor={ui.placeColor}
                 bodyFlags={bodyFlags} actions={actions}
                 cropDeps={[ui.sheet, ui.houseMode, ui.ei, ui.si, ui.gi, ui.tab]} />
          <div className="housebar">
            <button id="recolour" title="Swap the selected stone's colour (c)"
                    hidden={config.readOnly}
                    onClick={() => { const st = edit.toggleStoneColor(shot, ui.selStone);
                                     if (st) patch({ stones: st, state_known: true }); }}>⇄</button>
            <button id="houseDone" hidden={config.readOnly}
                    onClick={() => dispatch({ type: "set", patch: { houseMode: "" } })}>Done</button>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Click the ice to add a stone, drag to move, drag off the sheet to remove.
          </div>
        </section>

        <ChartPanel view={view} shot={shot} shotKey={shotKey}
                    cursor={{ ei: ui.ei, si: ui.si }} ui={ui} config={config}
                    series={series} here={here} notice={ui.notice} actions={actions} />
      </main>

      {/* Always mounted, like #report: CSS decides who is visible, so crossing
          the phone gate on a rotation can never reparent the player. */}
      {config.readOnly
        ? <Watch view={view} ui={ui} config={config} series={series}
                 think={think} here={here} actions={actions} />
        : null}

      <section id="report" className={ui.reporting ? "show" : undefined}>
        <Report view={view} stats={stats} think={think} series={series} actions={actions} />
      </section>
      {/* Outside <main>: the phone's watch layout hides everything in it
          but #playCard, and a dialog inside a hidden parent never shows. */}
      <FlagDialog flagging={ui.flagging} onClose={closeFlag} />
    </>
  );
}

function Flags({ doc, shot, trimmed }) {
  const flags = [];
  /* First, and about the whole game rather than this rock: the ends do not
   * look like the format they were analysed as. It lives here, not as a child
   * of <main>, because main is a grid whose areas are all spoken for on a
   * desktop -- an extra child drops to a row below the fold -- and on a phone
   * it would push the video down under the fixed house card. */
  const warning = formatWarning(doc);
  // Asked at the first end and nowhere else: "the stream opens with practice,
  // so where are those rocks?" Saying nothing would leave it looking like the
  // detector lost them. No advice on how to get them back, because there is
  // none to give: the boundary belongs to the game now, so every chart and
  // the review link alike start here.
  if (trimmed > 0)
    flags.push(`${trimmed} end${trimmed === 1 ? "" : "s"} of warm-up before the `
             + "game are not shown. This game starts at its first full end.");
  if (isBlank(shot))
    flags.push("This shot's house could not be read. Place the stones as they "
             + "were, then mark it charted — a blank here is not an empty house.");
  if (shot?.missing && typeof shot.t_guess_s === "number")
    flags.push("This rock was never seen, so the video starts at a guess — about "
             + "45 s a shot from the nearest rock that was. If it was really thrown "
             + "earlier in the end, move it under Order.");
  else if (shot?.color_inferred)
    flags.push("No new stone appeared, so the thrower comes from the alternation "
             + "rule rather than from seeing the rock arrive.");
  return (
    <div id="flags">
      {warning ? <div className="warn" id="formatWarning">{warning}</div> : null}
      {flags.map((f, i) => <div key={i} className="warn">{f}</div>)}
    </div>
  );
}

function Header({ doc, config, ui, status, queue, view, shot, dispatch, goTo }) {
  const src = doc.source;
  const unplaced = view.game.ends.reduce((n, e) => n + (e.unplaced_shots || 0), 0);
  const blanks = queue.length
    ? `${queue.length} to chart →`
    : unplaced ? `${unplaced} unaccounted for` : "all charted ✓";
  return (
    <header>
      <h1>{config.hosted ? <a href="/games" title="All games">Curling Chart</a>
                         : "Curling Chart"}</h1>
      <span className="muted" id="src">
        sheet {src.sheet ?? "?"} &middot;{" "}
        <a href={src.url} target="_blank" rel="noopener">{src.video_id}</a>
      </span>
      {liveGame(doc, view.game)
        ? <span id="livePill" className="pill live"
                title="Still being played: each end appears here once it is over">live</span>
        : null}
      <span id="stoneChip" className={shot ? `chip ${shot.color} ${isBlank(shot) ? "unknown" : ""}` : "chip"}
            hidden={!shot}>{shot ? (isBlank(shot) ? "?" : shot.number) : ""}</span>
      <span className="grow" />
      <button id="blanks" className={`pill ${queue.length || unplaced ? "bad" : "ok"}`}
              title="Jump to the next shot needing charting"
              onClick={() => { const it = nextBlankAfter(queue, ui.ei, ui.si);
                               if (it) goTo(it.ei, it.si); }}>{blanks}</button>
      <span id="save" className={`pill${status.cls === "saved" ? " ok"
                                      : status.cls === "failed" ? " bad" : ""}`}
            hidden={config.readOnly}>{status.text}</span>
      <button id="menuBtn" title="More"
              onClick={() => dispatch({ type: "set",
                patch: { menu: ui.menu === "open" ? "" : "open" } })}>⋯</button>
      <div id="menu" onClick={() => dispatch({ type: "set", patch: { menu: "" } })}
           onChange={() => dispatch({ type: "set", patch: { menu: "" } })}>
        <select id="game" aria-label="Game" value={ui.gi}
                onChange={e => dispatch({ type: "game", gi: +e.target.value })}>
          {doc.games.map((g, i) => (
            <option key={i} value={i}>{`Game ${i + 1} (${g.ends.length} ends)`}</option>
          ))}
        </select>
        <select id="end" aria-label="End" value={ui.ei}
                onChange={e => goTo(+e.target.value, 0)}>
          {view.game.ends.map((e, i) => (
            <option key={i} value={i}>{`End ${e.number} (${e.house})`}</option>
          ))}
        </select>
        <CopyButton id="copyLink" title="Copy a link to this rock" label="Copy link"
                    text={() => location.href} />
        <CopyButton id="shareLink" title="Copy a view-only link to this rock"
                    label="View-only link"
                    text={() => withHash(doc.chart.share_url, location.hash)}
                    hidden={!(doc.chart?.share_url && !config.readOnly)} />
        <button id="download" title="Save overrides.json to disk" hidden={config.readOnly}
                onClick={() => store.download()}>⬇</button>
        <button id="reportBtn" className={ui.reporting ? "on" : undefined}
                onClick={() => dispatch({ type: "set", patch: { reporting: !ui.reporting } })}>
          Report
        </button>
        <button id="flagBtn" title="Flag an issue with this rock" hidden={!config.hosted}
                onClick={() => {
                  // `opened` keys the dialog's form, so every opening starts fresh.
                  const at = flagPlace(view, ui.ei, ui.si, view.format);
                  if (at) dispatch({ type: "set",
                    patch: { flagging: { ...at, opened: Date.now() } } });
                }}>
          ⚑ Flag
        </button>
      </div>
    </header>
  );
}

function CopyButton({ id, title, label, text, hidden }) {
  const [said, setSaid] = useReducer((_, v) => v, label);
  return (
    <button id={id} title={title} hidden={hidden} onClick={async () => {
      const value = text();
      try { await navigator.clipboard.writeText(value); setSaid("Copied ✓"); }
      catch { prompt(`${label}:`, value); }
      setTimeout(() => setSaid(label), 1500);
    }}>{said}</button>
  );
}
