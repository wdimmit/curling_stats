/* Geometry for the two thinking-time charts. Data, not markup.
 *
 * These used to build SVG strings, which meant the only way to test them was
 * to count substrings -- `got.count('class="ln red"') == 1` -- and the only
 * way to make a bar clickable was to re-query the DOM and reattach handlers
 * after every rebuild, in two places, which is exactly how the report's bars
 * ended up with no handlers at all. Returning positions lets the components
 * carry their own handlers and lets the tests assert on what is actually
 * being claimed: which bar is which rock, and how long it is.
 *
 * Hand-rolled rather than a charting library: this is two polylines and some
 * gridlines, and the page has to print.
 */
import { CHARTBOX } from "./constants.mjs";
import { clockText } from "./stats.mjs";

/* A gridline every round minute or five, whichever keeps it under `most`
 * lines, so the labels read as times rather than as arbitrary seconds. */
function gridlines(top, y, steps, most, fallback) {
  const step = steps.find(s => top / s <= most) || fallback;
  const out = [];
  for (let v = 0; v <= top; v += step) out.push({ v, y: y(v), label: clockText(v) });
  return out;
}

/* End boundaries: the clock is spent inside ends, so the ends are the scale
 * that matters along the bottom. */
function endScale(bounds, x, BOX) {
  return {
    ticks: bounds.map(b => ({ x: x(b.i), y1: BOX.padT, y2: BOX.h - BOX.padB })),
    endLabels: bounds.map((b, k) => ({
      x: (x(k ? bounds[k - 1].i : 0) + x(b.i)) / 2,
      y: BOX.h - 7,
      number: b.number,
    })),
  };
}

const marker = (at, n, x, BOX) =>
  (at == null || at < 0 || at > n
    ? null
    : { x: x(at), y1: BOX.padT, y2: BOX.h - BOX.padB });

export function chartGeometry(series, at = null, BOX = CHARTBOX) {
  const { points, bounds } = series;
  const n = points.length - 1;
  const top = Math.max(series.red, series.yellow);
  if (n < 1 || top <= 0) return null;

  const x = i => BOX.padL + (i / n) * (BOX.w - BOX.padL - BOX.padR);
  const y = v => BOX.h - BOX.padB - (v / top) * (BOX.h - BOX.padT - BOX.padB);

  return {
    box: BOX,
    viewBox: `0 0 ${BOX.w} ${BOX.h}`,
    aria: `Cumulative thinking time: red ${clockText(series.red)}, `
        + `yellow ${clockText(series.yellow)}`,
    grid: gridlines(top, y, [30, 60, 120, 300, 600, 900], 5, 1800),
    ...endScale(bounds, x, BOX),
    // Yellow first, so red draws over it where they coincide -- as before.
    lines: ["yellow", "red"].map(color => ({
      color, points: points.map(p => [x(p.i), y(p[color])]),
    })),
    // Where the clock was stopped on an assumed crossing rather than a seen
    // one, mark the step it produced instead of letting it pass as measurement.
    marks: points.filter(p => p.estimated)
                 .map(p => ({ x: x(p.i), y: y(p[p.color]), color: p.color })),
    you: marker(at, n, x, BOX),
  };
}

/* The same game as bars, one per rock, in the order they were thrown.
 *
 * The lines answer "who is ahead on the clock"; this answers "which rock took
 * so long", which is not a question a cumulative curve can be read for -- a
 * long shot is a slightly steeper step among a hundred others. A bar is tall
 * or it is not. The dashed line is the game's own median, because teams take
 * as long as the game is and a slow rock is only slow against its own.
 *
 * Deliberately not a histogram of durations: that shows the spread and loses
 * the thing being looked for, which is *which* rock to go and watch. */
export function barsGeometry(series, at = null, BOX = CHARTBOX) {
  const { points, bounds, median } = series;
  const n = points.length - 1;
  const thrown = points.filter(p => p.secs != null);
  if (n < 1 || !thrown.length) return null;

  const top = Math.max(series.longest, 1);
  const x = i => BOX.padL + (i / n) * (BOX.w - BOX.padL - BOX.padR);
  const y = v => BOX.h - BOX.padB - (v / top) * (BOX.h - BOX.padT - BOX.padB);
  // One bar per rock, filling the slot it occupies with a hairline of space.
  const w = Math.max(1.5, (x(1) - x(0)) * 0.8);

  return {
    box: BOX,
    viewBox: `0 0 ${BOX.w} ${BOX.h}`,
    aria: `Thinking time per rock, longest ${clockText(series.longest)}, `
        + `median ${clockText(median)}`,
    grid: gridlines(top, y, [30, 60, 120, 300], 4, 600),
    ...endScale(bounds, x, BOX),
    bars: thrown.map(p => ({
      shot: p.i, ei: p.ei, si: p.si, color: p.color || "", est: !!p.estimated,
      x: x(p.i) - w / 2, y: y(p.secs), w, h: Math.max(0.8, (BOX.h - BOX.padB) - y(p.secs)),
      title: `End ${p.end}, ${p.label} — ${clockText(p.secs)}`
           + (p.estimated ? " (estimated)" : ""),
    })),
    median: median > 0
      ? { y: y(median), x1: BOX.padL, x2: BOX.w - BOX.padR,
          title: `median ${clockText(median)}` }
      : null,
    you: marker(at, n, x, BOX),
  };
}
