/* Everything a charter fills in, plus the read-only detail beside it. */
import { Fragment, useEffect, useRef, useState } from "react";
import {
  GROUPS, GROUP_TYPE, MISS_REASONS, TALLBOX, TYPES, subtypesOf,
  blankQueue, boardReadable, endSummary, identity, isBlank, liveGame, openGroupFor, scoreCell,
  unreadNote,
  houseDeltaText, thinkText, throwerText, typeOf,
} from "../core/index.mjs";
import { ClockKey, ThinkingBars, ThinkingChart } from "./Charts.jsx";

/* Local state keyed by the shot, so moving to another rock re-seeds the field
 * and staying on one never does. That is the React-shaped replacement for the
 * `document.activeElement !== el` guard the imperative version needed, and it
 * removes the caret-jump it was there to prevent. The upward dispatch is
 * debounced so the store sees keystroke batches rather than keystrokes. */
function useTypedField(initial, commit, busy) {
  const [text, setText] = useState(initial ?? "");
  const typed = useRef(false);
  const focused = useRef(false);
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
   * closed to every poll and save reply. The window crossing 1700 px swaps
   * the panel for the bar (or back) under the caret, so let go here. */
  useEffect(() => () => {
    if (focused.current) busy(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return {
    value: text,
    onChange: e => setText(e.target.value),
    onFocus: () => { focused.current = true; busy(true); },
    onBlur: () => { focused.current = false; busy(false); },
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
                             on => actions.setBusy(on ? shotKey : null));
  const note = useTypedField(shot?.note, v => actions.patch({ note: v }),
                             on => actions.setBusy(on ? shotKey : null));
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

/* The wide layout's grading (spec 2026-10-15 §1): the panel's controls in two
 * rows under the video. One of Grading and GradingBar renders, never both, so
 * every id stays unique. The renumbering notice goes with it: the panel that
 * carries it below 1700 px is not in the wide layout. */
export function GradingBar({ shot, shotKey, openGroup, readOnly, others, notice, actions }) {
  const t = useTypePicker({ shot, shotKey, openGroup, readOnly, actions });
  const miss = useTypedField(shot?.miss_reason, v => actions.patch({ miss_reason: v }),
                             on => actions.setBusy(on ? shotKey : null));
  const note = useTypedField(shot?.note, v => actions.patch({ note: v }),
                             on => actions.setBusy(on ? shotKey : null));
  return (
    <div id="grading" className="gbar">
      <div className="gbar-row">
        <span className="gbar-lbl">Shot type</span>
        <TypeGroups t={t} />
        <span className="gbar-sep" aria-hidden="true" />
        <TypeList t={t} readOnly={readOnly} actions={actions} />
        <AutoType shot={shot} actions={actions} />
      </div>
      <div className="gbar-row">
        <span className="gbar-lbl">Score</span>
        <ScoreButtons shot={shot} readOnly={readOnly} actions={actions} />
        <span className="gbar-sep" aria-hidden="true" />
        <label className="gbar-lbl" htmlFor="missReason">Miss</label>
        <input type="text" id="missReason" list="missList" placeholder="&mdash;" {...miss} />
        <MissList />
        <label className="gbar-lbl" htmlFor="note">Note</label>
        <textarea id="note" rows={1} placeholder="&mdash;" {...note} />
        <span className="gbar-sep" aria-hidden="true" />
        <label className="gbar-lbl" htmlFor="moveBefore">Order</label>
        <MoveBefore shot={shot} others={others} readOnly={readOnly} actions={actions}
                    title={"Where in the end this rock was really thrown. Detection missed a rock and put "
                         + "its blank in the wrong place? Move the blank to where it was thrown; the whole "
                         + "end renumbers."} />
      </div>
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
export function EndBox({ view, cursor, config, actions }) {
  const at = view.ends[cursor.ei];
  const s = endSummary(view, cursor.ei);
  const name = c => view.game.teams[c]?.name || c;
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
      {!config.readOnly && ["red", "yellow"].map(c => (
        <label key={c} className="chk">
          <input type="checkbox" checked={!!at?.swapped?.[c]}
                 onChange={e => actions.setSwapped(c, e.target.checked)} />
          {` ${name(c)} swapped roles (player B threw first and last)`}
        </label>
      ))}
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
