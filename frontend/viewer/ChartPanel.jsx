/* Everything a charter fills in, plus the read-only detail beside it. */
import { Fragment, useEffect, useRef, useState } from "react";
import {
  GROUPS, MISS_REASONS, TALLBOX, TYPES,
  blankQueue, clockText, identity, isBlank, isGraded, splitText, thinkText, typeOf,
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
  useEffect(() => {
    // Not on mount. The effect runs once with the value the shot already has,
    // and committing that would write `{miss_reason: null}` for every rock the
    // charter merely looked at -- marking the chart dirty and saving nothing.
    if (!typed.current) { typed.current = true; return; }
    const id = setTimeout(() => commit(text || null), 150);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return {
    value: text,
    onChange: e => setText(e.target.value),
    onFocus: () => busy(true),
    onBlur: () => busy(false),
  };
}

function TypePicker({ shot, openGroup, readOnly, actions }) {
  const current = typeOf(shot);
  const group = openGroup || TYPES.find(t => t.id === current)?.group || "Draw";
  useEffect(() => { if (!openGroup) actions.openGroup(group); }, [openGroup, group, actions]);
  return (
    <>
      <h2>Shot type</h2>
      <div className="pick" id="typeGroups">
        {GROUPS.map(g => (
          <button key={g} data-g={g} className={g === group ? "on" : ""}
                  onClick={() => actions.openGroup(g)}>{g}</button>
        ))}
      </div>
      <div className="pick" id="typeList" style={{ marginTop: 6 }}>
        {TYPES.filter(t => t.group === group).map(t => (
          <button key={t.id} data-t={t.id} className={t.id === current ? "on" : ""}
                  onClick={() => !readOnly && actions.setType(t.id)}>{t.name}</button>
        ))}
      </div>
      <div className="muted" id="autoType" style={{ fontSize: 12, marginTop: 6 }}>
        {shot?.shot_type_source === "manual"
          ? `detector said ${actions.rawType() || "unknown"}`
          : `auto${shot?.shot_type_confidence
              ? ` · confidence ${shot.shot_type_confidence.toFixed(2)}` : ""}`}
      </div>
    </>
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
      <TypePicker shot={shot} openGroup={openGroup} readOnly={readOnly} actions={actions} />

      <h3>Score</h3>
      <div className="pick scorebtns" id="scoreBtns">
        {[0, 1, 2, 3, 4].map(v => (
          <button key={v} data-v={v} className={shot?.user_score === v ? "on" : ""}
                  onClick={() => !readOnly && actions.patch({ user_score: v })}>{v}</button>
        ))}
        <button data-v="" title="Clear the score"
                onClick={() => !readOnly && actions.patch({ user_score: null })}>&mdash;</button>
      </div>

      <h3>Miss reason</h3>
      <input type="text" id="missReason" list="missList" placeholder="&mdash;" {...miss} />
      <datalist id="missList">
        {MISS_REASONS.map(r => <option key={r} value={r} />)}
      </datalist>

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
        <select id="moveBefore" title="Where in the end this rock was really thrown"
                disabled={!shot || readOnly}
                value={shot?.before ?? ""}
                onChange={e => actions.moveBefore(e.target.value)}>
          <option value="">detected order</option>
          {others.map(x => (
            <option key={identity(x)} value={identity(x)}>
              {`before #${x.number} (${x.color}${isBlank(x) ? ", blank" : ""})`}
            </option>
          ))}
        </select>
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Detection missed a rock and put its blank in the wrong place? Move the
          blank to where it was thrown; the whole end renumbers.
        </div>
      </div>
    </div>
  );
}

function Detail({ shot }) {
  const d = shot?.house_delta;
  const delta = !d ? "—"
    : [d.removed?.length && `${d.removed.length} out`,
       d.added?.length && `${d.added.length} in`,
       d.moved?.length && `${d.moved.length} moved`].filter(Boolean).join(", ") || "no change";
  const rows = [
    ["Thrower", `${shot?.position ?? "—"}${shot ? ` (rock ${shot.rock_of_player})` : ""}`],
    ["Weight", shot?.entry_speed_m_s != null ? `${shot.entry_speed_m_s.toFixed(2)} m/s` : "—"],
    ["Travel", shot?.travel_m != null ? `${shot.travel_m.toFixed(2)} m` : "—"],
    ["Long split", splitText(shot)],
    ["Thinking", thinkText(shot)],
    ["House", delta],
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

function Scoreboard({ game }) {
  return (
    <details id="scoreBox">
      <summary>Scoreboard (detected &mdash; not a goal of this tool)</summary>
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
              {game.ends.map(e => <td key={e.number}>{e.score[c] || "–"}</td>)}
              <td><strong>{game.final[c]}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
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

      <Detail shot={shot} />

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

      <Scoreboard game={view.game} />

      {/* No `hidden`: a live region only announces from inside the
          accessibility tree, so it stays there and only its text changes. */}
      <div id="renumbered" role="status">{notice || ""}</div>
    </aside>
  );
}
