/* The only writer of the attributes the stylesheet reads as state.
 *
 * style.css keys layout off body[data-mode|peek|sheet|house|menu] and
 * body.reporting -- 548 lines of it, including the whole phone shell. Having
 * one function that writes them means the ordering constraint below is local
 * and visible, rather than spread across whatever happens to run first.
 *
 * Idempotent, so calling it twice costs nothing. That matters: the house
 * measures itself with getBoundingClientRect(), which flushes layout against
 * whatever these attributes currently say, so the measuring effect calls this
 * again immediately before measuring rather than trusting React to have run a
 * sibling's layout effect first.
 */
export function writeBodyState({ mode, peek, sheet, house, menu, reporting }) {
  const b = document.body;
  const set = (k, v) => { if (b.dataset[k] !== v) b.dataset[k] = v; };
  const drop = k => { if (k in b.dataset) delete b.dataset[k]; };
  // peek, sheet and house are always present -- an empty value is a real
  // state ("not cropped", "not open") and the phone rules read them that way.
  // mode and menu are absent unless set, which is what the old page did and
  // what `body[data-mode="view"]` and `[data-menu="open"]` expect.
  set("peek", peek || "");
  set("sheet", sheet || "");
  set("house", house || "");
  mode ? set("mode", mode) : drop("mode");
  // undefined means "never opened" and is absent; "" means "opened, then
  // closed" and stays, which is what the old page left behind. Only
  // [data-menu="open"] is ever selected on, so this is about keeping a DOM
  // comparison honest rather than about layout.
  menu === undefined ? drop("menu") : set("menu", menu);
  b.classList.toggle("reporting", !!reporting);
}
