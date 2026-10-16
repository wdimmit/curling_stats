/* A small sheet over the page (spec 2026-10-16 §6): the portrait iPad's Miss,
 * Note and Order, each a box that would not fit in the bar at touch size.
 *
 * A native <dialog> opened with showModal(), as the flag dialog is: focus,
 * Escape and the backdrop come with it, and App's key handler stands down
 * while any dialog is open. It closes on Esc, on a tap on the backdrop and on
 * Done; `onClose` is the dialog's own close event, so all three reach it.
 * Everything visible is inside .sheet-body: a tap there never lands on the
 * <dialog> element itself, so only the backdrop closes it. The head comes
 * first, so showModal() gives the caret to Done and not to a text box -- the
 * on-screen keyboard comes up only when a box is tapped. */
import { useEffect, useRef } from "react";

export function Sheet({ id, title, open, onClose, children }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  // A tap on the backdrop lands on the <dialog> itself; anything inside it does not.
  const onClick = e => { if (e.target === e.currentTarget) e.currentTarget.close(); };
  return (
    <dialog id={id} className="sheet" ref={ref} onClose={onClose} onClick={onClick}
            aria-labelledby={`${id}Title`}>
      <div className="sheet-body">
        <div className="sheet-head">
          <h2 id={`${id}Title`}>{title}</h2>
          <form method="dialog"><button type="submit">Done</button></form>
        </div>
        {children}
      </div>
    </dialog>
  );
}
