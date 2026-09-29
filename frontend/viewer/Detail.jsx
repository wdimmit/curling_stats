/* Detail: was this rock thrown at the skip's broom? The whole sheet as a strip
 * beside (the phone) or above (the desktop) six figures. Everything it draws
 * comes from core/line.mjs; this file only turns it into markup. */
import { DESKBOX, DESKDELIVERYBOX, deliveryGeometry, deliveryReadable, deliveryReason, lineFigures,
         sideways, stripGeometry, stripShapes } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";
import { DeliveryChart, deliveryLabel } from "./Delivery.jsx";

const RING = { twelve: PAINT.twelve, eight: PAINT.ice, four: PAINT.four, button: PAINT.ice };
const GOLD = "#a07a00";      // the rock's path: #e8b400 is unreadable on the ice
const MUTED = "#6d6455";
const LINE = { hog: [PAINT.red, 1.2] };
const PAINTED = [PAINT.iceLine, 0.8];   // tee, back, hack and centre lines

/* One renderer for both orientations. `fluid` leaves the size to CSS: the
 * desktop card scales the drawing to its own width through the viewBox. The
 * attribute order below is the phone's markup, character for character. */
function Strip({ shot, shapes: s, label, fluid }) {
  if (!s) return <div className="dstrip dstrip-none" aria-hidden="true" />;
  const own = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <svg className="dstrip" {...(fluid ? {} : { width: s.w, height: s.h })} viewBox={`0 0 ${s.w} ${s.h}`}
         role="img" aria-label={label}>
      {s.rings.map((r, i) => (
        <ellipse key={i} cx={r.cx} cy={r.cy} rx={r.rx} ry={r.ry} fill={RING[r.kind]} fillOpacity={0.55} />
      ))}
      {s.lines.map((l, i) => {
        const [stroke, width] = LINE[l.kind] ?? PAINTED;
        return <line key={`l${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={stroke} strokeWidth={width} />;
      })}
      {s.stones.map((t, i) => (
        <circle key={`s${i}`} cx={t.x} cy={t.y} r={3.2} fill={t.color === "red" ? PAINT.red : PAINT.yellow}
                fillOpacity={0.6} stroke={PAINT.graniteEdge} strokeWidth={0.6} />
      ))}
      {s.aim ? <polyline points={s.aim} fill="none" stroke={MUTED} strokeWidth={1.4} strokeDasharray="4 3" /> : null}
      {s.ext ? <polyline points={s.ext} fill="none" stroke={PAINT.accent} strokeWidth={1.2} strokeDasharray="2 2.5" /> : null}
      {s.thrown ? <polyline points={s.thrown} fill="none" stroke={PAINT.accent} strokeWidth={2.4} /> : null}
      {s.path ? <polyline points={s.path} fill="none" stroke={GOLD} strokeWidth={2.2} strokeLinejoin="round" /> : null}
      {s.miss ? (
        <g>
          <line x1={s.miss.x1} y1={s.miss.y1} x2={s.miss.x2} y2={s.miss.y2} stroke={PAINT.accent} strokeWidth={1} />
          <text x={s.miss.tx} y={s.miss.ty} fontSize={s.miss.size} fontWeight={700}
                textAnchor={s.miss.anchor} fill={PAINT.accent}
                {...(s.miss.halo ? { stroke: PAINT.ice, strokeWidth: 3, paintOrder: "stroke" } : {})}>{s.miss.label}</text>
        </g>
      ) : null}
      {s.broom ? (
        <rect x={s.broom.x} y={s.broom.y} width={s.broom.w} height={s.broom.h} rx={1}
              fill={PAINT.accent} stroke={own} strokeWidth={1}><title>skip&apos;s broom</title></rect>
      ) : null}
      {s.rest ? <circle cx={s.rest.x} cy={s.rest.y} r={4.2} fill={own} stroke={PAINT.accent} strokeWidth={1.2} /> : null}
      {s.start ? <circle cx={s.start.x} cy={s.start.y} r={3.2} fill={PAINT.accent} /> : null}
    </svg>
  );
}

// A doubles rock nobody held a broom for has no intended line to show
// against the thrown one, and no curl direction to call "wide" against.
const isBroomless = shot => !shot?.target_broom && shot?.line?.at_tee != null;
const stripWords = shot => isBroomless(shot)
  ? { seen: "the thrown line and where the rock went", wideGloss: "" }
  : { seen: "the intended line, the thrown line and where the rock went", wideGloss: " · wide = the side away from the curl" };

const Check = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"
       style={{ fill: "none", strokeWidth: 2.6 }}><path d="M4 12 L10 18 L20 6" /></svg>
);

/* The six figures. The phone stacks them; the desktop card's CSS lays them
 * out three across. */
export function Figures({ figures }) {
  return (
    <dl className="dfigs">
      {figures.map(x => (
        <div key={x.key} className={x.dim ? "dfig dim" : "dfig"}>
          <dt>{x.label}</dt>
          <dd className="dval">{x.value}</dd>
          <dd className={x.tick ? `dnote ${x.tick}` : "dnote"}>
            {x.tick === "confirmed" ? <Check /> : null}{x.note}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Detail({ shot, doc }) {
  const f = lineFigures(shot, doc);
  if (f.predates) return <p className="dnone">{f.reason}</p>;
  const { seen, wideGloss } = stripWords(shot);
  return (
    <>
      <div className="dbody">
        <Strip shot={shot} shapes={stripShapes(stripGeometry(shot))}
               label={`The sheet from above, thrower at the bottom: ${seen}`} />
        <Figures figures={f.figures} />
      </div>
      <p className="dcap">Sheet from above, thrower at the bottom · across ×3 · figures ±4 in{wideGloss}</p>
    </>
  );
}

/* The desktop card under the video: the same strip on its side, the
 * delivery close up under it the same way round, then the same figures. A
 * chart from before the delivery was measured keeps the card it had; a rock
 * the camera did not follow says so in one line. */
export function DeskDetail({ shot, doc }) {
  // An end where detection found nothing has no rock to describe, and
  // lineFigures would blame a broom that was never the problem.
  if (!shot) return <p className="dnone">No rocks were detected in this end</p>;
  const f = lineFigures(shot, doc);
  if (f.predates) return <p className="dnone">{f.reason}</p>;
  const { seen, wideGloss } = stripWords(shot);
  const measured = deliveryReadable(doc);
  const why = measured ? deliveryReason(shot, doc) : null;
  const g = measured && !why ? deliveryGeometry(shot, DESKDELIVERYBOX) : null;
  return (
    <>
      <Strip shot={shot} shapes={sideways(stripShapes(stripGeometry(shot, DESKBOX)))} fluid
             label={`The sheet from above, thrower at the left: ${seen}`} />
      {g ? <DeliveryChart g={g} label={deliveryLabel(shot, true)} fluid /> : null}
      {why ? <p className="dlvnone">{why}</p> : null}
      <Figures figures={f.figures} />
      <p className="dcap">Sheet from above, thrower at the left · across ×1.5 · figures ±4 in{wideGloss}
        {g ? ` · below it, the hack to 1.5 m past the hog line, across ×${g.stretch}, a dot every 0.1 s` : ""}</p>
    </>
  );
}
