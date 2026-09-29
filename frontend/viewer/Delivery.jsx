/* The delivery, close up: the rock from the hack to 1.5 m past the hog line,
 * a dot every tenth of a second, beside the line from the hack to the broom.
 * Everything it draws comes from core/delivery.mjs; this file only turns it
 * into markup. The ice is painted in literal PAINT colours and stays light in
 * dark mode, as the strip does. */
import { DELIVERYBOX, deliveryGeometry, deliveryReason, rampColor } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";

const MUTED = "#6d6455";
const LINE = { hack: [PAINT.rail, 1], tee: [PAINT.iceLine, 1], hog: [PAINT.red, 1.2], centre: [PAINT.iceLine, 0.7] };
const LEGEND_N = 8;

/* The legend: a short run of the ramp's own dots, from the push-off to past
 * the hog line. No <defs>: the desktop card stays mounted on a phone, and two
 * charts in one page must not share an id. */
function Legend({ g }) {
  const x = g.side ? g.plot.x + 6 : g.plot.x + 8;
  const y = g.side ? g.plot.y + g.plot.h - 8 : g.plot.y + 12;
  return (
    <g>
      <text x={x} y={y - 6} fontSize={8.5} fill={MUTED}>push-off → hog +1.5 m</text>
      {Array.from({ length: LEGEND_N }, (_, i) => (
        <circle key={i} cx={x + 3 + i * 9} cy={y + 2} r={2.6} fill={rampColor(i / (LEGEND_N - 1))} />
      ))}
    </g>
  );
}

export function DeliveryChart({ g, label, fluid, className = "dlv" }) {
  if (!g) return null;
  return (
    <svg className={className} {...(fluid ? {} : { width: g.w, height: g.h })} viewBox={`0 0 ${g.w} ${g.h}`}
         role="img" aria-label={label}>
      <rect x={0} y={0} width={g.w} height={g.h} fill={PAINT.ice} />
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
      {g.dots.map((d, i) => <circle key={`d${i}`} cx={d.x} cy={d.y} r={2.3} fill={d.fill} />)}
      {g.start ? <circle cx={g.start.x} cy={g.start.y} r={4.2} fill="none" stroke={PAINT.accent} strokeWidth={1.3} /> : null}
      <Legend g={g} />
    </svg>
  );
}

export const deliveryLabel = (shot, side) =>
  `The delivery from above, thrower at the ${side ? "left" : "bottom"}: the rock from the hack to 1.5 m past the hog line, ` +
  `a dot every tenth of a second, darker earlier${shot?.target_broom ? ", with the line from the hack to the broom" : ""}`;

/* The phone's Delivery tab. Fluid: 358 px is the pane at 390 wide, and a
 * narrower phone scales the whole chart rather than scrolling it sideways. */
export function Delivery({ shot, doc }) {
  if (!shot) return <p className="dnone">No rocks were detected in this end</p>;
  const reason = deliveryReason(shot, doc);
  if (reason) return <p className="dnone">{reason}</p>;
  const g = deliveryGeometry(shot, DELIVERYBOX);
  return (
    <>
      <DeliveryChart g={g} label={deliveryLabel(shot, false)} fluid />
      <p className="dcap">From above, thrower at the bottom · across ×{g.stretch} · a dot every 0.1 s, dark at rest to gold past the hog line</p>
    </>
  );
}
