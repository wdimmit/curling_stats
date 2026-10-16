/* Whether the page is in the portrait iPad layout, for useSyncExternalStore.
 * React decides what renders there; style.css decides how it is laid out --
 * both off TABLET_QUERY. */
import { TABLET_QUERY } from "../core/constants.mjs";

const mq = () => matchMedia(TABLET_QUERY);

export function subscribe(cb) {
  const m = mq();
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

export const isTablet = () => mq().matches;
