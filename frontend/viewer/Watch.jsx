/* Watch mode: the phone layout for someone viewing a game, not charting one.
 *
 * The charting phone layout is a bottom sheet holding a grade row, and the
 * read-only surfaces inherited it with the grading cut out -- a 256px empty
 * panel under a video. This replaces it on /s/ and /g/ with the thing a
 * viewer actually wants: a pager over the current rock, a pane for whichever
 * of House, Detail, Delivery or Timing is open, and the tab bar that switches
 * between them. The pane's own file owns what it draws -- this is the shell, and the
 * following that keeps it all pointed at the video as it plays.
 *
 * This renders no video slot. #playCard stays exactly where it is inside
 * <main> and CSS decides who is visible -- crossing the phone gate on a
 * rotation must never reparent the iframe (runtime/player.mjs). The house
 * sheet is the existing #houseCard promoted by CSS for the same reason: a
 * second <House> would put a second id="house" in the document.
 */
import { useEffect, useMemo } from "react";
import {
  PAUSE_AFTER_REST_S, WATCH_TABS, endAt, rockAt, rockSpan, rockRows, stepRock,
} from "../core/index.mjs";
import * as player from "../runtime/player.mjs";
import { Delivery } from "./Delivery.jsx";
import { Detail } from "./Detail.jsx";
import { Pager, useSwipe } from "./Pager.jsx";
import { Timing } from "./Timing.jsx";

/* Follow the video, and hand control back the moment a thumb disagrees.
 *
 * Polled rather than driven by events: the YouTube iframe reports its time
 * over postMessage and has no "rock changed" to listen for. One second is
 * fine -- the interval between rocks is measured in tens of seconds.
 *
 * The end is followed too: once the playhead reaches the next end, the
 * cursor goes to the rock it is in there, not on holding the last one.
 *
 * Pausing after each rock (App) stops the video PAUSE_AFTER_REST_S past a
 * rest, so following runs `lag` behind the video: the rock just finished
 * stays up while the video sits paused on it, and the next one comes up once
 * it plays on. The extra second covers a tick landing between the pause
 * point and the pause.
 */
function useFollow(view, leadIn, rows, following, ei, si, lag, actions) {
  useEffect(() => {
    if (!following || !rows.length) return undefined;
    const tick = () => {
      if (!player.isPlaying()) return;
      const now = player.currentTime();
      const t = typeof now === "number" ? now - lag : now;
      const e = endAt(view, t, leadIn);
      if (e != null && e !== ei) {
        const at = rockAt(rockRows(view, e, leadIn), t);
        if (at != null) actions.followTo(e, at);
        return;
      }
      const span = rockSpan(rows, t);
      if (span && (si < span[0] || si > span[1])) actions.followTo(ei, span[1]);
    };
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [view, leadIn, rows, following, ei, si, lag, actions]);
}

export function Watch({ view, ui, config, series, think, here, actions }) {
  const rows = useMemo(() => rockRows(view, ui.ei, ui.leadIn), [view, ui.ei, ui.leadIn]);
  useFollow(view, ui.leadIn, rows, ui.following !== false, ui.ei, ui.si,
            ui.pauseAfterShot ? PAUSE_AFTER_REST_S + 1 : 0, actions);
  const swipe = useSwipe(d => actions.step(d));
  const row = rows[ui.si] || null;
  const shot = view.ends[ui.ei]?.shots[ui.si] ?? null;
  const ends = useMemo(() => view.ends.map(x => ({
    number: x.end?.number, house: x.end?.house, rocks: x.shots.length,
  })), [view]);
  return (
    <section id="watch" aria-label="This game's rocks">
      {ui.tab !== "timing" && row ? (
        <Pager row={row} format={view.format} count={rows.length} index={ui.si} ends={ends} ei={ui.ei}
               canPrev={!!stepRock(view, ui.ei, ui.si, -1)} canNext={!!stepRock(view, ui.ei, ui.si, 1)}
               onStep={actions.step} onEnd={actions.goToEnd} />
      ) : null}
      {ui.tab === "detail" ? (
        <div className="wpane" {...swipe}><Detail shot={shot} doc={view.doc} /></div>
      ) : null}
      {ui.tab === "delivery" ? (
        <div className="wpane" {...swipe}><Delivery shot={shot} doc={view.doc} /></div>
      ) : null}
      {/* The House tab has no element of its own: #houseCard is promoted by CSS
          between the pager and the tabs -- one <House> in the page, so one #house. */}
      {ui.tab === "timing" ? (
        <Timing view={view} ui={ui} series={series} think={think} here={here} actions={actions} />
      ) : null}
      <nav className="wtabs" aria-label="Views">
        {WATCH_TABS.map(([k, label]) => (
          <button key={k} type="button" aria-current={ui.tab === k ? "page" : undefined}
                  onClick={() => actions.setTab(k)}>{label}</button>
        ))}
      </nav>
    </section>
  );
}
