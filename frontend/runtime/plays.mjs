/* Saying which rocks of a game you threw (api.py, "plays"), read back by the
 * My shots report. Every call needs a signed-in person's token, which the
 * dialog passes in: the viewer does not load Firebase until somebody asks
 * (runtime/auth.mjs). Each answers {ok, status, ...the server's JSON}, with
 * `error` the server's own words when it gave any. */
const playUrl = sourceId => `/api/me/plays/${encodeURIComponent(sourceId)}`;

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

/* {ok, play}. `body` is {path, color, slot}; `path` is the page's own. */
export const savePlay = (sourceId, body, token) => call(playUrl(sourceId), {
  method: "PUT",
  headers: { "Content-Type": "application/json", ...authed(token) },
  body: JSON.stringify(body),
});

/* {ok, removed}. */
export const clearPlay = (sourceId, token) => call(playUrl(sourceId), {
  method: "DELETE", headers: authed(token),
});
