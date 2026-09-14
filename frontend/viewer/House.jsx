/* The sheet, the stones on it, and the pointer gestures that edit them. */
import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  DRAG_MIN_M, PAINT, PHONE_QUERY, R,
  clampX, clampY, houseViewBox, isBlank, onSheet, shouldCrop, stoneAt,
} from "../core/index.mjs";
import * as dragStore from "../runtime/dragStore.mjs";
import { writeBodyState } from "../runtime/bodyState.mjs";

const phone = () => matchMedia(PHONE_QUERY).matches;

/* The crop is measured, and the measurement is the branch this layout gets
 * wrong. Gated on the phone media query rather than the measured box alone: a
 * bad first measurement would otherwise read as "short and wide" on a desktop
 * too, and that crop is a fixed point -- its own aspect ratio re-derives the
 * same box on every later measurement.
 *
 * writeBodyState() is called here, immediately before measuring, as well as by
 * the component that owns it. getBoundingClientRect() flushes layout against
 * body[data-sheet|house|peek], so the attributes have to have landed first;
 * making that ordering local to this function is safer than depending on React
 * running a sibling's layout effect before this one. Both writes are
 * idempotent, so the second costs nothing.
 */
function useHouseCrop(svgRef, { editing, bodyFlags, deps }) {
  const [box, setBox] = useState({ crop: false, aspect: 0 });

  useLayoutEffect(() => {
    writeBodyState(bodyFlags);
    const measure = () => {
      const el = svgRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setBox({
        crop: shouldCrop({ phone: phone(), editing, width: r.width, height: r.height }),
        aspect: r.height > 0 ? r.width / r.height : 0,
      });
    };
    measure();
    // The first paint can measure a zero-height SVG.
    const raf = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return houseViewBox(box.crop ? "crop" : "full", box.aspect);
}

function sheetPoint(evt, svg) {
  const p = new DOMPoint(evt.clientX, evt.clientY)
    .matrixTransform(svg.getScreenCTM().inverse());
  return { x: p.x, y: p.y };
}

/* The flight, drawn as a tail that fades in from where the camera first saw
 * the stone to where it stopped. Segments rather than one polyline so the
 * opacity can ramp without a gradient along the path. */
function Track({ shot }) {
  const pts = shot?.track;
  if (!pts || pts.length < 2) return null;
  const color = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <g clipPath="url(#sheetClip)" pointerEvents="none">
      {pts.slice(0, -1).map((p, i) => (
        <line key={i} x1={p[1]} y1={p[2]} x2={pts[i + 1][1]} y2={pts[i + 1][2]}
              stroke={color} strokeWidth={0.045} strokeLinecap="round"
              opacity={(0.12 + 0.78 * (i / Math.max(1, pts.length - 2))).toFixed(3)} />
      ))}
      <circle cx={pts[0][1]} cy={pts[0][2]} r={0.06} fill="none"
              stroke={color} strokeWidth={0.025} opacity={0.5} />
    </g>
  );
}

/* The only subscriber to the drag store, so a moving stone repaints itself and
 * nothing else in the tree. */
function Stones({ shot, selStone }) {
  const dragging = useSyncExternalStore(dragStore.subscribe, dragStore.getStones,
                                        () => null);
  const stones = dragging ?? shot?.stones ?? [];
  const thrown = shot?.delivered_stone_index;
  return stones.map((st, i) => (
    <g key={i} className={"stone" + (i === selStone ? " sel" : "")} data-i={i}>
      {i === thrown && (
        <circle cx={st.x} cy={st.y} r={R.stone + 0.07} fill="none"
                stroke={PAINT.thrown} strokeWidth={0.03} strokeDasharray="0.09 0.07" />
      )}
      <circle cx={st.x} cy={st.y} r={R.stone} fill={PAINT.granite}
              stroke={PAINT.graniteEdge} strokeWidth={0.02} />
      <circle cx={st.x} cy={st.y} r={R.stone * 0.55}
              fill={st.color === "red" ? PAINT.red : PAINT.yellow} />
      {i === selStone && (
        <circle cx={st.x} cy={st.y} r={R.stone + 0.05} fill="none"
                stroke={PAINT.accent} strokeWidth={0.035} strokeDasharray="0.07 0.05" />
      )}
      <title>
        {`${st.color} — ${(st.distance_to_tee ?? 0).toFixed(2)} m from the tee`
         + `${st.in_house ? " (in the rings)" : ""}`
         + `${i === thrown ? " — this shot" : ""}`}
      </title>
    </g>
  ));
}

function Unknown() {
  return (
    <g pointerEvents="none">
      <rect x={-R.halfWidth} y={R.back} width={2 * R.halfWidth} height={R.hog - R.back}
            fill="url(#hatch)" />
      <rect x={-R.halfWidth} y={R.back} width={2 * R.halfWidth} height={R.hog - R.back}
            fill="none" stroke={PAINT.warn} strokeWidth={0.05} strokeDasharray="0.25 0.18" />
      <text x={0} y={4.9} textAnchor="middle" fontSize={0.34} fill={PAINT.warn}
            fontWeight="700">STATE UNKNOWN — chart this shot</text>
    </g>
  );
}

export function House({ shot, shotKey, selStone, houseMode, showTrack, readOnly,
                        placeColor, bodyFlags, cropDeps, actions }) {
  const svgRef = useRef(null);
  const drag = useRef(null);
  const viewBox = useHouseCrop(svgRef, {
    editing: houseMode === "edit", bodyFlags, deps: cropDeps,
  });

  /* The gesture lives in a ref, never in state: a drag that re-rendered the
   * tree on every pointermove would relayout the whole game sixty times a
   * second and reset the save debounce with it. */
  const onPointerDown = ev => {
    // On the phone the house is a picture until you enter the editor: one tap
    // cannot both open the editor and place a stone.
    if (phone() && houseMode !== "edit") {
      if (!readOnly) actions.openHouseEditor();
      return;
    }
    const g = ev.target.closest?.(".stone");
    const p = sheetPoint(ev, svgRef.current);
    if (g) {
      const i = +g.dataset.i;
      drag.current = { i, start: p, moved: false };
      actions.selectStone(i);
      actions.setBusy(shotKey);
      svgRef.current.setPointerCapture(ev.pointerId);
    } else {
      drag.current = { i: null, start: p, moved: false };
    }
  };

  const onPointerMove = ev => {
    const d = drag.current;
    if (!d || d.i === null || !shot) return;
    const p = sheetPoint(ev, svgRef.current);
    if (!d.moved && Math.hypot(p.x - d.start.x, p.y - d.start.y) < DRAG_MIN_M) return;
    d.moved = true;
    const stones = (dragStore.getStones() ?? shot.stones ?? []).map(s => ({ ...s }));
    const st = stones[d.i];
    if (!st) return;
    st.x = clampX(p.x); st.y = clampY(p.y);
    st.distance_to_tee = Math.hypot(st.x, st.y);
    st.in_house = st.distance_to_tee <= R.inHouse;
    st.source = "manual";
    dragStore.set(stones);            // live feedback; committed on release
  };

  const onPointerUp = ev => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    const p = sheetPoint(ev, svgRef.current);
    const moved = dragStore.getStones();
    if (d.i !== null) {
      actions.setBusy(null);
      if (!d.moved) { dragStore.clear(); return; }     // a click that selected it
      dragStore.clear();
      if (!onSheet(p)) { actions.removeStone(d.i); return; }  // dragged off: remove
      if (moved) actions.commitStones(moved);
      return;
    }
    if (!onSheet(p) || Math.hypot(p.x - d.start.x, p.y - d.start.y) >= DRAG_MIN_M) return;
    if (!shot) return;
    // Empty ice: place a stone. On a blank shot the first rock of the
    // thrower's colour is, by construction, the one they just threw.
    actions.placeStone(stoneAt(clampX(p.x), clampY(p.y), placeColor));
  };

  return (
    <svg id="house" viewBox={viewBox} aria-label="House diagram" ref={svgRef}
         onPointerDown={onPointerDown} onPointerMove={onPointerMove}
         onPointerUp={onPointerUp}>
      <defs>
        <clipPath id="sheetClip">
          <rect x={-R.halfWidth} y={R.back} width={2 * R.halfWidth} height={R.hog - R.back} />
        </clipPath>
        <pattern id="hatch" width={0.36} height={0.36}
                 patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width={0.18} height={0.36} fill={PAINT.warn} opacity={0.22} />
        </pattern>
      </defs>
      <rect id="ice" x={-R.halfWidth} y={R.back} width={2 * R.halfWidth}
            height={R.hog - R.back} fill={PAINT.ice} stroke={PAINT.iceLine}
            strokeWidth={0.02} />
      {[[R.twelve, PAINT.twelve], [R.eight, PAINT.ice],
        [R.four, PAINT.four], [R.button, PAINT.ice]].map(([r, fill]) => (
        <circle key={r} cx={0} cy={0} r={r} fill={fill} />
      ))}
      {[0, R.hog].map(y => (
        <line key={y} x1={-R.halfWidth} y1={y} x2={R.halfWidth} y2={y}
              stroke={PAINT.rail} strokeWidth={y ? 0.05 : 0.02} />
      ))}
      <line x1={0} y1={R.back} x2={0} y2={R.hog} stroke={PAINT.rail} strokeWidth={0.015} />
      {showTrack && <Track shot={shot} />}
      <Stones shot={shot} selStone={selStone} />
      {isBlank(shot) && <Unknown />}
    </svg>
  );
}
