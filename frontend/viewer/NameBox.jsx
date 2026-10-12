/* A team-name box that offers the names the league already uses -- drawn by
 * the page, not left to a <datalist>. Firefox for Android draws nothing for a
 * datalist on a text box (Mozilla bug 1535985), so a phone there was offered
 * no names at all while the desktop showed them.
 *
 * The names sit under the box while it has focus, narrow as somebody types,
 * and a tap, or the arrows and Enter, puts one in the box. Focus stays in the
 * box throughout: a press on the list is kept from taking it, because the
 * game list saves the moment focus leaves the pair (site/ui.jsx,
 * useCommitOnExit), and on a phone a moved focus would drop the keyboard.
 *
 * Renders the box and its list side by side, not wrapped, so each editor
 * places the list in its own layout: under the box in the dialog's grid,
 * under the pair in the game list's row. Shared by the viewer's "Name
 * teams…" and the site's game list. */
import { useId, useState } from "react";
import { namesMatching } from "../runtime/teams.mjs";

export function NameBox({ names, value, onValue, onKeyDown, onBlur, ...input }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const shown = open ? namesMatching(names, value) : [];
  const lit = hi < shown.length ? hi : -1;

  // Picked, the list goes until more is typed: the names this one is the
  // start of are not offered over it.
  const pick = name => { onValue(name); setHi(-1); setOpen(false); };

  // Only a highlighted name takes Enter or Escape from the editor around the
  // box; with none, Enter still saves or submits and Escape still cancels.
  const key = e => {
    if (e.key === "ArrowDown" && shown.length) {
      e.preventDefault();
      setHi(Math.min(lit + 1, shown.length - 1));
    } else if (e.key === "ArrowUp" && shown.length) {
      e.preventDefault();
      setHi(Math.max(lit - 1, -1));
    } else if (e.key === "Enter" && lit >= 0) {
      e.preventDefault();
      pick(shown[lit]);
    } else if (e.key === "Escape" && lit >= 0) {
      e.preventDefault();
      setHi(-1);
      setOpen(false);
    } else {
      onKeyDown?.(e);
    }
  };

  return (
    <>
      <input {...input} value={value} autoComplete="off" role="combobox"
             aria-autocomplete="list" aria-expanded={shown.length > 0}
             aria-controls={shown.length ? id : undefined}
             aria-activedescendant={lit >= 0 ? `${id}-${lit}` : undefined}
             onChange={e => { onValue(e.target.value); setHi(-1); setOpen(true); }}
             onFocus={() => setOpen(true)}
             onBlur={e => { setOpen(false); setHi(-1); onBlur?.(e); }}
             onKeyDown={key} />
      {shown.length > 0 && (
        <ul id={id} role="listbox" className="namelist" aria-label="Names already used"
            onMouseDown={e => e.preventDefault()}>
          {shown.map((n, i) => (
            <li key={n} id={`${id}-${i}`} role="option" aria-selected={i === lit}
                onClick={() => pick(n)}>{n}</li>
          ))}
        </ul>
      )}
    </>
  );
}
