/* The two thinking-time charts, drawn from core/charts geometry.
 *
 * The bars carry their own onSelect. The version this replaced returned SVG
 * as a string, so every redraw had to re-query `rect[data-shot]` and reattach
 * handlers -- which had to be remembered in two places, and was not: the
 * report's bars were drawn with no handlers at all until somebody clicked one.
 */
import { Fragment, useLayoutEffect, useRef, useState } from "react";
import {
  barLabels, barsGeometry, chartGeometry, clockText, endSpan, lineEnds,
} from "../core/index.mjs";
import { CHARTBOX } from "../core/constants.mjs";

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

export function ThinkingChart({ series, at = null, box, shadeEnd = null }) {
  const geom = chartGeometry(series, at, box);
  if (!geom) return null;
  const shade = shadeEnd == null ? null : endSpan(geom, shadeEnd, box ?? CHARTBOX);
  return (
    <svg className="clockchart" viewBox={geom.viewBox} role="img" aria-label={geom.aria}>
      <Grid geom={geom} />
      {shade ? <rect className="endshade" x={n(shade.x0)} y={n(shade.y1)}
                     width={n(shade.x1 - shade.x0)} height={n(shade.y2 - shade.y1)} /> : null}
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

/* A team's colour, drawn rather than painted: a CSS background is dropped
 * when the page is printed, an SVG fill is not. */
export function Dot({ c, size = 12 }) {
  return (
    <svg className={`rpt-dot ${c}`} width={size} height={size} viewBox="0 0 12 12"
         aria-hidden="true">
      <circle cx="6" cy="6" r="5.5" />
    </svg>
  );
}

/* The width the element is drawn at, followed as it changes. */
function useWidth(ref, fallback) {
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      const x = Math.round(el.getBoundingClientRect().width);
      if (x > 0) setW(x);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

function ReportGrid({ geom, words }) {
  const { box } = geom;
  return (
    <>
      {geom.grid.map(g => (
        <Fragment key={g.v}>
          <line className="g" x1={box.padL} x2={box.w - box.padR} y1={n(g.y)} y2={n(g.y)} />
          <text className="yl" x={box.padL - 8} y={n(g.y + 4)}>{g.label}</text>
        </Fragment>
      ))}
      {geom.ticks.map((t, i) => (
        <line key={i} className="b" x1={n(t.x)} x2={n(t.x)} y1={t.y1} y2={t.y2} />
      ))}
      {geom.endLabels.map(l => (
        <text key={l.number} className="xl" x={n(l.x)} y={l.y}>
          {words ? `End ${l.number}` : l.number}
        </text>
      ))}
    </>
  );
}

/* The report's clock: both charts drawn at the card's own width, so a label
 * stays 13 px whatever the screen -- the sidebar's scale with their box
 * instead. Narrow (a phone), the totals move under the chart, out of the
 * margin they would otherwise need. The bars are screen-only: print keeps
 * the running totals. */
export function ReportClock({ series, names, onSelect }) {
  const ref = useRef(null);
  const width = useWidth(ref, 860);
  const roomy = width >= 600;
  const lineBox = { w: width, h: roomy ? 240 : 190, padL: roomy ? 52 : 40,
                    padR: roomy ? 128 : 6, padT: 12, padB: 30 };
  const barBox = { ...lineBox, h: roomy ? 200 : 160, padT: 22 };
  const lines = chartGeometry(series, null, lineBox);
  const bars = barsGeometry(series, null, barBox);
  const words = series.bounds.length <= 5;
  return (
    <div ref={ref} className="rpt-clock">
      {lines ? (
        <svg className="rclock lines" viewBox={lines.viewBox} width="100%" role="img"
             aria-label={lines.aria}>
          <ReportGrid geom={lines} words={words} />
          {lines.lines.map(l => (
            <polyline key={l.color} className={`ln ${l.color}`}
                      points={l.points.map(([x, y]) => `${n(x)},${n(y)}`).join(" ")} />
          ))}
          {lines.marks.map((m, i) => (
            <circle key={i} cx={n(m.x)} cy={n(m.y)} r="3" className={`est ${m.color}`} />
          ))}
          {roomy ? lineEnds(lines, series).map(e => (
            <g key={e.color}>
              <circle className={`dot ${e.color}`} cx={n(e.x + 15)} cy={n(e.y)} r="5" />
              <text className="tot" x={n(e.x + 25)} y={n(e.y + 5)}>
                {names[e.color]} {clockText(e.total)}
              </text>
            </g>
          )) : null}
        </svg>
      ) : null}
      {!roomy ? (
        <div className="rpt-clockkey">
          {["red", "yellow"].map(c => (
            <span key={c}><Dot c={c} size={11} />{names[c]} {clockText(series[c])}</span>
          ))}
        </div>
      ) : null}
      {bars ? (
        <svg className="rclock bars screenonly" viewBox={bars.viewBox} width="100%" role="img"
             aria-label={bars.aria}>
          <ReportGrid geom={bars} words={words} />
          {bars.bars.map(b => (
            <rect key={b.shot} className={`bar ${b.color}${b.est ? " est" : ""}`}
                  x={n(b.x)} y={n(b.y)} width={n(b.w)} height={n(b.h)}
                  onClick={onSelect && (() => onSelect(b))}>
              <title>{b.title}</title>
            </rect>
          ))}
          {bars.median ? (
            <>
              <line className="median" x1={bars.median.x1} x2={bars.median.x2}
                    y1={n(bars.median.y)} y2={n(bars.median.y)}>
                <title>{bars.median.title}</title>
              </line>
              {roomy ? (
                <text className="mlabel" x={n(bars.median.x2 + 10)} y={n(bars.median.y + 4)}>
                  median {clockText(series.median)}
                </text>
              ) : null}
            </>
          ) : null}
          {barLabels(bars, 3).map(l => (
            <text key={l.shot} className="blabel" x={n(l.x)} y={n(l.y - 5)}>{l.text}</text>
          ))}
        </svg>
      ) : null}
      {!roomy && bars?.median ? (
        <div className="rpt-clockkey screenonly">
          Dashed line: median {clockText(series.median)} a rock
        </div>
      ) : null}
    </div>
  );
}
