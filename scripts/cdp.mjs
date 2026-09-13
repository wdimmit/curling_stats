/* A very small Chrome DevTools Protocol client, over node's built-in WebSocket.
 *
 * Used by scripts/domsnap.mjs, and by any check that has to assert on what the
 * page actually does rather than on what the source says it does. This project
 * has twice shipped a viewer bug that reading the code did not find and one
 * browser click did: handlers that were never attached, and a hollow SVG bar
 * whose `fill:none` left only its outline able to receive the event.
 *
 * Start Chrome first:
 *   google-chrome --headless=new --disable-gpu --no-sandbox \
 *     --remote-debugging-port=9222 --user-data-dir=$(mktemp -d) about:blank
 */
import http from "node:http";

const PORT = Number(process.env.CDP_PORT || 9222);

const get = path => new Promise((res, rej) =>
  http.get({ host: "127.0.0.1", port: PORT, path },
    r => { let d = ""; r.on("data", c => (d += c)); r.on("end", () => res(d)); })
    .on("error", rej));

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", ev => {
      const m = JSON.parse(ev.data);
      if (!m.id || !this.pending.has(m.id)) return;
      const { resolve, reject } = this.pending.get(m.id);
      this.pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    });
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  /* Evaluates in page scope and awaits promises, so `fetch(...).then(r =>
   * r.json())` works as an expression. Throws with the page's own stack. */
  async eval(expression) {
    const r = await this.send("Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails)
      throw new Error(r.exceptionDetails.exception?.description
        ?? JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
}

export async function attach(urlSubstring = "", tries = 100) {
  for (let i = 0; i < tries; i++) {
    try {
      const page = JSON.parse(await get("/json/list"))
        .find(t => t.type === "page" && t.url.includes(urlSubstring));
      if (page) {
        const ws = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((res, rej) => {
          ws.addEventListener("open", res);
          ws.addEventListener("error", rej);
        });
        const cdp = new CDP(ws);
        await cdp.send("Runtime.enable");
        await cdp.send("Page.enable");
        return cdp;
      }
    } catch { /* chrome is not up yet */ }
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`no page target matching "${urlSubstring}" on port ${PORT}`);
}
