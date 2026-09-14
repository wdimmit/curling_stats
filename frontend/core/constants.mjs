/* Numbers and tables the whole frontend agrees on.
 *
 * Plain ESM with no React and no DOM, because the Python test suite imports
 * this directly under bare node -- see tests/test_viewer_js.py.
 */

/* The house is painted with literal colours rather than CSS variables: var()
 * in a presentation attribute is not dependable, and a house that silently
 * fails to paint is far worse than one that does not follow the theme. */
export const PAINT = {
  ice: "#fbfbfa", iceLine: "#c9c9c4", rail: "#8a8a85",
  twelve: "#3f9c47", four: "#3b4fa8", granite: "#9a9a95",
  graniteEdge: "#5f5f5b", red: "#d13438", yellow: "#e8b400",
  warn: "#b45309", accent: "#2b6cb0", thrown: "#ffffff",
};

/* Metres. The SVG user-space unit is a metre, which is why none of the
 * geometry below needs a scale factor. */
export const R = {
  button: 0.152, four: 0.610, eight: 1.219, twelve: 1.829, stone: 0.142,
  inHouse: 1.971, hog: 6.401, back: -1.829, halfWidth: 2.375,
};

// Keep placed stones where they can still be seen: the viewBox stops at 6.0,
// short of the hog line, and a stone dropped past it would vanish.
export const LIMIT = { x: R.halfWidth - R.stone, yLo: -2.45, yHi: 5.9 };

export const DRAG_MIN_M = 0.03;   // below this a pointer gesture is a click
export const SAVE_DEBOUNCE_MS = 800;
export const TYPICAL_GAP_S = 45;  // a club delivery about every 45 s
export const POLL_MS = 15000;

/* Must stay byte-identical to the phone block in style.css. The JS gate and
 * the stylesheet gate being the same string is what keeps the behaviour that
 * assumes the phone shell from applying where the shell does not. A test
 * asserts the two are equal. */
export const PHONE_QUERY = "(max-width: 640px) and (min-height: 521px)";

export const POSITIONS = ["lead", "second", "third", "skip"];

/* Curl Coach's taxonomy, in two levels.
 *
 * The four coarse categories are what the detector offers, and a charter may
 * leave it at one: "a draw" is a complete answer, not a half-filled one. So
 * the group carries a type of its own -- `base` below -- and the entries under
 * it are refinements. None of them repeats its own group, because being made
 * to pick "Draw" inside Draw is asking the same question twice.
 *
 * The fine type is a statement about what was *called*, which no amount of
 * tracking recovers; it is the charter's to give, or to leave alone.
 */
export const GROUPS = ["Draw", "Guard", "Hit", "Other"];

/* Other is a container, not a category: a rock is never "an Other". Its
 * entries are whole answers, so it has no base type. */
export const GROUP_TYPE = { Draw: "draw", Guard: "guard", Hit: "hit" };

export const TYPES = [
  { id: "draw",         name: "Draw",            group: "Draw",  base: true },
  { id: "freeze",       name: "Freeze",          group: "Draw"  },
  { id: "split_on",     name: "Split on",        group: "Draw"  },
  { id: "tap_up",       name: "Tap up",          group: "Draw"  },

  { id: "guard",        name: "Guard",           group: "Guard", base: true },
  { id: "centre_guard", name: "Centre guard",    group: "Guard" },
  { id: "corner_guard", name: "Corner guard",    group: "Guard" },

  { id: "hit",          name: "Hit",             group: "Hit",   base: true },
  { id: "hit_stick",    name: "Hit & stick",     group: "Hit"   },
  { id: "hit_roll",     name: "Hit & roll",      group: "Hit"   },
  { id: "double",       name: "Double",          group: "Hit"   },
  { id: "peel",         name: "Peel",            group: "Hit"   },
  { id: "raise",        name: "Raise",           group: "Hit"   },
  { id: "run_back",     name: "Run back",        group: "Hit"   },
  { id: "tick",         name: "Tick",            group: "Hit"   },
  { id: "in_off",       name: "In-off",          group: "Hit"   },

  // Curl Coach's "non scored shots": charted, but never counted in a
  // percentage, because there was no shot to make.
  { id: "through",      name: "Throw away",      group: "Other", unscored: true },
  // Seen thrown, never reached the house: the detector saw it leave the
  // far house and nothing arrive. Scored, because there was a shot to make.
  { id: "hogged",       name: "Hogged",          group: "Other" },
  { id: "not_thrown",   name: "Not thrown",      group: "Other", unscored: true },
  { id: "unknown",      name: "Unknown",         group: "Other", unscored: true },
];

/* What the picker offers inside a group: refinements only.
 *
 * A type this table does not know -- one retired since a chart was made --
 * needs no special case. The report falls back to the raw id under Other, and
 * the picker opens nothing and waits, which is the right thing to do about a
 * rock whose type means nothing here. */
export const subtypesOf = group =>
  TYPES.filter(t => t.group === group && !t.base);

export const TYPE = Object.fromEntries(TYPES.map(t => [t.id, t]));

export const MISS_REASONS = [
  "heavy", "light", "narrow", "wide", "wrong turn", "swept too long",
  "not swept enough", "wrecked on a guard", "rock picked", "hogged",
];

export const CHARTBOX = { w: 640, h: 210, padL: 46, padR: 8, padT: 8, padB: 22 };
/* The same chart in the aside is a third of the width, and the box scales
 * whole: at the report's proportions it comes out 95 px tall with the labels
 * on top of the lines. Taller and roomier in its own units, so that what the
 * browser scales down is still legible. */
export const TALLBOX = { w: 640, h: 400, padL: 96, padR: 12, padT: 14, padB: 52 };
