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

/* The calendar day a timestamp falls on where the reader is, as YYYY-MM-DD.
 *
 * played_at is YouTube's publish time, in UTC. Slicing the date off the ISO
 * text put every evening game in North America on the next day, since 7 pm
 * Central is already past midnight UTC. Null when there is no date. */
export function localDay(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
       + `-${String(d.getDate()).padStart(2, "0")}`;
}

export const day = iso => localDay(iso) || "—";

/* The time of day a moment in ms falls on where the reader is, as "6:30 PM". */
export const clockTime = ms =>
  new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

/* The same clock split into the figures and the "PM", so the figures can be
 * set large and the period small. A 24-hour locale has no period, and then
 * it is "". */
export function clockParts(ms) {
  const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" })
    .formatToParts(new Date(ms));
  return {
    time: parts.filter(p => p.type !== "dayPeriod").map(p => p.value).join("").trim(),
    period: parts.find(p => p.type === "dayPeriod")?.value || "",
  };
}

/* A localDay key as the weekday and the date, "Tuesday" and "September 29";
 * the year only when it is not this one. */
export function dayParts(key) {
  const [y, m, d] = key.split("-").map(Number);
  const at = new Date(y, m - 1, d);
  return {
    weekday: at.toLocaleDateString([], { weekday: "long" }),
    date: at.toLocaleDateString([], y === new Date().getFullYear()
      ? { month: "long", day: "numeric" } : { month: "long", day: "numeric", year: "numeric" }),
  };
}

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
