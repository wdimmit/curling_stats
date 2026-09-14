/* React's view of who is signed in, and of one fetched thing.
 *
 * auth.js stays a plain module with a listener list: it is a Firebase
 * subscription, not a query, and it has to work the same for anything that is
 * not a component. useSyncExternalStore is the seam.
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { authedFetch, currentUser, enabled, onUser } from "./auth.js";

let ready = false;
const readySubs = new Set();
onUser((_, isReady) => {
  ready = isReady;
  readySubs.forEach(fn => fn());
});

const subscribe = fn => {
  readySubs.add(fn);
  const off = onUser(fn);
  return () => { readySubs.delete(fn); off(); };
};

/** The signed-in user, and whether the SDK has finished looking.
 *
 * Every page renders signed out and fills in a moment later, once the SDK has
 * read its own storage. There is no way to know sooner: the server has never
 * been told who is asking. */
export function useAuthUser() {
  const user = useSyncExternalStore(subscribe, currentUser, () => null);
  const isReady = useSyncExternalStore(subscribe, () => ready, () => false);
  return { user, ready: isReady, accounts: enabled() };
}

/* One fetched resource, refetched when `deps` change and reloadable by hand.
 *
 * Deliberately this rather than a query library: every page here does one or
 * two GETs and then navigates away, and the two things such a library is best
 * at -- sharing a cache between components and de-duplicating concurrent
 * requests for one key -- barely arise. If that changes, this hook is the
 * seam to swap behind. */
export function useResource(url, { auth = true, deps = [], skip = false } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !skip });

  const reload = useCallback(async () => {
    if (skip) { setState({ data: null, error: null, loading: false }); return; }
    setState(s => ({ ...s, loading: true }));
    try {
      const res = await (auth ? authedFetch(url) : fetch(url));
      if (!res.ok) { setState({ data: null, error: res, loading: false }); return; }
      setState({ data: await res.json(), error: null, loading: false });
    } catch (err) {
      setState({ data: null, error: err, loading: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, auth, skip, ...deps]);

  useEffect(() => { reload(); }, [reload]);
  return { ...state, reload, setData: data => setState(s => ({ ...s, data })) };
}
