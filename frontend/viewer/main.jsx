/* Boot the viewer.
 *
 * The two fetches happen before the first render, as they did before: whether
 * overrides.json is asked for at all depends on the mode, and a review page
 * has no such route. Doing it here rather than in an effect also means the
 * page never paints an empty chart it is about to replace.
 */
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { LIVE_POLL_MS, modeOf } from "../core/index.mjs";
import * as store from "../runtime/overridesStore.mjs";
import { loadCursor } from "../runtime/prefs.mjs";
import { App } from "./App.jsx";

const config = modeOf(window.CHART);

async function loadOverrides() {
  if (config.review) return { map: {}, version: null };
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

/* A game still being played is published an end at a time, so while the
 * timeline says so the page keeps asking for it. App keeps the viewer's place
 * across a new document -- the game, end and rock are its own state, and ends
 * are only ever appended -- so the next end simply appears. An answer that has
 * not moved on is dropped without a render. */
function Live({ first, cursor }) {
  const [doc, setDoc] = useState(first);
  const live = !!doc.live?.in_progress;
  useEffect(() => {
    if (!live) return undefined;
    const id = setInterval(async () => {
      try {
        const r = await fetch("timeline.json", { cache: "no-cache" });
        if (!r.ok) return;
        const next = await r.json();
        setDoc(prev => (next.live?.updated_at === prev.live?.updated_at ? prev : next));
      } catch {
        /* a missed look is only a late end: try again next time */
      }
    }, LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [live]);
  return <App doc={doc} config={config} cursor={cursor} />;
}

Promise.all([fetch("timeline.json").then(r => r.json()), loadOverrides()])
  .then(([doc, ov]) => {
    store.start(config, { overrides: ov.map, version: ov.version });
    createRoot(document.getElementById("root"))
      .render(<Live first={doc} cursor={loadCursor(config.slug)} />);
  })
  .catch(err => {
    document.body.innerHTML =
      `<main><div class="card">Could not load timeline.json — ${String(err)
        .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]))
      }</div></main>`;
  });
