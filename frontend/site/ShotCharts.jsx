/* My shots' two charts for one group of rocks: where each crossed the broom
 * against its weight, and every delivery laid over the others. The geometry
 * is core's (missScatter, deliveryOverlay), tested under node; this maps it to
 * SVG. The plot is painted as ice in literal colours, as the viewer paints
 * it, so it reads the same in dark mode; the words around it take the page's
 * own ink from site.css. */
import { useId } from "react";
import { PAINT } from "../core/constants.mjs";
import { deliveryOverlay } from "../core/delivery.mjs";
import { missScatter } from "../core/myshots.mjs";

// useId's characters are not all allowed in a url(#…) reference.
const useClipId = () => "clip" + useId().replace(/[^A-Za-z0-9_-]/g, "");

function Plot({ plot }) {
  return <rect x={plot.x} y={plot.y} width={plot.w} height={plot.h}
               fill={PAINT.ice} stroke={PAINT.iceLine} />;
}

function Sides({ sides }) {
  return sides.map(s => (
    <text key={s.text} x={s.x} y={s.y} textAnchor={s.anchor} className="sc-side">{s.text}</text>
  ));
}

export function MissScatter({ rows, turn }) {
  const g = missScatter(rows, turn);
  if (!g.points.length) {
    return <p className="muted shotchart-none">No rock here was both measured at the broom and timed.</p>;
  }
  const { plot } = g;
  return (
    <figure className="shotchart">
      <svg viewBox={`0 0 ${g.w} ${g.h}`} role="img"
           aria-label={`Where ${g.points.length} rocks crossed the broom, against their weight`}>
        <Plot plot={plot} />
        <rect x={g.band.x} y={g.band.y} width={g.band.w} height={g.band.h}
              fill={PAINT.twelve} fillOpacity={0.14} />
        {g.xTicks.map(t => (
          <g key={t.x}>
            <line x1={t.x} x2={t.x} y1={plot.y} y2={plot.y + plot.h}
                  stroke={t.zero ? PAINT.rail : PAINT.iceLine} />
            {t.label && <text x={t.x} y={plot.y + plot.h + 13} textAnchor="middle">{t.label}</text>}
          </g>
        ))}
        {g.yTicks.map(t => (
          <g key={t.y}>
            <line x1={plot.x} x2={plot.x + plot.w} y1={t.y} y2={t.y} stroke={PAINT.iceLine}
                  strokeDasharray="2 3" />
            <text x={plot.x - 5} y={t.y + 4} textAnchor="end">{t.label}</text>
          </g>
        ))}
        <Sides sides={g.sides} />
        <text x={plot.x + plot.w / 2} y={g.h - 4} textAnchor="middle" className="sc-axis">{g.xLabel}</text>
        {g.points.map(p => (
          <a key={p.key} href={p.href || undefined}>
            {/* Past the edge: drawn on it, as a ring that says it is further. */}
            <circle cx={p.cx} cy={p.cy} r={p.clipped ? 6 : 4.5}
                    fill={p.clipped || p.hollow ? PAINT.ice : PAINT.accent} stroke={PAINT.accent}
                    strokeWidth={1.5} strokeDasharray={p.clipped ? "2 2" : undefined}
                    opacity={p.dim ? 0.35 : 0.8} />
          </a>
        ))}
      </svg>
      <figcaption>
        Where each rock crossed the broom, by its weight: heavier at the top, {g.yLabel} down
        the side. The band is on the broom. Hollow: the split is estimated.
        {g.clipped ? ` Dashed: further out than the edge.` : ""}
        {g.skipped ? ` ${g.skipped} not plotted.` : ""}
      </figcaption>
    </figure>
  );
}

export function DeliveryOverlay({ rows, turn }) {
  const clip = useClipId();
  const paths = rows.filter(r => r.path).map(r => ({
    key: r.key, pts: r.path.pts, start: r.path.start, dim: r.nums.tick === "disagrees" }));
  if (!paths.length) return <p className="muted shotchart-none">No rock here has a delivery path.</p>;
  const g = deliveryOverlay(paths, turn);
  const { plot } = g;
  return (
    <figure className="shotchart">
      <svg viewBox={`0 0 ${g.w} ${g.h}`} role="img"
           aria-label={`${g.n} deliveries, each measured off its own line to the broom`}>
        <defs>
          <clipPath id={clip}><rect x={plot.x} y={plot.y} width={plot.w} height={plot.h} /></clipPath>
        </defs>
        <Plot plot={plot} />
        <g clipPath={`url(#${clip})`}>
          {g.lines.map(q => <line key={q.kind} x1={q.x1} y1={q.y1} x2={q.x2} y2={q.y2}
                                  stroke={PAINT.iceLine} strokeWidth={q.kind === "hog" ? 2 : 1} />)}
          <line x1={g.aim.x1} y1={g.aim.y1} x2={g.aim.x2} y2={g.aim.y2}
                stroke={PAINT.rail} strokeDasharray="4 3" />
          {g.rocks.map(r => (
            <g key={r.key} opacity={r.dim ? 0.4 : 1}>
              <polyline points={r.d} fill="none" stroke={PAINT.accent} strokeOpacity={0.28}
                        strokeWidth={1.3} strokeLinejoin="round" />
              {r.start && <circle cx={r.start.x} cy={r.start.y} r={2.2} fill={PAINT.accent} fillOpacity={0.5} />}
            </g>
          ))}
          {g.median && <polyline points={g.median} fill="none" stroke={PAINT.four} strokeWidth={2.5}
                                 strokeLinejoin="round" />}
        </g>
        {g.labels.map(l => <text key={l.kind} x={l.x} y={l.y} textAnchor={l.anchor}>{l.text}</text>)}
        {/* A tick every 10 cm, a figure every 20. */}
        {g.ticks.map(t => (
          <g key={t.label}>
            <line x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} className="sc-tick" />
            {Number(t.label) % 20 === 0 && <text x={t.lx} y={t.ly} textAnchor="middle">{t.label}</text>}
          </g>
        ))}
        <Sides sides={g.sides} />
      </svg>
      <figcaption>
        Every delivery, hack to past the hog line, as cm off its own line from the
        foothold to the broom: straight up the dashed line is dead on. Blue is the
        median. Across is stretched ×{g.stretch}.
      </figcaption>
    </figure>
  );
}
