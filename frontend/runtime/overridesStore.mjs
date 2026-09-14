/* Corrections, and everything involved in getting them to the server.
 *
 * Deliberately a module singleton read through useSyncExternalStore, not React
 * state. This is a timer, network and unload machine, and most of its shape is
 * scar tissue: the snapshot-then-clear ordering in save() is what stopped
 * edits made mid-flight from being dropped; the 409 branch adopts the server's
 * map wholesale because the alternative is two charters silently diverging;
 * busyKey() keeps a poll from redrawing the field the caret is in; the beacon
 * sends only dirty keys so a closing tab cannot erase a teammate's work.
 * Putting that in an effect would make React's scheduling part of a
 * correctness argument, which is not a trade worth making. It was moved here,
 * not rewritten.
 */
import {
  busyKeyOf, dirtyPayload, reconcile, saveUrl, unloadBeacon,
} from "../core/wire.mjs";
import { POLL_MS, SAVE_DEBOUNCE_MS } from "../core/constants.mjs";
import * as dragStore from "./dragStore.mjs";

let config = { readOnly: true, merge: false, shared: false };
let overrides = {};
let version = null;
const dirty = new Set();
let saving = false;
let again = false;
let timer = null;
let status = { cls: "", text: "saved" };
let busyOn = null;            // the shot being typed into or dragged on
let onNotice = () => {};

const listeners = new Set();
const announce = () => listeners.forEach(fn => fn());

export const subscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };
export const getOverrides = () => overrides;
export const getStatus = () => status;
export const getVersion = () => version;
export const isDirty = () => dirty.size > 0;

function setStatus(cls, text) {
  if (status.cls === cls && status.text === text) return;
  status = { cls, text };
  announce();
}

export function start(cfg, { overrides: initial, version: v }) {
  config = cfg;
  overrides = initial || {};
  version = v ?? null;
  setStatus("saved", Object.keys(overrides).length ? "loaded" : "no edits yet");
  if (config.merge && !config.readOnly && config.shared) poll();
}

/* The shot the charter has their hands on right now -- dragging a stone, or
 * typing in the note or the miss reason. Nothing arriving from the server may
 * overwrite it: those two fields patch on every keystroke, so there is a
 * moment after each debounced save where the key is clean and redrawing it
 * would take the caret with it.
 *
 * Declared by the caller rather than sniffed from document.activeElement: the
 * components know which shot they are editing, and the store should not have
 * to know which element ids mean "typing". */
export const busyKey = () =>
  busyKeyOf({ dragging: dragStore.isActive(), editingField: !!busyOn }, busyOn);

export function setBusy(key) { busyOn = key; }

/* Registered by the view once it has somewhere to put the message. */
export function setNotice(fn) { onNotice = fn || (() => {}); }

/* ------------------------------------------------------------------ edits */

export function apply(next, key) {
  if (config.readOnly || next === overrides) return;
  overrides = next;
  if (key) dirty.add(key);
  schedule();
  announce();
}

function schedule() {
  setStatus("unsaved", "•");
  clearTimeout(timer);
  timer = setTimeout(save, SAVE_DEBOUNCE_MS);
}

/* ------------------------------------------------------------------- save */

export async function save() {
  if (saving) { again = true; return; }
  saving = true;
  timer = null;
  setStatus("", "saving…");
  // Taken before the request and cleared before the await: an edit made while
  // this one is in flight re-dirties its key and rides the next save, instead
  // of being cleared along with the keys this save is carrying.
  const sent = [...dirty];
  const payload = config.merge ? dirtyPayload(overrides, dirty) : overrides;
  dirty.clear();
  try {
    const res = await fetch(saveUrl(config, version), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 409) {
      const body = await res.json();
      overrides = body.overrides || {};
      version = body.version ?? version;
      dirty.clear();
      announce();
      setStatus("failed", "reloaded — someone else saved");
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json().catch(() => ({}));
    if (typeof body.version === "number") version = body.version;
    if (body.overrides) {                       // we were behind; catch up
      const r = reconcile(overrides, dirty, busyKey(), body.overrides);
      overrides = r.overrides;
      if (r.touched.length) { announce(); onNotice(r.touched.length); }
    }
    const t = new Date();
    setStatus("saved", `saved ${String(t.getHours()).padStart(2, "0")}:`
                     + `${String(t.getMinutes()).padStart(2, "0")}`);
  } catch {
    // Put the keys back by hand. The whole document used to go up every time,
    // so a failed save recovered by accident; a patch carries only what it was
    // given, and anything dropped here is simply gone.
    for (const k of sent) dirty.add(k);
    setStatus("failed", "not saved — use ⬇");
    clearTimeout(timer);
    timer = setTimeout(save, 5000);
  } finally {
    saving = false;
    if (again) { again = false; schedule(); }
  }
}

/* A chart a team shares is the only one somebody else can change under you,
 * so it is the only one worth asking about. Skipped whenever there is work in
 * flight or in hand: reconcile would refuse those keys anyway, and this way a
 * charter who is actually charting costs no reads at all. */
function poll() {
  setInterval(async () => {
    if (saving || dirty.size || dragStore.isActive()) return;
    try {
      const res = await fetch("overrides.json", {
        headers: version === null ? {} : { "If-None-Match": `"${version}"` },
      });
      if (res.status === 304 || !res.ok) return;
      const tag = res.headers.get("ETag");
      if (tag) { const n = parseInt(tag.replace(/"/g, ""), 10); if (!isNaN(n)) version = n; }
      const r = reconcile(overrides, dirty, busyKey(), await res.json());
      overrides = r.overrides;
      if (r.touched.length) { announce(); onNotice(r.touched.length); }
    } catch { /* offline: the next tick asks again */ }
  }, POLL_MS);
}

/* Registered once at module scope, as it was before: a listener that came and
 * went with a component could fire after unmount or not at all. */
if (typeof addEventListener === "function")
  addEventListener("pagehide", () => {
    const beacon = unloadBeacon(config, overrides, dirty);
    if (!beacon) return;
    navigator.sendBeacon?.(beacon.url,
      new Blob([JSON.stringify(beacon.body)], { type: "application/json" }));
  });

/* The documented escape hatch when saving keeps failing. */
export function download() {
  const blob = new Blob([JSON.stringify(overrides, null, 2)],
                        { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "overrides.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
