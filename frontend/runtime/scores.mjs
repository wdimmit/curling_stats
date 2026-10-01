/* Sending an end's score, entered by hand, to the game it belongs to. The
 * server keeps it on the game, so every chart of it shows it. */
export async function sendScore(sourceId, body, token) {
  try {
    const r = await fetch(`/api/games/${encodeURIComponent(sourceId)}/scores`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    return r.ok ? { ok: true, entered: data.entered_scores } : { ok: false, status: r.status };
  } catch {
    return { ok: false, status: 0 };
  }
}
