/* Timing: the whole game's clock above every rock, grouped by end. Each row
 * carries its thinking-time bar, so the list *is* the per-rock chart turned
 * on its side -- the one statistic that works on a game nobody has charted. */
import { useEffect, useRef } from "react";
import { clockText, endSummary, rockRows } from "../core/index.mjs";
import { TIMINGBOX } from "../core/constants.mjs";
import { ThinkingChart } from "./Charts.jsx";

// Rows are this tall (.wrow in style.css). The list keeps the current one in
// view by its measured offset, centring it by half a row.
const ROW_H = 56;

function RockRow({ row, current, onPick }) {
  return (
    <button type="button" className="wrow" aria-current={current || undefined}
            onClick={() => onPick(row.i)}>
      <span className={`wdisc ${row.color}`}>{row.number}</span>
      <span className="wmid">
        <span className="whead">
          <span className="wname">{row.name}</span>
          {row.splitText ? (
            <span className="wsplit" title="Long split: hog line to hog line">
              <span aria-hidden="true">·</span>{" "}
              <span className="sr">long split </span>{row.splitText}
            </span>
          ) : null}
        </span>
        <span className="wtrack">
          {row.unmeasured ? null
            : <i className={row.color} style={{ width: `${Math.max(4, row.frac * 100)}%` }} />}
        </span>
      </span>
      <span className="wright">
        {current ? <span className="wnow">▶ playing</span> : null}
        <span className="wsecs">{row.text}{row.estimated && !row.unmeasured ? " est." : ""}</span>
      </span>
    </button>
  );
}

function EndHead({ summary }) {
  const { number, running, hammer, boardReadable, scoresWithheld, powerPlay, gameLive } = summary;
  return (
    <div className="tend">
      <span className="wen">End {number}</span>
      {gameLive ? <span className="wlive">live</span> : null}
      {hammer ? <span className="wham">{hammer} has hammer</span> : null}
      {powerPlay ? <span className="wpp">power play · {powerPlay.color}, {powerPlay.side}</span> : null}
      {running ? (
        <span className="wsc">
          <i className="wdot red" />{running.red} – {running.yellow}<i className="wdot yellow" />
        </span>
      ) : (
        <span className="wsc wsc-none"
              title={scoresWithheld
                ? "The wall board was read, but its scores could not be matched "
                  + "to these ends. Setting the game's start time places them, "
                  + "with no need to read the board again."
                : undefined}>
          {boardReadable === false ? "chart predates board reading"
            : scoresWithheld ? "needs a start time"
            : "not posted"}
        </span>
      )}
    </div>
  );
}

export function Timing({ view, ui, series, think, here, actions }) {
  const listRef = useRef(null);

  /* Keep the current row in view. Deliberately not scrollIntoView: that
   * scrolls every scrollable ancestor, and the phone shell is a stack of
   * fixed boxes that must not move.
   *
   * Rows are measured against the list itself: their offsetParent is the
   * fixed #watch, whose offsets also count the chart above the list. */
  const STICKY_H = 30;   // .tend's height in style.css: it covers the list's top
  useEffect(() => {
    const el = listRef.current;
    const cur = el?.querySelector(".wrow[aria-current]");
    if (!el || !cur) return;
    const top = cur.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
    if (top < el.scrollTop + STICKY_H || top + ROW_H > el.scrollTop + el.clientHeight)
      el.scrollTop = Math.max(0, top - el.clientHeight / 2 + ROW_H / 2);
  }, [ui.ei, ui.si, ui.following]);

  /* A gesture, not a scroll event: the effect above scrolls this same
   * element, and a scroll listener could not tell the two apart. */
  const yield_ = () => { if (ui.following !== false) actions.setFollowing(false); };
  const teams = view.game.teams || {};
  const current = view.ends[ui.ei]?.shots[ui.si];
  return (
    <div className="tpane">
      <div className="tchart">
        <ThinkingChart series={series} at={here} box={TIMINGBOX} shadeEnd={ui.ei} />
        <div className="wtotals">
          <span><i className="wdot red" />{teams.red?.name || "red"} {clockText(series.red)}</span>
          <span><i className="wdot yellow" />{teams.yellow?.name || "yellow"} {clockText(series.yellow)}</span>
        </div>
        <p className="wcaveat">
          Read from {think.measured} of {think.measured + think.unmeasured} rocks
          {think.estimated ? `, ${think.estimated} estimated` : ""}.
        </p>
      </div>
      <div className="tlist" ref={listRef} onWheel={yield_} onTouchMove={yield_}>
        {view.ends.map((e, k) => {
          const summary = endSummary(view, k);
          return (
            <section key={k} aria-label={`End ${e.end?.number ?? k + 1}`}>
              {summary ? <EndHead summary={summary} /> : null}
              {rockRows(view, k, ui.leadIn).map(r => (
                <RockRow key={r.i} row={r} current={k === ui.ei && r.i === ui.si}
                         onPick={i => { actions.setFollowing(true); actions.goTo(k, i); }} />
              ))}
            </section>
          );
        })}
      </div>
      {ui.following === false && current ? (
        <button type="button" className="wback"
                onClick={() => { actions.setFollowing(true); actions.goTo(ui.ei, ui.si); }}>
          ↓ Back to rock {current.number}
        </button>
      ) : null}
    </div>
  );
}
