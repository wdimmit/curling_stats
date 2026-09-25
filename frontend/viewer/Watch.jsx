/* Watch mode: the phone layout for someone viewing a game, not charting one.
 *
 * The charting phone layout is a bottom sheet holding a grade row, and the
 * read-only surfaces inherited it with the grading cut out -- a 256px empty
 * panel under a video. This replaces it on /s/ and /g/ with the thing a
 * viewer actually wants: three tabs: House, Detail and Timing.
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
import { useEffect, useMemo } from "react";
import { houseCaption, rockAt, rockRows, stepRock } from "../core/index.mjs";
import * as player from "../runtime/player.mjs";
import { Detail } from "./Detail.jsx";
import { Pager, useSwipe } from "./Pager.jsx";
import { Timing } from "./Timing.jsx";

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

const TABS = [["house", "House"], ["detail", "Detail"], ["timing", "Timing"]];

export function Watch({ view, ui, config, series, think, here, actions }) {
  const rows = useMemo(() => rockRows(view, ui.ei, ui.leadIn), [view, ui.ei, ui.leadIn]);
  useFollow(rows, ui.following !== false, ui.si, actions);
  const swipe = useSwipe(d => actions.step(d));
  const row = rows[ui.si] || null;
  const shot = view.ends[ui.ei]?.shots[ui.si] ?? null;
  const caption = houseCaption(shot);
  return (
    <section id="watch" aria-label="This game's rocks">
      {ui.tab !== "timing" && row ? (
        <Pager row={row} count={rows.length} index={ui.si} end={view.ends[ui.ei]?.end?.number}
               canPrev={!!stepRock(view, ui.ei, ui.si, -1)} canNext={!!stepRock(view, ui.ei, ui.si, 1)}
               onStep={actions.step} />
      ) : null}
      {ui.tab === "detail" ? (
        <div className="wpane" {...swipe}><Detail shot={shot} doc={view.doc} /></div>
      ) : null}
      {/* #houseCard itself is promoted by CSS between the pager and this
          caption: one <House> in the page, so one #house. */}
      {ui.tab === "house" ? (
        <div className="whouse">
          {caption ?? "Dark pad: the skip's broom · ringed: this rock"}
        </div>
      ) : null}
      {ui.tab === "timing" ? (
        <Timing view={view} ui={ui} series={series} think={think} here={here} actions={actions} />
      ) : null}
      <nav className="wtabs" aria-label="Views">
        {TABS.map(([k, label]) => (
          <button key={k} type="button" aria-current={ui.tab === k ? "page" : undefined}
                  onClick={() => actions.setTab(k)}>{label}</button>
        ))}
      </nav>
    </section>
  );
}
