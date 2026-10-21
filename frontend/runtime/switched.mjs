/* A chart's End box saying which ends a doubles team's pair threw the other
 * way round (api.py, "switched-ends"): kept on the game, so every chart and
 * link of it changes. Needs a signed-in person's token, which the caller
 * passes in: the viewer does not load Firebase until somebody asks
 * (runtime/auth.mjs). Answers {ok, status, color, ends}, or
 * {ok: false, status, error} with `error` the server's own words when it gave
 * any. */
export const switchedUrl = sourceId => `/api/games/${encodeURIComponent(sourceId)}/switched-ends`;

/* `body` is {path, color, end, on}: `path` the chart's own page, `end` the
 * end ticked (by end identity), `on` whether it is now switched. */
export async function saveSwitchedEnds(sourceId, body, token) {
  try {
    const r = await fetch(switchedUrl(sourceId), {
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
