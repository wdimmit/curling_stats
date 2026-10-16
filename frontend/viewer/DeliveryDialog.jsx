/* The delivery opened large (spec 2026-10-15 §2): the close up drawn big, and
 * the rock's speed along the same axis under it.
 *
 * A native <dialog> opened with showModal(), as the flag dialog is: focus,
 * Escape and the backdrop come with it, and App's key handler stands down
 * while any dialog is open. `delivering` is the snapshot taken when it was
 * opened -- a review link follows the video, and the drawing must not change
 * under the person reading it. */
import { useEffect, useRef } from "react";
import {
  BIGDELIVERYBOX, SPEEDBOX, TYPES, deliveryGeometry, deliverySpeed, speedGeometry, speedNumbers, typeOf,
} from "../core/index.mjs";
import { DeliveryChart, SpeedChart, deliveryLabel } from "./Delivery.jsx";

const typeName = shot => {
  const id = typeOf(shot);
  const t = TYPES.find(x => x.id === id);
  return t ? t.name : id === "unknown" ? "" : id[0].toUpperCase() + id.slice(1);
};

export const CLOSE = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4}
       aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
);

export function DeliveryDialog({ delivering, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (delivering && !d.open) d.showModal();
    if (!delivering && d.open) d.close();
  }, [delivering]);
  // A click on the backdrop lands on the <dialog> itself; anything inside it does not.
  const onClick = e => { if (e.target === e.currentTarget) e.currentTarget.close(); };
  return (
    <dialog id="deliveryDialog" ref={ref} onClose={onClose} onClick={onClick} aria-labelledby="deliveryTitle">
      {delivering && <Large key={delivering.opened} shot={delivering.shot} />}
    </dialog>
  );
}

function Large({ shot }) {
  const g = deliveryGeometry(shot, BIGDELIVERYBOX);
  const speed = deliverySpeed(shot);
  const sg = speedGeometry(speed, SPEEDBOX, shot);
  const n = speedNumbers(speed);
  const type = typeName(shot);
  return (
    <>
      <div className="dd-head">
        <h2 id="deliveryTitle">Delivery</h2>
        <span className={`dd-chip ${shot.color}`}>{shot.number}</span>
        <span className="dd-who">{shot.label || `shot ${shot.number}`}{type ? ` · ${type}` : ""}</span>
        <span className="grow" />
        <span className="dd-esc">Esc or click outside to close</span>
        <form method="dialog"><button className="dd-close" aria-label="Close">{CLOSE}</button></form>
      </div>
      <DeliveryChart g={g} label={deliveryLabel(shot, true, g)} fluid className="dd-dlv" />
      <p className="dcap">From above, thrower at the left · across ×{g.stretch} · a dot every {g.every.short}, dark at the push-off to gold past the hog line · ticks every 2 in</p>
      {sg && n ? (
        <>
          <div className="dd-speed">
            <h3>Speed</h3>
            <span className="dd-num"><b>{n.peak}</b> {n.peakWhere}</span>
            {n.hog ? <span className="dd-num"><b>{n.hog}</b> at the hog line</span> : null}
            {n.change ? (
              <span className="dd-num">{n.change.value ? <b>{n.change.value}</b> : null} {n.change.words}</span>
            ) : null}
          </div>
          <SpeedChart sg={sg}
                      label={`Speed along the delivery: ${n.peak} at its fastest${n.hog ? `, ${n.hog} at the hog line` : ""}`} />
          <p className="dcap">Speed along the same axis, smoothed over a second. Ripples smaller than about 0.2 m/s come from the camera, not the throw.</p>
        </>
      ) : <p className="dnone">The speed could not be read for this rock.</p>}
    </>
  );
}
