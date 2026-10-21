/* Saying which rocks of a game you threw (api.py, "plays"), read back by the
 * My shots report. Every call needs a signed-in person's token, which the
 * dialog passes in: the viewer does not load Firebase until somebody asks
 * (runtime/auth.mjs). Each answers {ok, status, ...the server's JSON}, with
 * `error` the server's own words when it gave any. */
export const playUrl = sourceId => `/api/me/plays/${encodeURIComponent(sourceId)}`;

/* Where the report reads its games: your own plays, or -- with `player`, a
 * teammate sharing theirs -- that teammate's, read-only. */
export const playsUrl = player => (player
  ? `/api/players/${encodeURIComponent(player)}/plays` : "/api/me/plays");
export const reportUrl = (sourceId, player) => (player
  ? `${playsUrl(player)}/${encodeURIComponent(sourceId)}` : playUrl(sourceId));

async function call(url, init) {
  try {
    const r = await fetch(url, init);
    const data = await r.json().catch(() => ({}));
    if (r.ok) return { ok: true, status: r.status, ...data };
    return { ok: false, status: r.status, error: typeof data.detail === "string" ? data.detail : null };
  } catch {
    return { ok: false, status: 0, error: null };
  }
}

const authed = token => ({ Authorization: `Bearer ${token}` });

/* {ok, play}: this person's play on the game, or play null. */
export const fetchPlay = (sourceId, token) => call(playUrl(sourceId), { headers: authed(token) });

/* {ok, play}. `body` is {path, color, slot, switched_ends?}; `path` is the
 * page's own. `switched_ends`, sent only when the person changed them, is the
 * ends the doubles pair threw the other way round (core playBody). */
export const savePlay = (sourceId, body, token) => call(playUrl(sourceId), {
  method: "PUT",
  headers: { "Content-Type": "application/json", ...authed(token) },
  body: JSON.stringify(body),
});

/* {ok, removed}. */
export const clearPlay = (sourceId, token) => call(playUrl(sourceId), {
  method: "DELETE", headers: authed(token),
});
