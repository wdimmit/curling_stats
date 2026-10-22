/* The delivery, close up: the rock from the hack to 1.5 m past the hog line,
 * a dot every frame (every tenth of a second before pipeline 2026.10.12.1),
 * beside the line from the hack to the broom.
 * Everything it draws comes from core/delivery.mjs; this file only turns it
 * into markup. The ice is painted in literal PAINT colours and stays light in
 * dark mode, as the strip does. */
import { DELIVERYBOX, deliveryGeometry, deliveryReason, quietCorner } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";

const MUTED = "#6d6455";
const HOUSE = { twelve: PAINT.twelve, eight: PAINT.ice, four: PAINT.four, button: PAINT.ice };
const STRIPE = { fill: "#b9c9da", edge: "#8fa6bd" };
const LINE = { hack: [PAINT.rail, 1], tee: [PAINT.iceLine, 1], hog: [PAINT.red, 1.2], centre: [PAINT.iceLine, 0.7] };
const LEGEND_ROW = 11;

/* The key to the dots' two colours, before the release and after it; none
 * for a rock whose release was not read, its dots all one colour. At
 * `legendAt` where the chart set one (the line chart's); upright else in the
 * same quiet corner, off the path and the words; on its side bottom left,
 * clear of the hack line and the footholds behind it, where the rock has
 * barely moved and its release is far off. The rows stack up from y, in the
 * room linechart.mjs quietCorner sizes. No <defs>: the desktop card stays
 * mounted on a phone, and two charts in one page must not share an id. */
function Legend({ g }) {
  if (!g.legend.length) return null;
  const hack = g.side ? g.lines.find(l => l.kind === "hack") : null;
  const at = g.legendAt ?? (g.side ? { x: (hack ? hack.x1 : g.plot.x) + 6, y: g.plot.y + g.plot.h - 8 }
                                   : quietCorner(g));
  const last = g.legend.length - 1;
  return (
    <g>
      {g.legend.map((e, i) => {
        const cy = at.y + 2 - (last - i) * LEGEND_ROW;
        return (
          <g key={e.text}>
            <circle cx={at.x + 3} cy={cy} r={2.6} fill={e.color} />
            <text x={at.x + 9} y={cy + 3} fontSize={8.5} fill={MUTED} stroke={PAINT.ice} strokeWidth={2.5}
                  paintOrder="stroke">{e.text}</text>
          </g>
        );
      })}
    </g>
  );
}

/* Where the hand let go: a tick across the path and the word beside it, in
 * the ink of the start ring -- a line where that is a ring, and neither of
 * the dots' colours. Both are haloed in ice, so the tick parts the dots it
 * crosses and the word reads over the aim line and the paint. */
function Release({ r }) {
  const { tick, label } = r;
  return (
    <g>
      <line x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2} stroke={PAINT.ice} strokeWidth={3.6}
            strokeLinecap="round" />
      <line x1={tick.x1} y1={tick.y1} x2={tick.x2} y2={tick.y2} stroke={PAINT.accent} strokeWidth={1.6}
            strokeLinecap="round" />
      <text x={label.x} y={label.y} fontSize={9} fontWeight={700} textAnchor={label.anchor} fill={PAINT.accent}
            stroke={PAINT.ice} strokeWidth={2.5} paintOrder="stroke">{label.text}</text>
    </g>
  );
}

/* What the dots' colours mean, for a caption or a label: nothing when they
 * are all one colour. */
export const dotColours = g => (g.legend.length ? ", navy before the release and gold after" : "");

