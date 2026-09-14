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
  MISS_REASONS, PHONE_QUERY,
  buildGameView, cumulativeThinking, cursor as cursorOf, gatherStats, gatherThinking,
  identity, isBlank, isGraded, keyFor, nextBlankAfter, blankQueue, peekMode,
  renumberNotice, shotVideoTime, typeOf, overrides as edit,
} from "../core/index.mjs";
import * as store from "../runtime/overridesStore.mjs";
import * as player from "../runtime/player.mjs";
import * as dragStore from "../runtime/dragStore.mjs";
import { writeBodyState } from "../runtime/bodyState.mjs";
import { loadPrefs, savePrefs, saveCursor } from "../runtime/prefs.mjs";
import { House } from "./House.jsx";
import { ChartPanel } from "./ChartPanel.jsx";
import { Report } from "./Report.jsx";

const phone = () => matchMedia(PHONE_QUERY).matches;

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

export function App({ doc, config }) {
  const [ui, dispatch] = useReducer(reducer, null, () => ({
    gi: 0, ei: 0, si: 0, selStone: null, placeColor: "red", openGroup: null,
    sheet: "peek", houseMode: "", menu: undefined, reporting: false, notice: null,
    ...loadPrefs(),
  }));

  const overrides = useSyncExternalStore(store.subscribe, store.getOverrides);
  const status = useSyncExternalStore(store.subscribe, store.getStatus);

  /* One relayout of the game, which the strip, the queue, the clock and the
   * report all read off. */
  const view = useMemo(() => buildGameView(doc, ui.gi, overrides),
                       [doc, ui.gi, overrides]);
  const { end, shot, raw, key: shotKey } = cursorOf(view, ui.ei, ui.si);
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
  };

  const noticeTimer = useRef(null);
  const notify = useCallback(text => {
    dispatch({ type: "set", patch: { notice: text } });
    clearTimeout(noticeTimer.current);
    if (text) noticeTimer.current = setTimeout(
      () => dispatch({ type: "set", patch: { notice: null } }), 5000);
  }, []);

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

  const setPref = useCallback(patchObj => {
    dispatch({ type: "set", patch: patchObj });
    savePrefs({ ...ui, ...patchObj });
  }, [ui]);

  const actions = useMemo(() => ({
    goTo,
    patch,
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
    goToBarFromReport: b => {
      // A bar in the report is still a rock you can go and watch; going there
      // closes the report rather than leaving it over the shot it just
      // took you to.
      dispatch({ type: "set", patch: { reporting: false } });
      goTo(b.ei, b.si);
    },
  }), [goTo, patch, setPref, raw, shot, shotKey, doc, ui.gi, ui.ei, ui.si, ui.sheet, notify]);

  /* ---------------------------------------------------------------- effects */

  useLayoutEffect(() => { writeBodyState(bodyFlags); });

  useEffect(() => {
    store.setNotice(n => notify(
      `Someone else charted ${n} shot${n === 1 ? "" : "s"}`));
  }, [notify]);

  useEffect(() => { saveCursor(config.slug, ui); }, [config.slug, ui.gi, ui.ei, ui.si]);

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
          <Flags shot={shot} />
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

        <section className="card" id="houseCard">
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
                 cropDeps={[ui.sheet, ui.houseMode, ui.ei, ui.si, ui.gi]} />
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

      <section id="report" className={ui.reporting ? "show" : undefined}>
        <Report view={view} stats={stats} think={think} series={series} actions={actions} />
      </section>
    </>
  );
}

function Flags({ shot }) {
  const flags = [];
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
        <CopyButton id="copyLink" title="Copy this page's link" label="Copy link"
                    text={() => location.href} />
        <CopyButton id="shareLink" title="Copy a link others can view but not edit"
                    label="View-only link" text={() => doc.chart.share_url}
                    hidden={!(doc.chart?.share_url && !config.readOnly)} />
        <button id="download" title="Save overrides.json to disk" hidden={config.readOnly}
                onClick={() => store.download()}>⬇</button>
        <button id="reportBtn" className={ui.reporting ? "on" : undefined}
                onClick={() => dispatch({ type: "set", patch: { reporting: !ui.reporting } })}>
          Report
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
