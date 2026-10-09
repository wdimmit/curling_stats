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
