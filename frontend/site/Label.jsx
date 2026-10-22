/* Members' labelling: box the stones on a still, one picture at a time.
 *
 * What the software gets wrong is shown with a box of the computer's own;
 * the member fixes, deletes or adds boxes, and the answer goes to
 * /api/label/{kind}/{id} (service/labelling.py check_answer says which
 * answers are kept). All of the arithmetic -- which box a click lands on,
 * what a corner may do, the answer's shape -- is core/labelling.mjs, tested in
 * node; this file draws and listens.
 *
 * Two flows share one page, chosen by the URL hash (#boxes, the default, or
 * #releases). Each is a component handed the item and the few things the
 * queue does (submit, skip, back); useLabelQueue holds what is not flow
 * specific: the item in front of the member, one fetched ahead, and the one
 * before for Back. A flow is signed-in only, and needs a computer: boxing a
 * stone to a few pixels is not a thumb's job.
 *
 * Signed in or not, the server is the judge: a 409 means somebody else
 * finished the item first, and a 401 sends the member back to sign-in. */
import { useCallback, useEffect, useRef, useState } from "react";
import { authedFetch, signIn } from "./auth.js";
import { CANT_TELL, MIN_WIDTH_PX, addBox, boxAnswer, boxAt, canFinish, cornerAt,
         drawScale, drawnBox, keepFlagged, moveBox, moveCorner, removeBox, setBox,
         setColour, startBoxes } from "../core/labelling.mjs";
import { signInTrouble } from "./signinState.mjs";
import { Card, Header } from "./ui.jsx";
import { useAuthUser } from "./useAuth.js";

const RESIZE_CURSOR = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize" };
// How close, in screen pixels, a click must be to a corner to take it.
const GRAB_PX = 9;
// A drag shorter than this, in screen pixels, was a click.
const CLICK_PX = 3;

/* The flag is the one thing a member must be told how to read on their first
 * visit, so it is told once per flow, and remembered if the browser lets it. */
const howtoKey = flow => `label.howto.${flow}`;
function seenHowto(flow) {
  try { return localStorage.getItem(howtoKey(flow)) === "1"; } catch { return false; }
}
function rememberHowto(flow) {
  try { localStorage.setItem(howtoKey(flow), "1"); } catch { /* asked again next visit */ }
}

function useWindowSize() {
  const [size, setSize] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));
  useEffect(() => {
    const on = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    addEventListener("resize", on);
    return () => removeEventListener("resize", on);
  }, []);
  return size;
}

function readFlow() {
  return location.hash === "#releases" ? "releases" : "boxes";
}

const post = (url, body) => authedFetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body) });

/* The item in front of the member, the one fetched ahead, and the one they
 * just answered, for Back.
 *
 * The next two are always asked for together: the server hands a member
 * their own live holds first, so with one item in hand "give me one" returns
 * that same item. Asking for two and taking the first that is not in hand is
 * how the one ahead is found. */
