/* Where the stones are while a finger is on one.
 *
 * A drag moves a stone continuously, and the version this replaced wrote every
 * move straight into the override map and redrew the house. Ported naively to
 * a React store that would be ~60 saves a second, each resetting the save
 * debounce, and ~60 relayouts of the whole game -- so the live position lives
 * here instead, only the stones subscribe to it, and one commit happens on
 * release.
 *
 * `active` is also what keeps a poll from redrawing the shot under the
 * charter's finger: it feeds the same busyKey() the note field does.
 */
let stones = null;
let active = false;
const listeners = new Set();

export const subscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };
export const getStones = () => stones;
export const isActive = () => active;

export function set(next) {
  stones = next;
  active = true;
  listeners.forEach(fn => fn());
}

export function clear() {
  if (!active && stones === null) return;
  stones = null;
  active = false;
  listeners.forEach(fn => fn());
}
