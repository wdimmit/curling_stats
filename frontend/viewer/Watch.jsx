/* Watch mode: the phone layout for someone viewing a game, not charting one.
 *
 * The charting phone layout is a bottom sheet holding a grade row, and the
 * read-only surfaces inherited it with the grading cut out -- a 256px empty
 * panel under a video. This replaces it on /s/ and /g/ with the thing a
 * viewer actually wants: the end as a list of its sixteen rocks.
 *
 * Each row carries its own thinking-time bar, so the list *is* the per-rock
 * chart, turned on its side. That matters more than it sounds: the clock is
 * the one statistic that works on a game nobody has charted, because it comes
 * out of detection rather than out of grading.
 *
 * This renders no video slot. #playCard stays exactly where it is inside
 * <main> and CSS decides who is visible -- crossing the phone gate on a
 * rotation must never reparent the iframe (runtime/player.mjs). The house
 * sheet is the existing #houseCard promoted by CSS for the same reason: a
 * second <House> would put a second id="house" in the document.
 */
import { useEffect, useMemo, useRef } from "react";
import { clockText, endSummary, rockAt, rockRows } from "../core/index.mjs";
import * as player from "../runtime/player.mjs";
import { ThinkingChart } from "./Charts.jsx";
import { TALLBOX } from "../core/constants.mjs";

const ROW_H = 56;

/* Follow the video, and hand control back the moment a thumb disagrees.
 *
 * Polled rather than driven by events: the YouTube iframe reports its time
 * over postMessage and has no "rock changed" to listen for. One second is
 * fine -- the interval between rocks is measured in tens of seconds.
 */
function useFollow(rows, following, si, actions) {
  useEffect(() => {
    if (!following || !rows.length) return undefined;
    const tick = () => {
      if (!player.isPlaying()) return;
      const at = rockAt(rows, player.currentTime());
      if (at !== null && at !== si) actions.followTo(at);
    };
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [rows, following, si, actions]);
}

function RockRow({ row, current, onPick }) {
  return (
    <button type="button" className="wrow" aria-current={current || undefined}
            onClick={() => onPick(row.i)}>
      <span className={`wdisc ${row.color}`}>{row.number}</span>
      <span className="wmid">
        <span className="wname">{row.name}</span>
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

function EndBar({ summary, onStep }) {
  if (!summary) return null;
  const { number, of, running, hammer, boardReadable, scoresWithheld } = summary;
  return (
    <div className="wendbar">
      <button type="button" onClick={() => onStep(-1)} disabled={number <= 1}
              aria-label="Previous end">◀</button>
      <div className="wendmid">
        <span className="wen">End {number} of {of}</span>
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
        {hammer ? <span className="wham">{hammer} has hammer</span> : null}
      </div>
      <button type="button" onClick={() => onStep(1)} disabled={number >= of}
              aria-label="Next end">▶</button>
    </div>
  );
}

/* The cumulative clock across the whole game. The same chart the aside draws,
 * which on a phone has never been reachable at all. */
function GameSheet({ series, think, here, onClose, actions }) {
  return (
    <>
      <div className="wsheethead">
        <span className="wgrip" />
        <span className="wst">Thinking time</span>
        <button type="button" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="wsheetbody">
        <ThinkingChart series={series} at={here} box={TALLBOX} />
        <div className="wtotals">
          <span><i className="wdot red" />red {clockText(series.red)}</span>
          <span><i className="wdot yellow" />yellow {clockText(series.yellow)}</span>
        </div>
        <p className="wcaveat">
          Read from {think.measured} of {think.measured + think.unmeasured} rocks
          {think.estimated ? `, ${think.estimated} estimated` : ""}. A rock with no
          interval is one nobody could time, not one thrown instantly.
        </p>
      </div>
    </>
  );
}

export function Watch({ view, ui, config, series, think, here, actions }) {
  const rows = useMemo(() => rockRows(view, ui.ei, ui.leadIn),
                       [view, ui.ei, ui.leadIn]);
  const summary = useMemo(() => endSummary(view, ui.ei), [view, ui.ei]);
  const listRef = useRef(null);

  useFollow(rows, ui.following !== false && !ui.watch, ui.si, actions);

  /* Keep the current row in view while following. Deliberately not
   * scrollIntoView: that scrolls every scrollable ancestor, and the phone
   * shell is a stack of fixed boxes that must not move. */
  useEffect(() => {
    const el = listRef.current;
    if (!el || ui.following === false) return;
    const top = ui.si * ROW_H;
    if (top < el.scrollTop || top + ROW_H > el.scrollTop + el.clientHeight)
      el.scrollTop = Math.max(0, top - el.clientHeight / 2 + ROW_H / 2);
  }, [ui.si, ui.following, ui.ei]);

  /* A gesture, not a scroll event: the effect above scrolls this same
   * element, and a scroll listener could not tell the two apart. */
  const yield_ = () => { if (ui.following !== false) actions.setFollowing(false); };

  const step = d => {
    const next = ui.ei + d;
    if (next < 0 || next >= view.ends.length) return;
    actions.goTo(next, 0);
  };

  const row = rows[ui.si] || null;
  return (
    <section id="watch" aria-label="Rocks in this end">
      <EndBar summary={summary} onStep={step} />
      <div className="wlist" ref={listRef} onWheel={yield_} onTouchMove={yield_}>
        {rows.map(r => (
          <RockRow key={r.i} row={r} current={r.i === ui.si}
                   onPick={i => { actions.setFollowing(true); actions.goTo(ui.ei, i); }} />
        ))}
      </div>
      {ui.following === false
        ? <button type="button" className="wback"
                  onClick={() => { actions.setFollowing(true); actions.goTo(ui.ei, ui.si); }}>
            ↓ Back to rock {row ? row.number : ""}
          </button>
        : null}
      <div className="wbar">
        <button type="button" onClick={() => actions.openWatch("house")}>⌂ House</button>
        <button type="button" onClick={() => actions.openWatch("game")}>◷ Whole game</button>
      </div>

      {/* The house sheet's chrome only. #houseCard itself is promoted into the
          gap between these two by CSS, so there is one <House> in the page. */}
      {ui.watch === "house" && row ? (
        <>
          <div className="wsheethead">
            <span className="wgrip" />
            <span className="wst">Rock {row.number} · {row.color}
              {row.position ? ` · ${row.position}` : ""}</span>
            <button type="button" onClick={() => actions.openWatch("")}
                    aria-label="Close">✕</button>
          </div>
          <div className="wsheetfoot">
            {row.name}
            {row.unmeasured ? "" : ` · ${row.text} thinking`}
          </div>
        </>
      ) : null}

      {ui.watch === "game" ? (
        <div className="wsheet">
          <GameSheet series={series} think={think} here={here}
                     onClose={() => actions.openWatch("")} actions={actions} />
        </div>
      ) : null}
    </section>
  );
}
