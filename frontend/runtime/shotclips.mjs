/* A clip of one shot (api.py "shot clips"): where it stands, and asking for
 * one. Anyone may ask where one stands; asking for one needs a signed-in
 * person's token, which the dialog passes in -- the viewer does not load
 * Firebase until somebody asks (runtime/auth.mjs). Both answer the API's
 * word as {ok: true, state, ...}, or {ok: false, status, error} with `error`
 * the server's own words when it gave any. */
const URL_ = "/api/shot-clips";

async function answer(r) {
  const data = await r.json().catch(() => ({}));
  if (r.ok) return { ok: true, ...data };
  return { ok: false, status: r.status, error: typeof data.detail === "string" ? data.detail : null };
}

export async function fetchShotClip(path, rock) {
  try {
    const q = `path=${encodeURIComponent(path)}&rock=${encodeURIComponent(rock)}`;
    return await answer(await fetch(`${URL_}?${q}`));
  } catch {
    return { ok: false, status: 0, error: null };
  }
}

export async function askShotClip(path, rock, token) {
  try {
    return await answer(await fetch(URL_, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ path, rock }),
    }));
  } catch {
    return { ok: false, status: 0, error: null };
  }
}
