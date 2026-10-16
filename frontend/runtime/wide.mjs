/* Whether the page is in the wide desktop layout, for useSyncExternalStore.
 * React decides what renders there; style.css decides how it is laid out --
 * both off WIDE_QUERY. */
import { WIDE_QUERY } from "../core/constants.mjs";

const mq = () => matchMedia(WIDE_QUERY);

export function subscribe(cb) {
  const m = mq();
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

export const isWide = () => mq().matches;
