/* The delivery, close up: the rock from the hack to 1.5 m past the hog line,
 * a dot every frame (every tenth of a second before pipeline 2026.10.12.1),
 * beside the line from the hack to the broom.
 * Everything it draws comes from core/delivery.mjs; this file only turns it
 * into markup. The ice is painted in literal PAINT colours and stays light in
 * dark mode, as the strip does. */
import { DELIVERYBOX, deliveryGeometry, deliveryReason, rampColor } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";

const MUTED = "#6d6455";
const HOUSE = { twelve: PAINT.twelve, eight: PAINT.ice, four: PAINT.four, button: PAINT.ice };
const STRIPE = { fill: "#b9c9da", edge: "#8fa6bd" };
const LINE = { hack: [PAINT.rail, 1], tee: [PAINT.iceLine, 1], hog: [PAINT.red, 1.2], centre: [PAINT.iceLine, 0.7] };
const LEGEND_N = 8;

/* The legend: a short run of the ramp's own dots, from the push-off to past
 * the hog line. No <defs>: the desktop card stays mounted on a phone, and two
 * charts in one page must not share an id. */
function Legend({ g }) {
  const x = g.legend ? g.legend.x : g.side ? g.plot.x + 6 : g.plot.x + 8;
  const y = g.legend ? g.legend.y : g.side ? g.plot.y + g.plot.h - 8 : g.plot.y + 12;
  return (
    <g>
      <text x={x} y={y - 6} fontSize={8.5} fill={MUTED}>push-off → hog +1.5 m</text>
      {Array.from({ length: LEGEND_N }, (_, i) => (
        <circle key={i} cx={x + 3 + i * 9} cy={y + 2} r={2.6} fill={rampColor(i / (LEGEND_N - 1))} />
      ))}
    </g>
  );
}

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
      <Legend g={g} />
    </svg>
  );
}

export const deliveryLabel = (shot, side, g) =>
  `The delivery from above, thrower at the ${side ? "left" : "bottom"}: the rock from the hack to 1.5 m past the hog line, ` +
  `a dot every ${g.every.words}, darker earlier${shot?.target_broom ? ", with the line from the hack to the broom" : ""}`;

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
      {sg.segs.map((s, i) => (
        <line key={`s${i}`} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} stroke={s.color} strokeWidth={2.2}
              strokeLinecap="round" />
      ))}
      <circle cx={sg.peak.x} cy={sg.peak.y} r={3.4} fill={PAINT.accent} />
      <text x={sg.peak.x} y={sg.peak.y - 8} fontSize={10} fontWeight={700} textAnchor="middle"
            fill={PAINT.accent}>{`${sg.peak.v.toFixed(1)} m/s peak`}</text>
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
      <p className="dcap">From above, thrower at the bottom · across ×{g.stretch} · a dot every {g.every.short}, dark at rest to gold past the hog line</p>
    </>
  );
}
