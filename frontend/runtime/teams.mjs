/* Saying who played a game (api.py, "teams"): the names the game list takes,
 * kept on the game, so every chart and link of it shows them. Needs a
 * signed-in person's token, which the dialog passes in: the viewer does not
 * load Firebase until somebody asks (runtime/auth.mjs). Answers
 * {ok, status, red, yellow}, or {ok: false, status, error} with `error` the
 * server's own words when it gave any. */
export const teamsUrl = sourceId => `/api/games/${encodeURIComponent(sourceId)}/teams`;

/* `body` is {red, yellow}; an empty name clears that team's. */
export async function sendTeams(sourceId, body, token) {
  try {
    const r = await fetch(teamsUrl(sourceId), {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (r.ok) return { ok: true, status: r.status, ...data };
    return { ok: false, status: r.status, error: typeof data.detail === "string" ? data.detail : null };
  } catch {
    return { ok: false, status: 0, error: null };
  }
}

/* The names this game's league has already given its teams (api.py,
 * "team-names"), the most used first, to offer while somebody types. Public,
 * so no token. Only ever a help: anything that goes wrong is no names, and
 * the boxes take whatever is typed as they always did. */
export const teamNamesUrl = sourceId => `/api/games/${encodeURIComponent(sourceId)}/team-names`;

export async function fetchTeamNames(sourceId) {
  try {
    const r = await fetch(teamNamesUrl(sourceId));
    const data = r.ok ? await r.json() : null;
    return Array.isArray(data?.names) ? data.names.filter(n => typeof n === "string") : [];
  } catch {
    return [];
  }
}

/* Which of those to show under a box as somebody types in it: the names that
 * start with what is typed, then the ones that hold it anywhere ("fam" finds
 * "McAll In The Fam"), each kept in the league's most-used order, case and
 * spacing aside. An empty box shows the most used. Nothing once the box
 * already says one of them -- it has been picked -- and never more than
 * `limit`, so the list stays short enough to sit above a phone's keyboard. */
const said = s => s.trim().replace(/\s+/g, " ").toLowerCase();

export function namesMatching(names, typed, limit = 8) {
  const t = said(typed);
  if (t && names.some(n => said(n) === t)) return [];
  const first = names.filter(n => said(n).startsWith(t));
  const then = names.filter(n => !said(n).startsWith(t) && said(n).includes(t));
  return [...first, ...then].slice(0, limit);
}
