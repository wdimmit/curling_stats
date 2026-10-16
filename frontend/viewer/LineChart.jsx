/* The line in one drawing (spec 2026-10-15 §1): the far end of the sheet
 * above, the delivery close up below, a funnel between them. Everything it
 * draws comes from core/linechart.mjs. The two parts are nested <svg>s, so
 * each clips to its own box with no clipPath -- and so with no ids, beside the
 * page's other charts. */
import { PAINT } from "../core/constants.mjs";
import { DeliveryChart, deliveryLabel } from "./Delivery.jsx";
import { Strip } from "./Detail.jsx";

const MUTED = "#6d6455";

export function LineChart({ shot, lc }) {
  if (!lc) return null;
  const f = lc.funnel;
  return (
    <svg className="linechart" viewBox={`0 0 ${lc.w} ${lc.h}`} width={lc.w} height={lc.h} role="img"
         aria-label="The sheet from above, thrower at the bottom: the far end, and the delivery close up">
      <rect x={0} y={0} width={lc.w} height={lc.h} fill={PAINT.ice} />
      <rect x={lc.top.x} y={lc.top.y} width={lc.top.w} height={lc.top.h} fill={PAINT.ice}
            stroke={PAINT.iceLine} strokeWidth={0.8} />
      <Strip shot={shot} shapes={lc.top.shapes} className="lc-top" at={{ x: lc.top.x, y: lc.top.y }}
             label="The far end of the sheet: the broom, the house, and where the rock went" />
      {lc.labels.map(t => (
        <text key={t.kind} x={t.x} y={t.y} fontSize={9} textAnchor="end"
              fill={t.kind === "hog" ? PAINT.red : MUTED}>{t.text}</text>
      ))}
      {f ? (
        <g aria-hidden="true">
          <polygon points={`${f.xl},${f.yt} ${f.xr},${f.yt} ${f.br},${f.yb} ${f.bl},${f.yb}`}
                   fill={MUTED} fillOpacity={0.08} />
          <line x1={f.xl} y1={f.yt} x2={f.bl} y2={f.yb} stroke={MUTED} strokeWidth={0.8} strokeDasharray="3 2" />
          <line x1={f.xr} y1={f.yt} x2={f.br} y2={f.yb} stroke={MUTED} strokeWidth={0.8} strokeDasharray="3 2" />
          <line x1={f.xl} y1={f.yt - 0.5} x2={f.xr} y2={f.yt - 0.5} stroke={MUTED} strokeWidth={2} />
        </g>
      ) : null}
      {lc.bottom ? (
        <DeliveryChart g={lc.bottom.g} label={deliveryLabel(shot, false, lc.bottom.g)} className="lc-dlv"
                       at={{ x: lc.bottom.x, y: lc.bottom.y }} />
      ) : null}
    </svg>
  );
}
