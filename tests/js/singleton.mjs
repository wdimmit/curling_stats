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
  leadIn: core.VIDEO_LEAD_IN_S, dirty: new Set(), dragging: false,
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
export const formatOf = core.formatOf;
export const throwInfo = core.throwInfo;
export const shotLabel = core.shotLabel;
export const roleText = core.roleText;
export const throwerText = core.throwerText;
export const formatWarning = core.formatWarning;
export const positionText = core.positionText;
export const endKey = core.endKey;
export const liveGame = core.liveGame;
export const unreadNote = core.unreadNote;
export const acceptLiveDoc = core.acceptLiveDoc;
export const layout = e => core.layout(game(), e, state.overrides, core.formatOf(state.doc));
export const mergedShots = e => layout(e).shots;
export const merge = (g, e, s) => core.merge(g, e, s, state.overrides);
export const rawShot = () => layout(end()).raws[state.si] ?? null;
export const shotKey = () => {
  const s = rawShot();
  return s ? keyFor(game(), end(), s) : null;
};

export const isBlank = core.isBlank;
export const isGraded = core.isGraded;
export const scoreCell = core.scoreCell;
export const boardReadable = core.boardReadable;
export const typeOf = core.typeOf;
export const peekMode = core.peekMode;
export const renumberNotice = core.renumberNotice;
export const chartedNotice = core.chartedNotice;
export const openGroupFor = core.openGroupFor;
export const subtypesOf = core.subtypesOf;
export const shotVideoTime = s => core.shotVideoTime(s, state.leadIn);

/* The watching surface reads the same view every other panel reads. */
export const rockRows = () => core.rockRows(view(), state.ei, state.leadIn);
export const rockSpan = core.rockSpan;
export const rockAt = core.rockAt;
export const endSummary = (v, ei) => core.endSummary(v ?? view(), ei ?? state.ei);

export const houseViewBox = core.houseViewBox;
export const shouldCrop = core.shouldCrop;
export const stoneAt = core.stoneAt;
export const broomMark = core.broomMark;
export const ghostStones = core.ghostStones;
export const flagPlace = core.flagPlace;
export const noteProblem = core.noteProblem;
export const settleWithin = core.settleWithin;
export const settledUser = core.settledUser;
export const buildGameView = core.buildGameView;

export const lineFigures = core.lineFigures;
export const feetInches = core.feetInches;
export const lineReason = core.lineReason;
export const lineX = core.lineX;
export const restOf = core.restOf;
export const playerHacks = core.playerHacks;
export const hackOf = core.hackOf;
export const lineNumbers = core.lineNumbers;
export const narrowOf = core.narrowOf;
export const sideNames = core.sideNames;
export const turnOf = core.turnOf;
export const groupOf = core.groupOf;
export const gameView = () => view();

export const clockText = core.clockText;
export const thinkText = core.thinkText;
export const splitText = core.splitText;
export const houseDeltaText = core.houseDeltaText;
export const pct = core.pct;
export const avg = core.avg;
export const gatherStats = (v) => core.gatherStats(v ?? view());
export const gatherThinking = () => core.gatherThinking(view());
export const cumulativeThinking = () => core.cumulativeThinking(view());
export const chartGeometry = core.chartGeometry;
export const barsGeometry = core.barsGeometry;

export const stepRock = d => core.stepRock(view(), state.ei, state.si, d);
export const stepRockIn = core.stepRock;
export const cursorFromHash = parsed => core.cursorFromHash(
  parsed, gi => core.buildGameView(state.doc, gi, state.overrides), state.doc.games.length);
export const parseHash = core.parseHash;
export const formatHash = core.formatHash;
export const withHash = core.withHash;
export const swipeStep = core.swipeStep;
export const stripGeometry = core.stripGeometry;
export const stripShapes = core.stripShapes;
export const sideways = core.sideways;
export const trackPoints = core.trackPoints;
export const deliveryReadable = core.deliveryReadable;
export const deliveryReason = core.deliveryReason;
export const deliveryPoints = core.deliveryPoints;
export const deliveryGeometry = core.deliveryGeometry;
export const rampColor = core.rampColor;
export const aimFrame = core.aimFrame;
export const deliveryOverlay = core.deliveryOverlay;
export const slotOf = core.slotOf;
export const positionChoices = core.positionChoices;
export const positionLabel = core.positionLabel;
export const playText = core.playText;
export const gameText = core.gameText;
export const playerRocks = core.playerRocks;
export const summarize = core.summarize;
export const summaryText = core.summaryText;
export const shotGroups = core.shotGroups;
export const missScatter = core.missScatter;
export const endSpan = core.endSpan;

/* The report's numbers, by the view they are given -- not state's. */
export const byEnd = core.byEnd;
export const headToHead = core.headToHead;
export const detailRows = core.detailRows;
export const longestThinks = core.longestThinks;
export const coverage = core.coverage;
export const coverageText = core.coverageText;
export const reportNotes = core.reportNotes;
export const reportMeta = core.reportMeta;
export const teamNames = core.teamNames;
export const endList = core.endList;
export const pctOf = core.pctOf;
export const gatherThinkingOf = core.gatherThinking;
export const cumulativeThinkingOf = core.cumulativeThinking;
export const lineEnds = core.lineEnds;
export const barLabels = core.barLabels;
export const scoreChoices = core.scoreChoices;
export const scoreError = core.scoreError;
export const FOURS_FORMAT = core.FOURS;

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
