/* The wide layout's side cards (spec 2026-10-15 §1): the rock's figures under
 * the house, and the line chart the height of the page. Rendered only in the
 * desk layouts -- the figures in the wide one and the portrait iPad's (spec
 * 2026-10-16 §5), the line chart in the wide one alone -- so the ids they
 * carry -- theirs, and the #flags and #endBox passed in -- are never in the
 * page twice. */
import { useLayoutEffect, useRef, useState } from "react";
import { LINEBOX, lineChartGeometry, lineFigures } from "../core/index.mjs";
import { dotColours } from "./Delivery.jsx";
import { Figures, isBroomless } from "./Detail.jsx";
import { LineChart } from "./LineChart.jsx";

const LINE_MIN_H = 560;

const TREND = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
       aria-hidden="true"><path d="M3 17c4-1 6-4 9-6s5-4 9-5" /><path d="M15 6h6v6" /></svg>
);

/* A rock never seen (`shot.missing`, lineReason's "This rock was never seen")
 * has no line, so its figures would be a grid of "–": one line says why
 * instead, and the house above keeps the room -- the queue's blanks are where
 * stones get placed. A blank that was seen keeps its figures.
 *
 * `onLine` (the portrait iPad, which draws no line chart) adds an eighth cell
 * that opens the delivery large, on a rock that has one -- the same test the
 * line chart uses to offer Expand -- and drops the caption: the large view
 * keeps its own. */
export function FiguresCard({ shot, doc, onLine, children }) {
  const f = shot ? lineFigures(shot, doc) : null;
  return (
    <section className="card" id="figCard" aria-label="This rock's figures">
      {children}
      {!shot ? <p className="dnone">No rocks were detected in this end</p>
        : f.predates || shot.missing ? <p className="dnone">{f.reason}</p>
        : (
          <>
            {/* On the wide desktop, one line of note each, the whole note the figure's
                tooltip; on the portrait iPad every note wraps in full (no hover). */}
            <Figures figures={f.figures.map(x => ({ ...x, title: typeof x.note === "string" ? x.note : undefined }))}>
              {onLine && !lineChartGeometry(shot, doc, LINEBOX).why ? (
                <div className="dfig dline">
                  <dt className="sr">The line</dt>
                  <dd><button type="button" id="lineOpen" onClick={onLine}>{TREND}Line &amp; speed</button></dd>
                </div>
              ) : null}
            </Figures>
            {onLine ? null : (
              <p className="dcap">
                {isBroomless(shot) ? "Figures ±2 in" : "Figures ±2 in · wide = the side away from the curl"}
              </p>
            )}
          </>
        )}
    </section>
  );
}

/* The chart is drawn to its box: the card is the page's height (style.css),
 * the box is what is left of it, and the geometry is worked out for that. */
function useBoxSize() {
  const ref = useRef(null);
  const [size, setSize] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([e]) => {
      const w = Math.round(e.contentRect.width), h = Math.round(e.contentRect.height);
      setSize(s => (s && s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

const EXPAND = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}
       aria-hidden="true"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" /></svg>
);

export function LineCard({ shot, doc, onExpand }) {
  const [ref, size] = useBoxSize();
  const lc = shot && size && size.w > 0
    ? lineChartGeometry(shot, doc, { ...LINEBOX, w: size.w, h: Math.max(LINE_MIN_H, size.h) }) : null;
  // Only a rock with a delivery opens large.
  const open = lc && !lc.why ? onExpand : null;
  return (
    <section className="card" id="lineCard" aria-label="The line">
      <div className="lc-head">
        <h2>The line</h2>
        {open ? (
          <button type="button" id="lineExpand" onClick={open} title="Open the delivery large">
            {EXPAND}Expand
          </button>
        ) : null}
      </div>
      <div className={open ? "lc-box zoom" : "lc-box"} ref={ref} onClick={open || undefined}>
        {lc ? <LineChart shot={shot} lc={lc} /> : null}
      </div>
      {lc?.why ? <p className="dlvnone">{lc.why}</p> : null}
      {lc ? (
        <p className="dcap">{lc.bottom
          ? `From above, thrower at the bottom · far end across ×${lc.acrossTop} · `
            + `the delivery across ×${lc.acrossBottom}, a dot every ${lc.every.short}${dotColours(lc.bottom.g)}`
          : `From above, thrower at the bottom · across ×${lc.acrossTop}`}</p>
      ) : null}
    </section>
  );
}
