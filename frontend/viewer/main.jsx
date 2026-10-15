/* Boot the viewer.
 *
 * The two fetches happen before the first render, as they did before: whether
 * overrides.json is asked for at all depends on the mode, and a review page
 * has no such route. Doing it here rather than in an effect also means the
 * page never paints an empty chart it is about to replace.
 */
import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { LIVE_POLL_MS, acceptLiveDoc, accessMode, modeOf } from "../core/index.mjs";
import * as store from "../runtime/overridesStore.mjs";
import { checkAccess } from "../runtime/access.mjs";
import { chartAuth } from "../runtime/chartAuth.mjs";
import { loadCursor } from "../runtime/prefs.mjs";
import { App } from "./App.jsx";

const base = modeOf(window.CHART);

async function loadOverrides() {
  if (base.review) return { map: {}, version: null };
  try {
    const r = await fetch("overrides.json");
    // The hosted API versions the overrides through an ETag; the local server
    // has none, and then saves are unconditional.
    const tag = r.headers.get("ETag");
    const n = tag ? parseInt(tag.replace(/"/g, ""), 10) : NaN;
    const map = r.ok ? await r.json() : {};
    return { map: (map && typeof map === "object" && !Array.isArray(map)) ? map : {},
             version: isNaN(n) ? null : n };
  } catch {
    return { map: {}, version: null };
  }
}

/* On a guarded page -- a chart somebody or a team owns -- whether this person
 * may edit it (checkAccess). Asked alongside the timeline, so it costs little
 * time, and before the first render, so editing never appears and then goes. */
async function loadAccess() {
  return base.guarded ? checkAccess() : null;
}

/* A game still being played is published an end at a time, so while the
 * timeline says so the page keeps asking for it. App keeps the viewer's place
 * across a new document -- the game, end and rock are its own state, and ends
 * are only ever appended -- so the next end simply appears. An answer that has
 * not moved on, or a restarted run's shorter rebuild, is dropped without a
 * render (acceptLiveDoc). */
function Live({ first, cursor, config }) {
  const [doc, setDoc] = useState(first);
  const live = !!doc.live?.in_progress;
  useEffect(() => {
    if (!live) return undefined;
    const id = setInterval(async () => {
      try {
        const r = await fetch("timeline.json", { cache: "no-cache" });
        if (!r.ok) return;
        const next = await r.json();
        setDoc(prev => acceptLiveDoc(prev, next));
      } catch {
        /* a missed look is only a late end: try again next time */
      }
    }, LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [live]);
  /* After a change made somewhere other than the overrides -- an entered
   * score, which the server merges into the game -- ask again rather than
   * patch the document here, so the page shows what every link shows. */
  const reload = useCallback(async () => {
    try {
      const r = await fetch("timeline.json", { cache: "no-cache" });
      if (r.ok) setDoc(await r.json());
    } catch { /* the next look, or a reload, will catch it up */ }
  }, []);
  return <App doc={doc} config={config} cursor={cursor} reload={reload} />;
}

Promise.all([fetch("timeline.json").then(r => r.json()), loadOverrides(), loadAccess()])
  .then(([doc, ov, access]) => {
    const config = base.guarded ? accessMode(base, access) : base;
    // A guarded chart this person may edit saves with their token, kept
    // fresh for the close-of-tab save (chartAuth); any other page sends none.
    const auth = base.guarded && !config.readOnly ? chartAuth() : null;
    if (auth) { auth.keepFresh(); auth.fresh(); }
    store.start(config, { overrides: ov.map, version: ov.version, auth });
    createRoot(document.getElementById("root"))
      .render(<Live first={doc} cursor={loadCursor(config.slug)} config={config} />);
  })
  .catch(err => {
    document.body.innerHTML =
      `<main><div class="card">Could not load timeline.json — ${String(err)
        .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]))
      }</div></main>`;
  });
