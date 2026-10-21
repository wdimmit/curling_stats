/* The end at a glance (2026-10-21), from the desktop header: the house after
 * every rock of the end the cursor is in, in throwing order, two rows of
 * them. A house is still a rock you can go to; going there closes the
 * dialog, as a bar in the thinking dialog does. `ending` is the end it was
 * opened on: a review link follows the video, and the end must not change
 * under the person reading it. */
import { useEffect, useRef } from "react";
import { isBlank, teamNames } from "../core/index.mjs";
import { HouseThumb } from "./House.jsx";
import { CLOSE, typeName } from "./DeliveryDialog.jsx";

export function EndDialog({ ending, view, at, showTrack, actions, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (ending && !d.open) d.showModal();
    if (!ending && d.open) d.close();
  }, [ending]);
  // A click on the backdrop lands on the <dialog> itself; anything inside the
  // padded wrapper does not.
  const onClick = e => { if (e.target === e.currentTarget) e.currentTarget.close(); };
  const e = ending ? view.ends[ending.ei] : null;
  const hammer = e?.end.hammer ? `${teamNames(view.game)[e.end.hammer]} has hammer` : null;
  return (
    <dialog id="endDialog" ref={ref} onClose={onClose} onClick={onClick} aria-labelledby="endTitle">
      {e ? (
        <div className="dd-body">
          <div className="dd-head">
            <h2 id="endTitle">{`End ${e.end.number}`}</h2>
            {hammer ? <span className="dd-who">{hammer}</span> : null}
            <span className="grow" />
            <span className="dd-esc">Click a house to go to its rock · Esc to close</span>
            <form method="dialog"><button className="dd-close" aria-label="Close">{CLOSE}</button></form>
          </div>
          {e.shots.length ? (
            <ol className="eh-grid" style={{ "--cols": Math.ceil(e.shots.length / 2) }}>
              {e.shots.map((s, si) => (
                <li key={si}>
                  <button type="button" className={`eh-rock${si === at ? " here" : ""}`}
                          title={s.label || `shot ${s.number}`}
                          aria-current={si === at ? "true" : undefined}
                          onClick={() => { actions.goToBarFromReport({ ei: ending.ei, si }); ref.current?.close(); }}>
                    <HouseThumb shot={s} showTrack={showTrack} />
                    <span className="eh-cap">
                      <span className={`dd-chip ${s.color}${isBlank(s) ? " unknown" : ""}`}>
                        {isBlank(s) ? "?" : s.number}
                      </span>
                      <span className="eh-type">{typeName(s)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : <p className="dnone">No rocks were found in this end.</p>}
        </div>
      ) : null}
    </dialog>
  );
}
