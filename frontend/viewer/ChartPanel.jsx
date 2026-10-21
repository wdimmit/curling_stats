/* Everything a charter fills in, plus the read-only detail beside it. */
import { Fragment, useEffect, useRef, useState } from "react";
import {
  GROUPS, GROUP_TYPE, MISS_REASONS, TALLBOX, TYPES, subtypesOf,
  blankQueue, boardReadable, endSummary, identity, isBlank, liveGame, openGroupFor, scoreCell,
  unreadNote,
  endLineText, houseDeltaText, sheetButtonText, swapText, thinkText, throwerText, typeOf,
} from "../core/index.mjs";
import { signInNow } from "../runtime/auth.mjs";
import { ClockKey, ThinkingBars, ThinkingChart } from "./Charts.jsx";
import { Sheet } from "./Sheet.jsx";

/* The typed field that had the caret when it was taken away, where in it,
 * and what it held: the window crossing 1500 px, or an iPad turning, swaps the
 * panel for the bar (or back) under the caret, and the field that replaces it
 * takes the caret back. Left on <body>, the next keys would reach the page's
 * shortcuts -- a digit sets the score, Enter marks the rock charted, x deletes
 * the selected stone. */
let refocus = null;

/* Local state keyed by the shot, so moving to another rock re-seeds the field
 * and staying on one never does. That is the React-shaped replacement for the
 * `document.activeElement !== el` guard the imperative version needed, and it
 * removes the caret-jump it was there to prevent. The upward dispatch is
 * debounced so the store sees keystroke batches rather than keystrokes.
 * `owner` is the shot's key: words are carried across a swap only to the same
 * rock's field, never into another rock's. */
