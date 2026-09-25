/* Detail: was this rock thrown at the skip's broom? The whole sheet as a strip,
 * thrower at the bottom, beside six figures. Everything it draws comes from
 * core/line.mjs; this file only turns it into markup. */
import { lineFigures, stripGeometry } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";

const RING = { twelve: PAINT.twelve, eight: PAINT.ice, four: PAINT.four, button: PAINT.ice };
const GOLD = "#a07a00";      // the rock's path: #e8b400 is unreadable on the ice
const MUTED = "#6d6455";

function Strip({ shot }) {
  const g = stripGeometry(shot);
  if (!g) return <div className="dstrip dstrip-none" aria-hidden="true" />;
  const own = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <svg className="dstrip" width={g.w} height={g.h} viewBox={`0 0 ${g.w} ${g.h}`} role="img"
         aria-label="The sheet from above, thrower at the bottom: the intended line, the thrown line and where the rock went">
      {g.rings.map((r, i) => (
        <ellipse key={i} cx={g.w / 2} cy={r.cy} rx={r.rx} ry={r.ry} fill={RING[r.kind]} fillOpacity={0.55} />
      ))}
      {g.hogs.map((y, i) => <line key={`h${i}`} x1={0} y1={y} x2={g.w} y2={y} stroke={PAINT.red} strokeWidth={1.2} />)}
      {[...g.tees, ...g.backs, g.hack].map((y, i) => (
        <line key={`l${i}`} x1={0} y1={y} x2={g.w} y2={y} stroke={PAINT.iceLine} strokeWidth={0.8} />
      ))}
      <line x1={g.w / 2} y1={0} x2={g.w / 2} y2={g.h} stroke={PAINT.iceLine} strokeWidth={0.8} />
      {g.stones.map((s, i) => (
        <circle key={`s${i}`} cx={s.cx} cy={s.cy} r={3.2} fill={s.color === "red" ? PAINT.red : PAINT.yellow}
                fillOpacity={0.6} stroke={PAINT.graniteEdge} strokeWidth={0.6} />
      ))}
      {g.aim ? <polyline points={g.aim} fill="none" stroke={MUTED} strokeWidth={1.4} strokeDasharray="4 3" /> : null}
      {g.ext ? <polyline points={g.ext} fill="none" stroke={PAINT.accent} strokeWidth={1.2} strokeDasharray="2 2.5" /> : null}
      {g.thrown ? <polyline points={g.thrown} fill="none" stroke={PAINT.accent} strokeWidth={2.4} /> : null}
      {g.path ? <polyline points={g.path} fill="none" stroke={GOLD} strokeWidth={2.2} strokeLinejoin="round" /> : null}
      {g.miss ? (
        <g>
          <line x1={g.miss.x1} y1={g.miss.y} x2={g.miss.x2} y2={g.miss.y} stroke={PAINT.accent} strokeWidth={1} />
          <text x={(g.miss.x1 + g.miss.x2) / 2} y={g.miss.y - 4} fontSize={9} fontWeight={700}
                textAnchor="middle" fill={PAINT.accent}>{g.miss.label}</text>
        </g>
      ) : null}
      {g.broom ? (
        <rect x={g.broom.x - 5} y={g.broom.y - 2} width={10} height={4} rx={1}
              fill={PAINT.accent} stroke={own} strokeWidth={1}><title>skip&apos;s broom</title></rect>
      ) : null}
      {g.rest ? <circle cx={g.rest.x} cy={g.rest.y} r={4.2} fill={own} stroke={PAINT.accent} strokeWidth={1.2} /> : null}
      {g.start ? <circle cx={g.start.x} cy={g.start.y} r={3.2} fill={PAINT.accent} /> : null}
    </svg>
  );
}

const Check = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"
       style={{ fill: "none", strokeWidth: 2.6 }}><path d="M4 12 L10 18 L20 6" /></svg>
);

export function Detail({ shot, doc }) {
  const f = lineFigures(shot, doc);
  if (f.predates) return <p className="dnone">{f.reason}</p>;
  return (
    <>
      <div className="dbody">
        <Strip shot={shot} />
        <dl className="dfigs">
          {f.figures.map(x => (
            <div key={x.key} className={x.dim ? "dfig dim" : "dfig"}>
              <dt>{x.label}</dt>
              <dd className="dval">{x.value}</dd>
              <dd className={x.tick ? `dnote ${x.tick}` : "dnote"}>
                {x.tick === "confirmed" ? <Check /> : null}{x.note}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <p className="dcap">Sheet from above, thrower at the bottom · across ×3 · figures ±10 cm · wide = the side away from the curl</p>
    </>
  );
}
