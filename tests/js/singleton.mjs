/* The mutable `state` the viewer used to be built around, kept alive for the
 * tests that were written against it.
 *
 * frontend/core takes its inputs as arguments. That is the point of it: the
 * old module-global made every caller a hidden dependency and made one render
 * cost a relayout of the whole game. But ~90 of the assertions in
 * test_viewer_js.py are about arithmetic that has not changed -- the override
 * merge, the renumbering, the report percentages, the clock -- and retyping
 * them during a framework port is how a parity guarantee quietly stops being
 * one. So the shape they poke lives here instead, curried over the core, and
 * the test bodies stay exactly as they were.
 *
 * This is a test fixture. Nothing in the application imports it.
 */
import * as core from "../../frontend/core/index.mjs";

/* The tables and numbers pass straight through -- they were never
 * curried over state, and a test that wants PHONE_QUERY or TYPES
 * should not have to know they moved. */
export * from "../../frontend/core/constants.mjs";

export const state = {
  doc: null, overrides: {}, version: null, gi: 0, ei: 0, si: 0,
  leadIn: 10, dirty: new Set(), dragging: false,
};

/* Rebuilt per call rather than memoised: the tests mutate state.overrides in
 * place, so an identity cache would hand back a stale game. */
const view = () => core.buildGameView(state.doc, state.gi, state.overrides);
const game = () => state.doc.games[state.gi];
const end = () => game().ends[state.ei];

export const config = core.modeOf(globalThis.window?.CHART);
export const { readOnly: READ_ONLY, review: REVIEW, merge: MERGE } = config;

export const identity = core.identity;
export const keyFor = core.keyFor;
export const layout = e => core.layout(game(), e, state.overrides);
export const mergedShots = e => layout(e).shots;
export const merge = (g, e, s) => core.merge(g, e, s, state.overrides);
export const rawShot = () => layout(end()).raws[state.si] ?? null;
export const shotKey = () => {
  const s = rawShot();
  return s ? keyFor(game(), end(), s) : null;
};

export const isBlank = core.isBlank;
export const isGraded = core.isGraded;
export const typeOf = core.typeOf;
export const peekMode = core.peekMode;
export const renumberNotice = core.renumberNotice;
export const openGroupFor = core.openGroupFor;
export const subtypesOf = core.subtypesOf;
export const shotVideoTime = s => core.shotVideoTime(s, state.leadIn);

export const houseViewBox = core.houseViewBox;
export const shouldCrop = core.shouldCrop;
export const stoneAt = core.stoneAt;

export const clockText = core.clockText;
export const thinkText = core.thinkText;
export const splitText = core.splitText;
export const pct = core.pct;
export const avg = core.avg;
export const gatherStats = () => core.gatherStats(view());
export const gatherThinking = () => core.gatherThinking(view());
export const cumulativeThinking = () => core.cumulativeThinking(view());
export const chartGeometry = core.chartGeometry;
export const barsGeometry = core.barsGeometry;

export const dirtyPayload = core.dirtyPayload;
export const saveUrl = () => core.saveUrl(config, state.version);
export const unloadBeacon = () => core.unloadBeacon(config, state.overrides, state.dirty);

/* The DOM half of the original: dragging, or the caret sitting in one of the
 * two free-text fields. Under bare node there is no document and it is only
 * the drag flag that can be set. */
export function busyKey() {
  const a = typeof document === "undefined" ? null : document.activeElement;
  const editingField = !!a && (a.id === "missReason" || a.id === "note");
  return core.busyKeyOf({ dragging: state.dragging, editingField }, shotKey());
}

/* Mutates, as the original did, and returns the keys that moved. */
export function reconcile(serverMap) {
  const r = core.reconcile(state.overrides, state.dirty, busyKey(), serverMap);
  state.overrides = r.overrides;
  return r.touched;
}
