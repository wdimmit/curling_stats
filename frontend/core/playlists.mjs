/* The admin's watched playlists (/playlists) in words, and the page's form
 * to and from what the API takes (api.py, /api/admin/playlists). Pure, so
 * the tests read the same words the page shows.
 *
 * The poller (service/playlists.py) looks at each playlist on its own
 * schedule: every `live_poll_s` inside one of its windows -- its league
 * nights, in the club's time -- and every `idle_poll_s` outside them, or
 * never when that is null. */

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
export const DAY_NAMES = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri",
                           sat: "Sat", sun: "Sun" };

export const playlistUrl = id =>
  `https://www.youtube.com/playlist?list=${encodeURIComponent(id)}`;

/* "Thu", "Tue, Thu", in the week's order; all seven are "Every day". */
export function daysText(days) {
  const ordered = DAYS.filter(d => days.includes(d));
  return ordered.length === DAYS.length ? "Every day"
    : ordered.map(d => DAY_NAMES[d]).join(", ");
}

/* A window that ends at or before its start runs on into the next day
 * (playlists.in_window), so a late draw's 22:00-01:00 is one night. */
export const pastMidnight = w => !!(w.start && w.end) && w.end <= w.start;

/* "Thu 18:00–23:30", each window, "; " between. "" when there are none. */
export function scheduleText(schedule) {
  return (schedule || []).map(w =>
    `${daysText(w.days)} ${w.start}–${w.end}${pastMidnight(w) ? " the next day" : ""}`)
    .join("; ");
}

/* An interval for "every …": 180 is "3 min", 3600 "hour", 7200 "2 hours". */
export function every(seconds) {
  const m = seconds / 60;
  if (m === 60) return "hour";
  if (m > 60 && m % 60 === 0) return `${m / 60} hours`;
  return `${Number(m.toFixed(1))} min`;
}

/* How often the poller looks at it, in a sentence. */
export function pollText(pl) {
  const idle = pl.idle_poll_s == null ? null : `every ${every(pl.idle_poll_s)}`;
  if (!pl.schedule?.length) return idle ? `Looked at ${idle}.` : "Never looked at.";
  return `Looked at every ${every(pl.live_poll_s)} on those nights, `
    + `${idle ? `${idle} otherwise` : "and never otherwise"}.`;
}

/* The card's pill: watching, off by hand, or switched off by its failures
 * (playlists.DISABLE_AFTER_FAILURES). */
export function statusOf(pl) {
  if (pl.enabled) return { kind: "watching", text: "watching" };
  if (pl.failures > 0) return { kind: "failing", text: "stopped" };
  return { kind: "", text: "off" };
}

/* What went wrong when it was last looked at, or null when nothing did. */
export function troubleText(pl) {
  if (!pl.last_error) return null;
  if (pl.enabled) {
    return `${pl.failures > 1 ? `The last ${pl.failures} looks` : "The last look"} failed: `
      + pl.last_error;
  }
  const looks = pl.failures === 1 ? "1 failed look" : `${pl.failures} failed looks`;
  return `Stopped after ${looks}: ${pl.last_error}. Watch it again to try once more.`;
}

/* "just now", "12 min ago", "5 h ago", "3 days ago"; "never" before the first. */
export function since(iso, nowMs) {
  const at = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(at)) return "never";
  const s = Math.max(0, (nowMs - at) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 2 * 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} days ago`;
}

/* "Last looked at 12 min ago · 37 videos seen", or that it has not been yet. */
export function lastLookText(pl, nowMs) {
  const n = pl.last_seen_video_ids?.length ?? 0;
  const seen = `${n} ${n === 1 ? "video" : "videos"} seen`;
  return pl.last_polled_at ? `Last looked at ${since(pl.last_polled_at, nowMs)} · ${seen}`
    : n ? `Not looked at yet · ${seen}` : "Not looked at yet";
}

/* The form keeps minutes as the text in their boxes; the API takes seconds.
 * Its defaults are WatchedPlaylist's (service/records.py). */
const minutes = seconds => String(Number((seconds / 60).toFixed(2)));

export const newNight = () => ({ days: [], start: "18:00", end: "23:30" });

export const emptyForm = () => ({ link: "", label: "", nights: [],
                                  liveMin: "3", idleMin: "60", idleNever: false });

export function formFrom(pl) {
  return {
    id: pl.id, link: pl.playlist_id, label: pl.label,
    nights: (pl.schedule || []).map(w => ({ days: [...w.days], start: w.start, end: w.end })),
    liveMin: minutes(pl.live_poll_s ?? 180),
    idleMin: minutes(pl.idle_poll_s ?? 3600),
    idleNever: pl.idle_poll_s === null,
  };
}

/* A day ticked or unticked, the rest kept in the week's order. */
export const toggleDay = (days, d) =>
  DAYS.filter(x => (x === d) !== days.includes(x));

/* The API's body for the form -- {body} -- or the first thing wrong with it,
 * in words -- {error}. The server checks it all again and says so in its own
 * words; these only spare a round trip for what is plainly missing.
 *
 * Only adding sends the link: a change cannot point a playlist at another.
 * An empty name when adding is the playlist's id, the server's default. */
export function bodyFrom(form, { adding = false } = {}) {
  const body = { label: form.label.trim() };
  if (adding) {
    if (!form.link.trim()) return { error: "Paste the playlist's link." };
    body.playlist_id = form.link.trim();
  } else if (!body.label) {
    return { error: "Give it a name: its games are filed under it." };
  }
  for (const w of form.nights) {
    if (!w.days.length) return { error: "Tick the day of each league night." };
    if (!w.start || !w.end) return { error: "Give each league night a start and an end." };
  }
  const live = Number(form.liveMin), idle = Number(form.idleMin);
  if (!(live >= 1)) return { error: "Minutes between looks on its nights: 1 or more." };
  if (!form.idleNever && !(idle >= 1)) {
    return { error: "Minutes between looks otherwise: 1 or more, or tick Never." };
  }
  if (form.idleNever && !form.nights.length) {
    return { error: "With no league nights it has to be looked at some other time." };
  }
  body.schedule = form.nights.map(w => ({ days: DAYS.filter(d => w.days.includes(d)),
                                          start: w.start, end: w.end }));
  body.live_poll_s = Math.round(live * 60);
  body.idle_poll_s = form.idleNever ? null : Math.round(idle * 60);
  return { body };
}