function useLabelQueue(kind, active) {
  const q = useRef({ phase: "loading", current: null, next: null, prev: null, count: null,
                     busy: false, notice: "", rev: 0, gen: 0, filling: false });
  const [view, setView] = useState({ phase: "loading", current: null, count: null,
                                     canBack: false, busy: false, notice: "" });

  const publish = useCallback((patch = {}) => {
    const s = q.current;
    Object.assign(s, patch);
    setView({ phase: s.phase, current: s.current, count: s.count, canBack: !!s.prev,
              busy: s.busy, notice: s.notice });
  }, []);

  const ask = useCallback(async () => {
    const res = await authedFetch(`/api/label/${kind}/next?n=2`);
    if (!res.ok) throw new Error(`next ${res.status}`);
    return res.json();
  }, [kind]);

  const topUp = useCallback(async () => {
    const s = q.current, gen = s.gen;
    if (s.next || !s.current || s.filling) return;
    s.filling = true;
    try {
      const data = await ask();
      if (gen !== s.gen) return;
      const fresh = data.items.find(i => i.id !== s.current?.item.id);
      if (fresh && !s.next) {
        s.next = fresh;
        new Image().src = fresh.image_url;   // so it is there when they get to it
      }
    } catch { /* the next item is asked for again when it is wanted */ }
    finally { s.filling = false; }
  }, [ask]);

  /* `keepPrev`: asking again mid-session (nothing was ready ahead), when Back
   * must still have the answer they just gave. */
  const load = useCallback(async (keepPrev = false) => {
    const s = q.current, gen = ++s.gen;
    Object.assign(s, { current: null, next: null, busy: false, filling: false });
    if (!keepPrev) s.prev = null;
    publish({ phase: "loading", notice: "" });
    try {
      const data = await ask();
      if (gen !== s.gen) return;
      const [first, second] = data.items;
      s.next = second || null;
      publish({ phase: first ? "ready" : "empty", count: data.count,
                current: first ? { item: first, restore: null, rev: ++s.rev } : null });
      topUp();
    } catch (err) {
      if (gen === s.gen) publish({ phase: "error", notice: String(err.message || err) });
    }
  }, [ask, publish, topUp]);

  useEffect(() => {
    if (!active) return undefined;
    load();
    const s = q.current;
    return () => { s.gen++; };
  }, [active, load]);

  const advance = useCallback(async patch => {
    const s = q.current;
    if (s.next) {
      const item = s.next;
      s.next = null;
      publish({ ...patch, busy: false, phase: "ready",
                current: { item, restore: null, rev: ++s.rev } });
      topUp();
    } else {
      // Nothing was ready ahead of them: look again, and say so meanwhile.
      Object.assign(s, patch, { current: null });
      await load(true);
      s.notice = patch.notice || "";
      publish();
    }
  }, [load, publish, topUp]);

  /* `restore` is what the picture looked like, so Back can put it back. */
  const submit = useCallback(async (answer, seconds, restore) => {
    const s = q.current;
    if (s.busy || !s.current) return;
    const item = s.current.item;
    publish({ busy: true, notice: "" });
    let res;
    try { res = await post(`/api/label/${kind}/${item.id}`, { answer, seconds }); }
    catch { publish({ busy: false, notice: "Couldn't reach the server. Try again." }); return; }
    if (res.status === 401) {
      publish({ busy: false, notice: "Sign in to go on." });
      signIn().catch(() => {});
      return;
    }
    if (res.status === 409) {
      advance({ notice: "Someone else finished this one." });
      return;
    }
    if (!res.ok) {
      let why = "That didn't save. Try again.";
      if (res.status === 422) {
        try { why = (await res.json()).detail || why; } catch { /* the generic line */ }
      }
      publish({ busy: false, notice: why });
      return;
    }
    const data = await res.json();
    s.prev = { item, restore };
    advance({ count: data.count, notice: "" });
  }, [kind, advance, publish]);

  const skip = useCallback(async () => {
    const s = q.current;
    if (s.busy || !s.current) return;
    publish({ busy: true, notice: "" });
    try {
      const res = await post(`/api/label/${kind}/${s.current.item.id}/skip`);
      if (res.status === 401) {
        publish({ busy: false, notice: "Sign in to go on." });
        signIn().catch(() => {});
        return;
      }
      if (!res.ok) { publish({ busy: false, notice: "Couldn't skip that one. Try again." }); return; }
    } catch { publish({ busy: false, notice: "Couldn't reach the server. Try again." }); return; }
    advance({ notice: "" });
  }, [kind, advance, publish]);

  /* The one before, with the answer given, for one step back. Answering it
   * again replaces that answer on the server; the item left behind is held
   * for them and comes up again next. */
  const back = useCallback(() => {
    const s = q.current;
    if (s.busy || !s.prev) return;
    const { item, restore } = s.prev;
    s.prev = null;
    s.next = s.current?.item || s.next;
    publish({ phase: "ready", notice: "", current: { item, restore, rev: ++s.rev } });
  }, [publish]);

  return { ...view, submit, skip, back, reload: () => load() };
}

/* ------------------------------------------------------------ rock boxes */

