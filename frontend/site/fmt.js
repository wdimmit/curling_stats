/* Clock text, in both directions. Duplicated verbatim between the catalogue
 * and the status page before this. */

export function hms(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0")
       + ":" + String(x).padStart(2, "0");
}

export function mmss(s) {
  if (s == null) return "";
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), x = s % 60;
  return m ? `${m}m ${String(x).padStart(2, "0")}s` : `${x}s`;
}

export const day = iso => (iso || "").slice(0, 10) || "—";

/** Seconds from "1:52:30", "6750" or "2:15". Null when it is not a time. */
export function parseClock(text) {
  text = (text || "").trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) return parseFloat(text);
  const parts = text.split(":").map(Number);
  if (parts.some(isNaN)) return null;
  return parts.reduce((acc, v) => acc * 60 + v, 0);
}

/* Say back what the clock text was understood to mean.
 *
 * "2:00" is two minutes, not two hours: the parser reads h:mm:ss from the
 * right, which is unambiguous for a start time like 1:52:30 and a trap for a
 * length. Asking for a two-hour game and silently getting two minutes is a
 * failed run and a confusing one, so the interpretation is shown as it is
 * typed rather than explained in a placeholder nobody rereads. */
export function describe(seconds) {
  if (seconds === null) return "";
  if (seconds < 60) return `= ${seconds} s`;
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  if (h && m) return `= ${h} h ${m} min`;
  if (h) return `= ${h} h`;
  return `= ${m} min`;
}