function useTypedField(initial, commit, busy, owner) {
  const [text, setText] = useState(initial ?? "");
  const typed = useRef(false);
  const focused = useRef(null);     // the element, while it has the caret
  const el = useRef(null);
  useEffect(() => {
    // Unmounts run before mounts in one commit, so the swap is already noted.
    const r = refocus, node = el.current;
    if (!r || !node || r.id !== node.id) return;
    refocus = null;
    // Typed in the last 150 ms, so never committed: the field it was typed in
    // went with its debounce. This one takes the words over and commits them.
    if (r.owner === owner && r.value !== node.value) setText(r.value);
    node.focus();
    node.setSelectionRange(r.start, r.end);
    // Once, on mount, by design: `owner` is the shot's key, constant for the
    // field's life (its bar or panel is keyed by it).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    // Not on mount. The effect runs once with the value the shot already has,
    // and committing that would write `{miss_reason: null}` for every rock the
    // charter merely looked at -- marking the chart dirty and saving nothing.
    if (!typed.current) { typed.current = true; return; }
    const id = setTimeout(() => commit(text || null), 150);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  /* A field removed while it has the caret never blurs -- React's onBlur
   * does not fire for a node already gone -- and the rock would stay busy,
   * closed to every poll and save reply. The window crossing 1500 px swaps
   * the panel for the bar (or back) under the caret, so let go here. */
  useEffect(() => () => {
    const node = focused.current;
    if (!node) return;
    busy(false);
    refocus = { id: node.id, start: node.selectionStart, end: node.selectionEnd, value: node.value, owner };
    // Nothing took it back (no field of that id came in): forget it.
    setTimeout(() => { refocus = null; }, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return {
    ref: el,
    value: text,
    onChange: e => setText(e.target.value),
    // A read-only field (the iPad's sheets on /s/) changes nothing, so reading
    // it must not hold the rock busy against the polls that follow the game.
    onFocus: e => { focused.current = e.currentTarget; if (!e.currentTarget.readOnly) busy(true); },
    onBlur: () => { focused.current = null; busy(false); },
  };
}

/* The rule itself is in core/shots.mjs, where it is tested. This is only
 * "when": on arriving at a different rock, never on a re-render -- applied on
 * every render, clicking "Hit" to turn a draw into a hit would snap the row
 * back to Draw before the type could be picked. */
function useOpenGroup(shotKey, current, openGroup, actions) {
  const arrivedOn = useRef(null);
  useEffect(() => {
    if (arrivedOn.current === shotKey) return;
    arrivedOn.current = shotKey;
    const want = openGroupFor(current, openGroup);
    if (want !== openGroup) actions.openGroup(want);
  });
}

/* The type picker's state, and its parts, so the panel (stacked, under a
 * heading) and the wide layout's bar (one row) draw the same buttons. */
function useTypePicker({ shot, shotKey, openGroup, readOnly, actions }) {
  const current = typeOf(shot);
  useOpenGroup(shotKey, current, openGroup, actions);
  const group = openGroup;
  const base = GROUP_TYPE[group];

  /* Choosing a category is an answer in itself -- "a draw" is a complete
   * thing to say about a rock. Landing on the category you are already in is
   * only opening the row, though, and must not quietly throw away a
   * refinement already set; that is what the blank below is for. */
  const pickGroup = g => {
    actions.openGroup(g);
    const b = GROUP_TYPE[g];
    if (!readOnly && b && !TYPES.some(t => t.group === g && t.id === current))
      actions.setType(b);
  };
  return { current, group, base, pickGroup };
}

function TypeGroups({ t }) {
  return (
    <div className="pick" id="typeGroups">
      {GROUPS.map(g => (
        <button key={g} data-g={g} className={g === t.group ? "on" : ""}
                onClick={() => t.pickGroup(g)}>{g}</button>
      ))}
    </div>
  );
}

function TypeList({ t, readOnly, actions, style }) {
  const { group, base, current } = t;
  return (
    <div className="pick" id="typeList" style={style}>
      {/* Blank is a real answer, and the way back from a refinement. */}
      {base && (
        <button data-t="" title={`Just a ${group?.toLowerCase()}, with nothing more said`}
                className={current === base ? "on" : ""}
                onClick={() => !readOnly && actions.setType(base)}>&mdash;</button>
      )}
      {group && subtypesOf(group).map(x => (
        <button key={x.id} data-t={x.id} className={x.id === current ? "on" : ""}
                onClick={() => !readOnly && actions.setType(x.id)}>{x.name}</button>
      ))}
    </div>
  );
}

function AutoType({ shot, actions, style }) {
  return (
    <div className="muted" id="autoType" style={style}>
      {shot?.shot_type_source === "manual"
        ? `detector said ${actions.rawType() || "unknown"}`
        : `auto${shot?.shot_type_confidence
            ? ` · confidence ${shot.shot_type_confidence.toFixed(2)}` : ""}`}
    </div>
  );
}

function TypePicker({ shot, shotKey, openGroup, readOnly, actions }) {
  const t = useTypePicker({ shot, shotKey, openGroup, readOnly, actions });
  return (
    <>
      <h2>Shot type</h2>
      <TypeGroups t={t} />
      <TypeList t={t} readOnly={readOnly} actions={actions} style={{ marginTop: 6 }} />
      <AutoType shot={shot} actions={actions} style={{ fontSize: 12, marginTop: 6 }} />
    </>
  );
}

function ScoreButtons({ shot, readOnly, actions }) {
  return (
    <div className="pick scorebtns" id="scoreBtns">
      {[0, 1, 2, 3, 4].map(v => (
        <button key={v} data-v={v} className={shot?.user_score === v ? "on" : ""}
                onClick={() => !readOnly && actions.patch({ user_score: v })}>{v}</button>
      ))}
      <button data-v="" title="Clear the score"
              onClick={() => !readOnly && actions.patch({ user_score: null })}>&mdash;</button>
    </div>
  );
}

function MissList() {
  return (
    <datalist id="missList">
      {MISS_REASONS.map(r => <option key={r} value={r} />)}
    </datalist>
  );
}

function MoveBefore({ shot, others, readOnly, actions, title }) {
  return (
    <select id="moveBefore" title={title} disabled={!shot || readOnly}
            value={shot?.before ?? ""}
            onChange={e => actions.moveBefore(e.target.value)}>
      <option value="">detected order</option>
      {others.map(x => (
        <option key={identity(x)} value={identity(x)}>
          {`before #${x.number} (${x.color}${isBlank(x) ? ", blank" : ""})`}
        </option>
      ))}
    </select>
  );
}

function Grading({ shot, shotKey, openGroup, readOnly, others, actions }) {
  const miss = useTypedField(shot?.miss_reason, v => actions.patch({ miss_reason: v }),
                             on => actions.setBusy(on ? shotKey : null), shotKey);
  const note = useTypedField(shot?.note, v => actions.patch({ note: v }),
                             on => actions.setBusy(on ? shotKey : null), shotKey);
  const before = others.find(x => shot?.before === identity(x));
  return (
    <div id="grading">
      <TypePicker shot={shot} shotKey={shotKey} openGroup={openGroup}
                  readOnly={readOnly} actions={actions} />

      <h3>Score</h3>
      <ScoreButtons shot={shot} readOnly={readOnly} actions={actions} />

      <h3>Miss reason</h3>
      <input type="text" id="missReason" list="missList" placeholder="&mdash;" {...miss} />
      <MissList />

      <h3>Note</h3>
      <textarea id="note" placeholder="&mdash;" {...note} />

      {/* Sibling order is load-bearing: the phone lays #moveBefore over
          #orderRow with `#orderRow:not([hidden]) ~ #moveBefore`. */}
      <div id="orderBox" hidden={readOnly}>
        <h3>Order</h3>
        <button id="orderRow" type="button" disabled={!shot || readOnly} hidden={readOnly}>
          <span className="lbl">Order</span>
          <span id="orderRowValue">
            {before ? `before #${before.number} (${before.color})` : "detected order"}
          </span>
        </button>
        <button id="placeStones" type="button" hidden={readOnly}
                onClick={() => !readOnly && actions.openHouseEditor()}>Place the stones</button>
        <MoveBefore shot={shot} others={others} readOnly={readOnly} actions={actions}
                    title="Where in the end this rock was really thrown" />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Detection missed a rock and put its blank in the wrong place? Move the
          blank to where it was thrown; the whole end renumbers.
        </div>
      </div>
    </div>
  );
}

/* A sheet's button on the iPad (spec §6): its name, then the value once one
 * is set. Only the value gives way, ending in "…": the name never shrinks, so
 * the button always says what it opens. The text is sheetButtonText's, the
 * value cut at a word, so the accessible name is that whatever is drawn. */
function SheetButton({ id, label, value, setLabel = label, onClick }) {
  const text = sheetButtonText(label, value, setLabel);
  const head = value ? `${setLabel}:` : text;
  return (
    <button id={id} type="button" className="sheetbtn" onClick={onClick}>
      <span className="sb">
        <span className="sb-lbl">{head}</span>
        {value ? <>{" "}<span className="sb-val">{text.slice(head.length + 1)}</span></> : null}
      </span>
    </button>
  );
}

/* The wide layout's grading (spec 2026-10-15 §1): the panel's controls in two
 * rows under the video. One of Grading and GradingBar renders, never both, so
 * every id stays unique. The renumbering notice goes with it: the panel that
 * carries it below 1500 px is not in the wide layout.
 *
 * With `sheets` (the portrait iPad, spec 2026-10-16 §6) the score row keeps
 * the score and puts Miss, Note and Order behind buttons, each opening a
 * small sheet that holds the same field, then `next` at the right. The typed
 * fields are the same hooks either way, so the sheets save as the bar does. */
export function GradingBar({ shot, shotKey, openGroup, readOnly, others, notice, actions,
                             sheets = false, next = null }) {
  const t = useTypePicker({ shot, shotKey, openGroup, readOnly, actions });
  // Which sheet is open: one at a time. A sheet's close event clears only
  // itself, so a close that lands late cannot shut the next one.
  const [open, setOpen] = useState(null);
  const shut = which => () => setOpen(o => (o === which ? null : o));
  /* An iPad turned upright with the caret in Miss or Note: the field that takes
   * it back is in a closed sheet, where it cannot. So open that sheet, and once
   * it is open hand the field the caret (the effect after the fields'). This
   * effect comes before the fields' hooks, whose own mount effects consume the
   * note; it is there by now, since unmounts run before mounts in one commit. */
  const caret = useRef(null);
  useEffect(() => {
    // Only this rock's field: a note left by another rock's never opens a sheet here.
    const r = refocus?.owner === shotKey ? refocus : null;
    const which = !sheets ? null : r?.id === "note" ? "note" : r?.id === "missReason" ? "miss" : null;
    if (!which) return;
    caret.current = { which, start: r.start, end: r.end };
    setOpen(which);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const miss = useTypedField(shot?.miss_reason, v => actions.patch({ miss_reason: v }),
                             on => actions.setBusy(on ? shotKey : null), shotKey);
  const note = useTypedField(shot?.note, v => actions.patch({ note: v }),
                             on => actions.setBusy(on ? shotKey : null), shotKey);
  // A child's effects run before its parent's, so the sheet has called
  // showModal() by now, and its field can take focus.
  useEffect(() => {
    const c = caret.current;
    if (!c || c.which !== open) return;
    caret.current = null;
    const node = (c.which === "note" ? note : miss).ref.current;
    node?.focus();
    node?.setSelectionRange(c.start, c.end);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const before = others.find(x => shot?.before === identity(x));
  return (
    <div id="grading" className="gbar">
      {/* Each label and its control are one box (.gbar-pair), so a row that
          wraps at the narrow end of the wide layout never strands a label at
          the end of a line. The subtypes may take a line of their own. */}
      <div className="gbar-row">
        <div className="gbar-pair">
          <span className="gbar-lbl">Shot type</span>
          <TypeGroups t={t} />
        </div>
        <span className="gbar-sep" aria-hidden="true" />
        <TypeList t={t} readOnly={readOnly} actions={actions} />
        <AutoType shot={shot} actions={actions} />
      </div>
      {sheets ? (
        <>
          <div className="gbar-row">
            <div className="gbar-pair">
              <span className="gbar-lbl">Score</span>
              <ScoreButtons shot={shot} readOnly={readOnly} actions={actions} />
            </div>
            <div className="gbar-sheets">
              <SheetButton id="missBtn" label="Miss" value={miss.value} onClick={() => setOpen("miss")} />
              <SheetButton id="noteBtn" label="Note" value={note.value} onClick={() => setOpen("note")} />
              {/* Not on /s/, as the panel hides #orderBox there. */}
              {!readOnly ? (
                <SheetButton id="orderBtn" label="Order" value={before ? `#${before.number}` : null}
                             onClick={() => setOpen("order")} />
              ) : null}
            </div>
            {next}
          </div>
          {/* On /s/ the two fields open read-only, so a long note can be read in full. */}
          <Sheet id="missSheet" title="Miss reason" open={open === "miss"} onClose={shut("miss")}>
            <div className="reasons">
              {MISS_REASONS.map(r => (
                <button key={r} type="button" disabled={readOnly} className={miss.value === r ? "on" : ""}
                        onClick={() => miss.onChange({ target: { value: r } })}>{r}</button>
              ))}
            </div>
            <input type="text" id="missReason" list="missList" placeholder="&mdash;" readOnly={readOnly} {...miss} />
            <MissList />
          </Sheet>
          <Sheet id="noteSheet" title="Note" open={open === "note"} onClose={shut("note")}>
            <textarea id="note" rows={4} placeholder="&mdash;" readOnly={readOnly} {...note} />
          </Sheet>
          {!readOnly ? (
            <Sheet id="orderSheet" title="Order" open={open === "order"} onClose={shut("order")}>
              <MoveBefore shot={shot} others={others} readOnly={readOnly} actions={actions}
                          title="Where in the end this rock was really thrown" />
              <p className="muted">
                Detection missed a rock and put its blank in the wrong place? Move the
                blank to where it was thrown; the whole end renumbers.
              </p>
            </Sheet>
          ) : null}
        </>
      ) : (
        <div className="gbar-row">
          <div className="gbar-pair">
            <span className="gbar-lbl">Score</span>
            <ScoreButtons shot={shot} readOnly={readOnly} actions={actions} />
          </div>
          <span className="gbar-sep" aria-hidden="true" />
          <div className="gbar-pair">
            <label className="gbar-lbl" htmlFor="missReason">Miss</label>
            <input type="text" id="missReason" list="missList" placeholder="&mdash;" {...miss} />
            <MissList />
          </div>
          <div className="gbar-pair gbar-note">
            <label className="gbar-lbl" htmlFor="note">Note</label>
            <textarea id="note" rows={1} placeholder="&mdash;" {...note} />
          </div>
          {/* Not on /s/, as the panel hides #orderBox there. */}
          {!readOnly ? (
            <>
              <span className="gbar-sep" aria-hidden="true" />
              <div className="gbar-pair">
                <label className="gbar-lbl" htmlFor="moveBefore">Order</label>
                <MoveBefore shot={shot} others={others} readOnly={readOnly} actions={actions}
                            title={"Where in the end this rock was really thrown. Detection missed a rock and put "
                                 + "its blank in the wrong place? Move the blank to where it was thrown; the whole "
                                 + "end renumbers."} />
              </div>
            </>
          ) : null}
        </div>
      )}
      <div id="renumbered" role="status">{notice || ""}</div>
    </div>
  );
}

function Detail({ shot, format }) {
  // The long split is the Detail card's Weight figure now, so it is not
  // repeated here; this m/s number is the speed the rock entered the house at.
  const rows = [
    ["Thrower", throwerText(shot, format)],
    ["Entry speed", shot?.entry_speed_m_s != null ? `${shot.entry_speed_m_s.toFixed(2)} m/s` : "—"],
    ["Travel", shot?.travel_m != null ? `${shot.travel_m.toFixed(2)} m` : "—"],
    ["Thinking", thinkText(shot)],
    ["House", houseDeltaText(shot?.house_delta)],
    ["Evidence", shot?.reason ?? "—"],
    ["Stones", String(shot?.stones?.length ?? 0)],
  ];
  return (
    <dl id="detail">
      {rows.map(([k, v]) => (
        <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>
      ))}
    </dl>
  );
}

/* The clock beside the game rather than inside the report, because a review
 * link never reaches the report: it has no grading, so its button is hidden,
 * and the clock would have gone with it. This is detection like the house and
 * the shot list, and every mode gets it. */
function ClockBox({ series, here, bars, open, actions }) {
  const empty = !series.points.length || (!series.red && !series.yellow);
  return (
    <details id="clockBox" hidden={empty} open={open}
             onToggle={e => actions.setClockOpen(e.currentTarget.open)}>
      <summary>Thinking time</summary>
      <div id="clock">
        {!empty && (
          <>
            {bars
              ? <ThinkingBars series={series} at={here} box={TALLBOX} onSelect={actions.goToBar} />
              : <ThinkingChart series={series} at={here} box={TALLBOX} />}
            <ClockKey series={series}>
              <span className="grow" />
              <button id="clockMode" className="linky"
                      onClick={() => actions.setClockBars(!bars)}>
                {bars ? "totals" : "per rock"}
              </button>
            </ClockKey>
          </>
        )}
      </div>
    </details>
  );
}

function Scoreboard({ game, doc }) {
  const readable = boardReadable(doc);
  const board = readable ? game.scoreboard : null;
  const unread = board?.unread_ends ?? [];
     const entered = game.ends.some(e => e.score_source === "entered");
     return (
    <details id="scoreBox">
      <summary>Scoreboard (read from the wall board)</summary>
      {!readable ? (
        <p className="scorekey">
          This chart predates board reading &mdash; no score is shown.
        </p>
      ) : board?.scores_withheld && !entered ? (
        <p className="scorekey">
          The wall board was read, but its scores could not be matched to
          these ends. Setting this game&rsquo;s start time places them
          &mdash; the board will not need to be read again.
        </p>
      ) : board || entered ? (
        <>
          <table id="score">
            <tbody>
              <tr>
                <th className="name" />
                {game.ends.map(e => <th key={e.number}>{e.number}</th>)}
                <th>Tot</th>
              </tr>
              {["red", "yellow"].map(c => (
                <tr key={c}>
                  <td className="name">
                    <span className="swatch" style={{ background: `var(--${c})` }} />
                    {game.teams[c].name || c}
                  </td>
                  {game.ends.map(e => (
                       <td key={e.number} className={e.score == null ? "unread"
                         : e.score_source === "entered" ? "entered" : ""}>
                         {scoreCell(e.score, c)}
                    </td>
                  ))}
                  <td><strong>{game.final ? game.final[c] : "·"}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="scorekey">
            <span>&ndash; blank end</span>
            <span>&middot; not posted</span>
            {entered ? <span><u className="entered">2</u> entered by hand</span> : null}
            {unread.length ? <span>{unreadNote(unread, liveGame(doc, game))}</span> : null}
          </p>
        </>
      ) : (
        <p className="scorekey">The wall board could not be read for this game.</p>
      )}
    </details>
  );
}

/* The end's hammer and any power play, plus the swap checkboxes -- the
 * former read in every mode (a desktop viewer or a review link never reaches
 * Timing/EndHead, which is phone-watch only), the latter only where a
 * charter can act on them. */
export function EndBox({ view, cursor, config, actions, compact }) {
  const at = view.ends[cursor.ei];
  const s = endSummary(view, cursor.ei);
  const name = c => view.game.teams[c]?.name || c;
  const [open, setOpen] = useState(false);
  // What the last tick came to, when it was not saved: nobody signed in
  // ("signin"), or the server's words.
  const [note, setNote] = useState(null);
  const tick = async (c, on) => {
    const res = await actions.setSwapped(c, on);
    setNote(res?.signin ? "signin" : res?.error || null);
  };
  const signIn = async () => {
    try { await signInNow(); setNote(null); } catch { /* closed: the line stays */ }
  };
  // The game's first end is never switched: who threw its 1st & 5th is what
  // Player A means.
  const first = cursor.ei === 0;
  const swaps = first ? null : (
    <>
      {["red", "yellow"].map(c => (
        <label key={c} className="chk">
          <input type="checkbox" checked={!!at?.swapped?.[c]}
                 onChange={e => tick(c, e.target.checked)} />
          {` ${name(c)} switched this end: B threw 1st & 5th`}
        </label>
      ))}
      {note === "signin" ? (
        <p className="endnote">Sign in to mark switched ends{" "}
          <button type="button" onClick={signIn}>Sign in</button></p>
      ) : note ? <p className="endnote" role="alert">{note}</p> : null}
    </>
  );
  // The iPad's doubles: the end's facts in one line, the checkboxes in a sheet.
  if (compact) {
    return (
      <div id="endBox">
        <p className="endline">{endLineText(s, at?.end.number, name)}</p>
        {!config.readOnly && !first ? (
          <>
            <SheetButton id="swapBtn" label="Switched ends" value={swapText(at?.swapped || {}, name)}
                         setLabel="Switched" onClick={() => setOpen(true)} />
            <Sheet id="swapSheet" title="Switched this end" open={open} onClose={() => setOpen(false)}>
              {swaps}
            </Sheet>
          </>
        ) : null}
      </div>
    );
  }
  return (
    <div id="endBox">
      <h3>End {at?.end.number}</h3>
      {s ? (
        <div className="endfacts">
          {s.hammer ? <span>{`${name(s.hammer)} has hammer`
            + `${s.hammerSource === "placement" ? " (from the placement)" : ""}`}</span> : null}
          {s.powerPlay
            ? <span className="wpp">power play · {s.powerPlay.color}, {s.powerPlay.side}</span>
            : null}
        </div>
      ) : null}
      {!config.readOnly && swaps}
    </div>
  );
}

export function ChartPanel({ view, shot, shotKey, cursor, ui, config, series, here,
                             notice, actions }) {
  const { items } = blankQueue(view);
  const others = view.ends[cursor.ei]?.shots.filter(
    x => shot && identity(x) !== identity(shot)) ?? [];
  return (
    <aside className="card" id="chart">
      <button id="sheetHandle" aria-expanded={String(ui.sheet === "open")}
              aria-controls="chart" onClick={actions.toggleSheet}>
        <span className="grip" /><span className="sr">Expand the chart panel</span>
      </button>

      {/* Rendered in every mode. `body[data-mode="review"] #grading` is what
          hides it, and a rule cannot hide an element that is not there. */}
      <Grading key={shotKey} shot={shot} shotKey={shotKey} openGroup={ui.openGroup}
               readOnly={config.readOnly} others={others} actions={actions} />

      <Detail shot={shot} format={view.format} />

      {view.format.swappable ? (
        <EndBox view={view} cursor={cursor} config={config} actions={actions} />
      ) : null}

      <details id="queueBox">
        <summary>Needs charting (<span id="queueCount">{items.length}</span>)</summary>
        <div className="queue" id="queue">
          {items.length
            ? items.map((it, i) => (
                <button key={i} data-i={i}
                        onClick={() => actions.goTo(it.ei, it.si)}>{it.label}</button>))
            : <div className="muted" style={{ fontSize: 12 }}>Nothing needs charting.</div>}
        </div>
      </details>

      <ClockBox series={series} here={here} bars={ui.clockBars}
                open={ui.clockOpen} actions={actions} />

      <Scoreboard game={view.game} doc={view.doc} />

      {/* No `hidden`: a live region only announces from inside the
          accessibility tree, so it stays there and only its text changes. */}
      <div id="renumbered" role="status">{notice || ""}</div>
    </aside>
  );
}