function BoxFlow({ item, restore, busy, canBack, paused, onSubmit, onSkip, onBack }) {
  const [s, setS] = useState(() => restore || startBoxes(item));
  const [sel, setSel] = useState(-1);
  const [colour, setColourPref] = useState("red");   // for the next box they draw
  const [hint, setHint] = useState("");
  const [ghost, setGhost] = useState(null);
  const [cursor, setCursor] = useState("crosshair");
  const [broken, setBroken] = useState(false);
  const win = useWindowSize();
  const scale = drawScale(item.w, item.h, win.w - 40, win.h - 260);

  const svgRef = useRef(null);
  const skipRef = useRef(null);
  const t0 = useRef(Date.now());
  const drag = useRef(null);
  const sRef = useRef(s);
  sRef.current = s;

  // Timed from when they can first see it, and not while the how-to is up.
  useEffect(() => { if (!paused) t0.current = Date.now(); }, [paused, item.id]);
  useEffect(() => { if (broken) skipRef.current?.focus(); }, [broken]);

  const point = e => {
    const r = svgRef.current.getBoundingClientRect();
    return { x: (e.clientX - r.left) * item.w / r.width, y: (e.clientY - r.top) * item.h / r.height };
  };

  const finish = useCallback(answer => {
    onSubmit(answer, (Date.now() - t0.current) / 1000, sRef.current);
  }, [onSubmit]);

  const act = useRef({});
  act.current = { s, sel, colour, busy, paused, broken, canBack, onSkip, finish, item };

  useEffect(() => {
    const key = e => {
      const a = act.current;
      if (e.ctrlKey || e.metaKey || e.altKey || a.paused) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (e.key === "Enter" && e.target.tagName === "BUTTON") return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (!["r", "y", "k", "s", "n", "Enter", "Delete", "Backspace"].includes(k)) return;
      e.preventDefault();
      if (a.busy) return;
      setHint("");
      if (k === "r" || k === "y") {
        const c = k === "r" ? "red" : "yellow";
        setColourPref(c);
        if (a.sel >= 0) setS(cur => setColour(cur, a.sel, c));
      } else if (k === "Delete" || k === "Backspace") {
        if (a.sel >= 0) { setS(cur => removeBox(cur, a.sel)); setSel(-1); }
      } else if (k === "k") {
        setS(cur => keepFlagged(cur));
      } else if (k === "n") {
        a.onSkip();
      } else if (a.broken) {
        // Nothing to answer about a picture that is not there.
      } else if (k === "s") {
        a.finish(CANT_TELL);
      } else if (!canFinish(sRef.current)) {
        setHint("Keep (K), fix or delete the dashed box first");
      } else {
        a.finish(boxAnswer(sRef.current));
      }
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);

  // A drag is followed anywhere on the page, so it survives leaving the picture.
  useEffect(() => {
    const move = e => {
      const d = drag.current;
      if (!d) return;
      const p = point(e), { w, h } = act.current.item;
      if (d.mode === "corner") {
        setS(cur => setBox(cur, d.i, moveCorner(cur.boxes[d.i], d.corner, p.x, p.y, w, h)));
      } else if (d.mode === "move") {
        if (!d.moved && Math.hypot(e.clientX - d.cx, e.clientY - d.cy) < CLICK_PX) return;
        d.moved = true;
        setS(cur => setBox(cur, d.i, moveBox(d.box, p.x - d.x, p.y - d.y, w, h)));
      } else {
        setGhost({ x0: Math.min(d.x, p.x), y0: Math.min(d.y, p.y),
                   x1: Math.max(d.x, p.x), y1: Math.max(d.y, p.y) });
      }
    };
    const up = e => {
      const d = drag.current;
      drag.current = null;
      if (!d || d.mode !== "draw") return;
      setGhost(null);
      const p = point(e), { w, h } = act.current.item;
      const box = drawnBox(d.x, d.y, p.x, p.y, w, h, act.current.colour);
      if (box) {
        setSel(sRef.current.boxes.length);
        setS(cur => addBox(cur, box));
      } else {
        setSel(-1);   // a click on empty ice
      }
    };
    addEventListener("mousemove", move);
    addEventListener("mouseup", up);
    return () => { removeEventListener("mousemove", move); removeEventListener("mouseup", up); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const down = e => {
    if (e.button !== 0 || busy) return;
    e.preventDefault();
    setHint("");
    const p = point(e), pad = GRAB_PX / scale;
    const c = cornerAt(s, p.x, p.y, pad);
    if (c) {
      setSel(c.i);
      drag.current = { mode: "corner", i: c.i, corner: c.corner };
      return;
    }
    const i = boxAt(s, p.x, p.y, 0);
    if (i >= 0) {
      setSel(i);
      drag.current = { mode: "move", i, box: s.boxes[i], x: p.x, y: p.y,
                       cx: e.clientX, cy: e.clientY, moved: false };
      return;
    }
    drag.current = { mode: "draw", x: p.x, y: p.y };
  };

  const hover = e => {
    if (drag.current) return;
    const p = point(e), c = cornerAt(s, p.x, p.y, GRAB_PX / scale);
    setCursor(c ? RESIZE_CURSOR[c.corner] : boxAt(s, p.x, p.y, 0) >= 0 ? "move" : "crosshair");
  };

  const flaggedOpen = s.flagged >= 0 && s.starts[s.flagged] === "untouched";
  const w = item.w * scale, h = item.h * scale;

  return (
    <div className="label-flow" data-item={item.id}>
      {broken ? (
        <div className="warn" role="alert">This image didn't load. Skip it (N) and try the next.</div>
      ) : (
        <div className="label-stage" style={{ width: w, height: h }}>
          <img src={item.image_url} width={w} height={h} alt="Stones on the ice, to be boxed"
               draggable={false} onError={() => setBroken(true)}
               onLoad={() => { t0.current = Date.now(); }} />
          <svg ref={svgRef} width={w} height={h} viewBox={`0 0 ${item.w} ${item.h}`}
               style={{ cursor }} onMouseDown={down} onMouseMove={hover}>
            {s.boxes.map((b, i) => {
              const dashed = flaggedOpen && b.start === s.flagged;
              return (
                <g key={i}>
                  {i === sel ? <rect x={b.x0} y={b.y0} width={b.x1 - b.x0} height={b.y1 - b.y0}
                                     className="label-halo" /> : null}
                  <rect x={b.x0} y={b.y0} width={b.x1 - b.x0} height={b.y1 - b.y0}
                        className={`label-box ${b.color}${dashed ? " dashed" : ""}`} />
                </g>
              );
            })}
            {ghost ? <rect x={ghost.x0} y={ghost.y0} width={ghost.x1 - ghost.x0}
                           height={ghost.y1 - ghost.y0} className={`label-box ${colour} dashed`} /> : null}
          </svg>
          {s.boxes.map((b, i) => {
            const dashed = flaggedOpen && b.start === s.flagged;
            const top = b.y0 * scale;
            return (
              <span key={i} className={`label-tag ${b.color}${i === sel ? " sel" : ""}`}
                    style={{ left: b.x0 * scale, top: top < 16 ? top + 2 : top - 16 }}>
                {b.color}{dashed ? " ?" : ""}
              </span>
            );
          })}
        </div>
      )}
      <p className="label-msg" role="status" aria-live="polite">{hint}</p>
      <div className="row label-actions">
        <button className="primary" disabled={busy || broken}
                onClick={() => (canFinish(s) ? finish(boxAnswer(s))
                                             : setHint("Keep (K), fix or delete the dashed box first"))}>
          Done <kbd>Enter</kbd></button>
        <button disabled={busy || !flaggedOpen} onClick={() => setS(keepFlagged)}>
          Keep dashed box <kbd>K</kbd></button>
        <button disabled={busy || sel < 0} onClick={() => { setS(cur => removeBox(cur, sel)); setSel(-1); }}>
          Delete <kbd>Del</kbd></button>
        <button disabled={busy || broken} onClick={() => finish(CANT_TELL)}>Can't tell <kbd>S</kbd></button>
        <button ref={skipRef} disabled={busy} onClick={onSkip}>Skip <kbd>N</kbd></button>
        <button disabled={busy || !canBack} onClick={onBack}>Back</button>
      </div>
      <p className="muted label-keys">
        Drag a corner to resize, drag inside a box to move it, drag on empty ice to draw one.
        <kbd>R</kbd> red, <kbd>Y</kbd> yellow for the selected box (or the next one you draw).
      </p>
    </div>
  );
}

/* ------------------------------------------------------------- the page */

const FLOWS = {
  boxes: { kind: "box", name: "Rock boxes", Flow: BoxFlow,
           howto: "Box every stone you can see, even part-hidden ones. People, brooms and shoes "
                  + "get no box. The dashed box is what the computer wasn't sure about." },
  releases: { kind: "release", name: "Release marks", Flow: null, howto: "" },
};

function Stage({ flow }) {
  const { kind, Flow, howto } = FLOWS[flow];
  const q = useLabelQueue(kind, true);
  const [showHowto, setShowHowto] = useState(() => !seenHowto(flow));
  const dismiss = () => { rememberHowto(flow); setShowHowto(false); };

  return (
    <>
      <div className="label-bar">
        <b>{FLOWS[flow].name}</b>
        {q.count != null ? <span>You've labelled {q.count}</span> : null}
        <span style={{ flex: 1 }} />
        <a href={flow === "boxes" ? "#releases" : "#boxes"}>
          {flow === "boxes" ? FLOWS.releases.name : FLOWS.boxes.name}</a>
      </div>
      {showHowto ? (
        <div className="card label-howto">
          <p style={{ marginTop: 0 }}>{howto}</p>
          <button className="primary" autoFocus onClick={dismiss}>Got it</button>
        </div>
      ) : null}
      {q.notice ? <div className="warn" role="status">{q.notice}</div> : null}
      {q.phase === "loading" ? <p className="muted">Loading…</p>
       : q.phase === "error" ? (
        <div className="warn">Couldn't load the next one.{" "}
          <button onClick={q.reload}>Try again</button></div>)
       : q.phase === "empty" || !q.current ? (
        <p className="muted">Nothing left to label right now. Thank you, and come back soon.</p>)
       : (
        <Flow key={`${q.current.item.id}/${q.current.rev}`} item={q.current.item}
              restore={q.current.restore} busy={q.busy} canBack={q.canBack}
              paused={showHowto} onSubmit={q.submit} onSkip={q.skip} onBack={q.back} />
      )}
    </>
  );
}

export function Label() {
  const { user, ready, accounts } = useAuthUser();
  const win = useWindowSize();
  const [flow, setFlow] = useState(readFlow);
  const [err, setErr] = useState("");
  useEffect(() => {
    const on = () => setFlow(readFlow());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);

  const signedOut = ready && accounts && !user;
  const links = [["/", "All games"], ["/label", "Label", true]];

  return (
    <>
      <Header links={links} />
      <main className="label-main">
        {signedOut ? (
          <Card>
            <p style={{ marginTop: 0 }}>Labelling is for signed-in members.</p>
            <button className="primary"
                    onClick={() => signIn().catch(e => setErr(signInTrouble(e)))}>Sign in</button>
            {err ? <p className="muted">{err}</p> : null}
          </Card>
        ) : !ready ? <p className="muted">Loading…</p>
         : win.w < MIN_WIDTH_PX ? (
          <Card>Labelling needs a computer: a mouse and a keyboard.</Card>)
         : FLOWS[flow].Flow ? <Stage key={flow} flow={flow} />
         : (
          <>
            <div className="label-bar"><b>{FLOWS[flow].name}</b><span style={{ flex: 1 }} />
              <a href="#boxes">{FLOWS.boxes.name}</a></div>
            <p className="muted">Release marks are not ready yet.</p>
          </>
        )}
      </main>
    </>
  );
}
