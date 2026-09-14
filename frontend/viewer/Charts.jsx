/* The two thinking-time charts, drawn from core/charts geometry.
 *
 * The bars carry their own onSelect. The version this replaced returned SVG
 * as a string, so every redraw had to re-query `rect[data-shot]` and reattach
 * handlers -- which had to be remembered in two places, and was not: the
 * report's bars were drawn with no handlers at all until somebody clicked one.
 */
import { Fragment } from "react";
import { barsGeometry, chartGeometry, clockText } from "../core/index.mjs";

const n = v => v.toFixed(1);

function Grid({ geom }) {
  return (
    <>
      {geom.grid.map(g => (
        <Fragment key={g.v}>
          <line x1={geom.box.padL} x2={geom.box.w - geom.box.padR}
                y1={n(g.y)} y2={n(g.y)} className="g" />
          <text x={geom.box.padL - 6} y={n(g.y + 4)} className="yl">{g.label}</text>
        </Fragment>
      ))}
      {geom.ticks.map((t, i) => (
        <line key={i} x1={n(t.x)} x2={n(t.x)} y1={t.y1} y2={t.y2} className="b" />
      ))}
      {geom.endLabels.map(l => (
        <text key={l.number} x={n(l.x)} y={l.y} className="xl">{l.number}</text>
      ))}
      {geom.you && (
        <line className="you" x1={n(geom.you.x)} x2={n(geom.you.x)}
              y1={geom.you.y1} y2={geom.you.y2} />
      )}
    </>
  );
}

export function ThinkingChart({ series, at = null, box }) {
  const geom = chartGeometry(series, at, box);
  if (!geom) return null;
  return (
    <svg className="clockchart" viewBox={geom.viewBox} role="img" aria-label={geom.aria}>
      <Grid geom={geom} />
      {geom.lines.map(l => (
        <polyline key={l.color} className={`ln ${l.color}`}
                  points={l.points.map(([x, y]) => `${n(x)},${n(y)}`).join(" ")} />
      ))}
      {geom.marks.map((m, i) => (
        <circle key={i} cx={n(m.x)} cy={n(m.y)} r="2.6" className={`est ${m.color}`} />
      ))}
    </svg>
  );
}

export function ThinkingBars({ series, at = null, box, onSelect }) {
  const geom = barsGeometry(series, at, box);
  if (!geom) return null;
  return (
    <svg className="clockchart bars" viewBox={geom.viewBox} role="img" aria-label={geom.aria}>
      <Grid geom={geom} />
      {geom.bars.map(b => (
        <rect key={b.shot} className={`bar ${b.color}${b.est ? " est" : ""}`}
              x={n(b.x)} y={n(b.y)} width={n(b.w)} height={n(b.h)}
              data-shot={b.shot}
              onClick={onSelect && (() => onSelect(b))}>
          <title>{b.title}</title>
        </rect>
      ))}
      {geom.median && (
        <line className="median" x1={geom.median.x1} x2={geom.median.x2}
              y1={n(geom.median.y)} y2={n(geom.median.y)}>
          <title>{geom.median.title}</title>
        </line>
      )}
    </svg>
  );
}

export function ClockKey({ series, children }) {
  return (
    <div className="key muted">
      <span><i className="sw red" />{clockText(series.red)}</span>
      <span><i className="sw yellow" />{clockText(series.yellow)}</span>
      {children}
    </div>
  );
}
