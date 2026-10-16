/* Thinking time, from the header (spec 2026-10-15 §1): in the wide layout the
 * chart panel's "Thinking time" box becomes this dialog. A bar is still a
 * rock you can go to; going there closes the dialog. */
import { useEffect, useRef } from "react";
import { TALLBOX } from "../core/index.mjs";
import { ClockKey, ThinkingBars, ThinkingChart } from "./Charts.jsx";
import { CLOSE } from "./DeliveryDialog.jsx";

export function ThinkingDialog({ open, series, here, bars, actions, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  // A click on the backdrop lands on the <dialog> itself; anything inside the
  // padded wrapper does not.
  const onClick = e => { if (e.target === e.currentTarget) e.currentTarget.close(); };
  const empty = !series.points.length || (!series.red && !series.yellow);
  return (
    <dialog id="thinkingDialog" ref={ref} onClose={onClose} onClick={onClick} aria-labelledby="thinkingTitle">
      {open ? (
        <div className="dd-body">
          <div className="dd-head">
            <h2 id="thinkingTitle">Thinking time</h2>
            <span className="grow" />
            <form method="dialog"><button className="dd-close" aria-label="Close">{CLOSE}</button></form>
          </div>
          {empty ? <p className="dnone">No thinking time was measured in this game.</p> : (
            <div className="tclock">
              {bars
                ? <ThinkingBars series={series} at={here} box={TALLBOX}
                                onSelect={b => { actions.goToBar(b); ref.current?.close(); }} />
                : <ThinkingChart series={series} at={here} box={TALLBOX} />}
              <ClockKey series={series}>
                <span className="grow" />
                <button className="linky" type="button" onClick={() => actions.setClockBars(!bars)}>
                  {bars ? "totals" : "per rock"}
                </button>
              </ClockKey>
            </div>
          )}
        </div>
      ) : null}
    </dialog>
  );
}
