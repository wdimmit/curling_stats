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

/* Whether an account's role (api.py /api/me, User.role) lets it name a
 * game's teams: validated accounts and admins. The server decides; this only
 * spares everyone else a form that would be refused. */
export const canNameTeams = role => role === "validated" || role === "admin";

/* The signed-in person's role: the role, null for none, or undefined when
 * the server did not say -- left to the server to decide at the save. */
export async function fetchRole(token) {
  try {
    const r = await fetch("/api/me", { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) return undefined;
    const data = await r.json();
    return data?.user?.role ?? null;
  } catch {
    return undefined;
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
 * spacing aside. An empty box shows the most used. Never the name the box
 * already says -- typing "Hart" on the way to "Hartung" still offers
 * "Hartung" -- and never more than `limit`, so the list stays short enough to
 * sit above a phone's keyboard. A pick closes the list (NameBox). */
const said = s => s.trim().replace(/\s+/g, " ").toLowerCase();

export function namesMatching(names, typed, limit = 8) {
  const t = said(typed);
  const others = names.filter(n => !t || said(n) !== t);
  const first = others.filter(n => said(n).startsWith(t));
  const then = others.filter(n => !said(n).startsWith(t) && said(n).includes(t));
  return [...first, ...then].slice(0, limit);
}