export function DeliveryChart({ g, label, fluid, className = "dlv", at }) {
  if (!g) return null;
  return (
    <svg className={className} {...(fluid ? {} : { width: g.w, height: g.h })} viewBox={`0 0 ${g.w} ${g.h}`}
         role="img" aria-label={label} {...(at ? { x: at.x, y: at.y } : {})}>
      <rect x={0} y={0} width={g.w} height={g.h} fill={PAINT.ice} />
      {g.paint ? (
        <svg x={g.plot.x} y={g.plot.y} width={g.plot.w} height={g.plot.h}
             viewBox={`${g.plot.x} ${g.plot.y} ${g.plot.w} ${g.plot.h}`} aria-hidden="true">
          {g.paint.stripe ? <path d={g.paint.stripe.d} fill={STRIPE.fill} fillOpacity={0.28} /> : null}
          <g opacity={0.3}>
            {g.paint.house.map(r => (
              <ellipse key={r.kind} cx={r.cx} cy={r.cy} rx={r.rx} ry={r.ry} fill={HOUSE[r.kind]} />
            ))}
          </g>
          {(g.paint.stripe?.edges || []).map((e, i) => (
            <line key={`e${i}`} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} stroke={STRIPE.edge}
                  strokeWidth={1} strokeOpacity={0.8} />
          ))}
        </svg>
      ) : null}
      {g.lines.map((l, i) => {
        const [stroke, width] = LINE[l.kind] ?? LINE.tee;
        return <line key={`l${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={stroke} strokeWidth={width}
                     {...(l.kind === "centre" ? { strokeDasharray: "3 3" } : {})} />;
      })}
      {g.labels.map((t, i) => (
        <text key={`t${i}`} x={t.x} y={t.y} fontSize={9} textAnchor={t.anchor}
              fill={t.kind === "hog" ? PAINT.red : MUTED}>{t.text}</text>
      ))}
      {g.ticks.map((t, i) => (
        <g key={`k${i}`}>
          <line x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} stroke={PAINT.iceLine} strokeWidth={1} />
          <text x={t.lx} y={t.ly} fontSize={8} textAnchor={t.anchor} fill={MUTED}>{t.label}</text>
        </g>
      ))}
      {(g.minor || []).map((t, i) => (
        <line key={`m${i}`} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} stroke={PAINT.iceLine} strokeWidth={0.8} />
      ))}
      {g.holds.map((b, i) => (
        <rect key={`h${i}`} x={b.x} y={b.y} width={b.w} height={b.h} rx={1}
              fill={b.used ? PAINT.graniteEdge : "none"} stroke={PAINT.graniteEdge} strokeWidth={0.8} />
      ))}
      {g.aim ? <polyline points={g.aim} fill="none" stroke={MUTED} strokeWidth={1.3} strokeDasharray="5 3" /> : null}
      {g.aimLabel ? (
        <text x={g.aimLabel.x} y={g.aimLabel.y} fontSize={8.5} textAnchor={g.aimLabel.anchor} fill={MUTED}>{g.aimLabel.text}</text>
      ) : null}
      {g.runs.map((r, i) => (
        <polyline key={`r${i}`} points={r} fill="none" stroke={MUTED} strokeOpacity={0.45} strokeWidth={1} />
      ))}
      {g.dots.map((d, i) => <circle key={`d${i}`} cx={d.x} cy={d.y} r={g.dotR} fill={d.fill} />)}
      {g.start ? <circle cx={g.start.x} cy={g.start.y} r={4.2} fill="none" stroke={PAINT.accent} strokeWidth={1.3} /> : null}
      {g.release ? <Release r={g.release} /> : null}
      <Legend g={g} />
    </svg>
  );
}

export const deliveryLabel = (shot, side, g) =>
  `The delivery from above, thrower at the ${side ? "left" : "bottom"}: the rock from the hack to 1.5 m past the hog line, ` +
  `a dot every ${g.every.words}${dotColours(g)}${shot?.target_broom ? ", with the line from the hack to the broom" : ""}`;

/* The delivery's speed, along the same axis as the big chart above it
 * (core/speed.mjs speedGeometry). Ice-painted like the charts it sits with. */
export function SpeedChart({ sg, label }) {
  if (!sg) return null;
  return (
    <svg className="spd" viewBox={`0 0 ${sg.w} ${sg.h}`} role="img" aria-label={label}>
      <rect x={0} y={0} width={sg.w} height={sg.h} fill={PAINT.ice} />
      {sg.grid.map(q => (
        <g key={`g${q.v}`}>
          <line x1={sg.plot.x} y1={q.y} x2={sg.plot.x + sg.plot.w} y2={q.y}
                stroke={q.v === 0 ? PAINT.iceLine : "#e6e0d0"} strokeWidth={Number.isInteger(q.v) ? 0.9 : 0.6} />
          {q.label ? <text x={sg.axisX} y={q.y + 3} fontSize={8} fill={MUTED}>{q.label}</text> : null}
        </g>
      ))}
      {sg.lines.map(l => (
        <g key={l.kind}>
          <line x1={l.x} y1={l.y1} x2={l.x} y2={l.y2} stroke={LINE[l.kind][0]} strokeWidth={LINE[l.kind][1]} />
          <text x={l.x} y={sg.h - 7} fontSize={9} textAnchor="middle"
                fill={l.kind === "hog" ? PAINT.red : MUTED}>{l.kind}</text>
        </g>
      ))}
      {/* At the same distance as the marker above, named over the plot: the
          lines' own names are under it. */}
      {sg.release ? (
        <g>
          <line x1={sg.release.x} y1={sg.release.y1} x2={sg.release.x} y2={sg.release.y2} stroke={PAINT.accent}
                strokeWidth={1} strokeDasharray="4 3" />
          <text x={sg.release.x} y={sg.release.y1 - 4} fontSize={9} fontWeight={700} textAnchor="middle"
                fill={PAINT.accent}>Release</text>
        </g>
      ) : null}
      {sg.segs.map((s, i) => (
        <line key={`s${i}`} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} stroke={s.color} strokeWidth={2.2}
              strokeLinecap="round" />
      ))}
      <circle cx={sg.peak.x} cy={sg.peak.y} r={3.4} fill={PAINT.accent} />
      {/* Haloed, as the hog speed is: the peak is often just past the release's line. */}
      <text x={sg.peak.x} y={sg.peak.y - 8} fontSize={10} fontWeight={700} textAnchor="middle"
            fill={PAINT.accent} stroke={PAINT.ice} strokeWidth={3} paintOrder="stroke">
        {`${sg.peak.v.toFixed(1)} m/s peak`}
      </text>
      {sg.hog ? (
        <>
          <circle cx={sg.hog.x} cy={sg.hog.y} r={3.4} fill={PAINT.red} />
          <text x={sg.hog.x - 6} y={sg.hog.y + 17} fontSize={10} fontWeight={700} textAnchor="end"
                fill={PAINT.accent} stroke={PAINT.ice} strokeWidth={3} paintOrder="stroke">
            {`${sg.hog.v.toFixed(1)} m/s at the hog line`}
          </text>
        </>
      ) : null}
    </svg>
  );
}

/* The phone's Delivery tab. Fluid: 358 px is the pane at 390 wide, and a
 * narrower phone scales the whole chart rather than scrolling it sideways. */
export function Delivery({ shot, doc }) {
  if (!shot) return <p className="dnone">No rocks were detected in this end</p>;
  const reason = deliveryReason(shot, doc);
  if (reason) return <p className="dnone">{reason}</p>;
  const g = deliveryGeometry(shot, DELIVERYBOX);
  return (
    <>
      <DeliveryChart g={g} label={deliveryLabel(shot, false, g)} fluid />
      <p className="dcap">From above, thrower at the bottom · across ×{g.stretch} · a dot every {g.every.short}{dotColours(g)}</p>
    </>
  );
}
