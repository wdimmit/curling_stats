# Flag an issue

**Status:** built on branch `flag-an-issue` (2026-09-25), from
`docs/superpowers/plans/2026-09-25-flag-an-issue.md`. Checked in headless
Chrome on `/c/`, `/s/` and `/g/` at desktop and phone widths. The design was
approved in chat with one change at review: no contact field. A flag carries
an account only when the person is signed in.

## Context

The pipeline gets things wrong in ways only a person watching can see: a house
read into the clearing, ghosts on the wrong stones, a rock thrown by the wrong
player. Today someone has to describe the place in a message ("s_0kdo…, end 4,
the last rock"), and the owner has to find it again.

**Success:** anyone looking at a chart presses one button, types what is wrong
and sends it. The owner later lists open flags from a terminal. Each flag
carries the game, end, rock and note, with a link that opens that rock and
enough identity to see exactly what the reporter saw, even after a reprocess.

## Scope

**In scope**
- A **⚑ Flag** button and dialog in the viewer, on edit (`/c/`), view-only
  (`/s/`) and review (`/g/`) links, on desktop and phone.
- A `flags` collection, one route to create a flag, and two admin routes to
  list and resolve flags.
- `scripts/flags.py` to list and resolve flags from a terminal.

**Unchanged**
- Grading, overrides and their save path. A flag is never an override and
  never reaches `export.json` or training labels.
- The shot's own `note` field (the grading note). It is not reused.
- Access control. The link is still the permission. A flag grants nothing.
- The local `curling-score serve`. It hides the button, having no server to
  send to.

**Naming.** "Report" is already the printable stats page (`#reportBtn`,
`Report.jsx`, `ui.reporting`). This feature is "flag" throughout: `flagBtn`,
`Flag.jsx`, `flags`.

## 1. What a flag stores (approved)

One `Flag` record per flag, in a new `flags` collection:

| Field | Meaning |
|---|---|
| `id` | `f_` plus a random slug (`slug.py`) |
| `created_at`, `resolved_at` | UTC datetimes; `resolved_at` is null while open |
| `status` | `"open"` or `"resolved"` |
| `note` | The reporter's text, trimmed, 1–2,000 characters |
| `where` | Worked out by the server from the link, never taken from the browser: `link` (`"c"`, `"s"` or `"g"`), `chart_id` and `share_slug` (null on `/g/`), `source_id`, `run_id`, `video_id`, `processing_version`, `title` |
| `place` | As the browser shows it: `game_index`, `end` and `rock` (display numbers; `rock` null for an end with no rocks), `end_id` and `rock_id` (the stable identities, `e.id ?? e.number` and `s.id ?? s.number`), `key` (the override key `g.e.s`), `t_video_s` (the rock's video time), `label` (e.g. "red, skip") |
| `overrides_version` | The chart's overrides version when flagged (null on `/g/`), so a charter's later edits can be told apart |
| `user` | `{uid, email}` when the request carries a valid sign-in, else null |
| `ip_hash` | The existing salted hash (`ip_hash(request)`), for abuse triage only |

- `run_id` plus `key` pins exactly what was on screen. Runs are immutable and
  a chart stays on its run. `source_id` plus `t_video_s` finds the same rock in
  a later run, where end and rock numbers may differ.
- `where` comes from the server because the browser does not know its run or
  source on `/c/` and `/s/`, and because `window.CHART.slug` is not evidence of
  anything (see Risks).
- Flags are in `export_all` and `import_all`, so the nightly backup and
  `restore` carry them. `resolved_at` joins `restore._TIME_FIELDS`.

## 2. The button and the dialog (approved)

**The button.** `<button id="flagBtn">⚑ Flag</button>` in the header menu, next
to `#reportBtn`:
- Desktop: the header row (`#menu` is `display: contents` there).
- Phone: the `⋯` menu, which exists in every mode.
- Shown on `/c/`, `/s/` and `/g/`, unlike `#reportBtn`, which review mode
  hides. Hidden when `config.hosted` is false.

**The place is taken when the dialog opens.** On review links the video plays
and follows, and the cursor would move under the reporter. Opening the dialog
snapshots the current game, end and rock into `ui.flagging`, and that snapshot
is what is sent.

**The dialog** (`Flag.jsx`, a native `<dialog>` opened with `showModal()`, which
brings focus handling, Escape and a backdrop):
- Title "Flag an issue".
- The place, e.g. "Game 1 · End 4 · Rock 16 (red, skip)". "End 4" alone when
  the end has no rocks.
- A textarea, required, `maxlength` 2,000, focused on open.
- One line saying who the flag is from:
  - "From *email*" when signed in;
  - "Sent without your name. Sign in on the site to attach your account."
    otherwise.
- **Send**, disabled while the note is blank or a send is in flight, and
  **Cancel**.
- On success: "Thanks, flagged." in place of the form, closing after 1.5 s.
- On failure: the server's `detail` (or "Could not send. Try again.") inline,
  with the text kept.

The global keyboard handler already ignores keys typed in a textarea, so
typing a note cannot step rocks or toggle panels.

**Sign-in, without Firebase in the viewer.** The viewer deliberately does not
load Firebase (`frontend/site/auth.js`, lines 1–12). The dialog loads it only
when it opens: `await import("../site/auth.js")`, then `whenReady()` and
`currentUser()`. The SDK itself stays on the gstatic CDN, as on the site pages.
The Firebase session is per origin, so someone signed in on the site is signed
in here. When they are, the request carries `Authorization: Bearer <ID token>`.

It fails soft:
- If the config says accounts are off, the CDN cannot be reached or a token
  will not refresh, the flag goes anonymous.
- Send never waits more than 3 s for sign-in to settle.

## 3. API and retrieval (approved)

**`POST /api/flags`** takes a JSON body `{path, place, note}`:
- `path` is the page's own `location.pathname` (`/c/<key>/`, `/s/<key>/` or
  `/g/<source_id>/`). The server parses it (`^/(c|s|g)/([A-Za-z0-9_-]+)/?$`)
  and resolves it with the existing `lookup(kind, key)` (which follows
  `superseded_by`) or `lookup_source(sid)`.
- Why one route and not `/c/{key}/flag` and friends:
  - review links keep their rule of no writes under `/g/` (`api.py:1213`,
    `tests/test_review_mode.py`);
  - there is one route to rate-limit.
- Checks, in the existing hand-written style:

| Condition | Response |
|---|---|
| Body over 8 KB | 413 |
| Not a JSON object, or `path` does not parse | 400 |
| Link does not resolve | 404 |
| `note` blank after trimming, or over 2,000 characters | 422 |
| `place` not an object, or a field of the wrong type | 422 (ints or null for numbers; `key` ≤ 40 characters, `label` ≤ 80, `t_video_s` a number or null) |
| More than 20 flags per IP in an hour, or 100 in a day | 429 (`repo.bump_rate_limit("flag:" + ip_hash, …)`) |
| Otherwise | 201 `{"id": "f_…"}` |

- The user comes from `current_user_or_none(authorization)`, which never
  raises. A bad token is simply anonymous.

**`GET /api/admin/flags?status=open|resolved|all&limit=200`**, behind
`require_admin`, returns `{"flags": [...]}` newest first. `status` defaults to
`open`. Filtering is on `status` equality with sorting in Python, as
`charts_for_owner` does, so no new Firestore index is needed.

**`POST /api/admin/flags/{id}/resolve`**, behind `require_admin`, sets `status`
and `resolved_at` and returns the flag, or 404.

**`scripts/flags.py`**:
- `list [--status open|resolved|all] [--json]`.
- `resolve ID...`.
- It reads `ADMIN_TOKEN` and `BASE_URL` (default `https://curling.dimmit.net`)
  from the environment.
- Each flag prints:
  - when (local time), id and status;
  - who (email, or "anonymous");
  - the title;
  - "Game · End · Rock (label)";
  - the note;
  - two links:
    - the page it was flagged on with its rock hash (`/c/<chart>/`,
      `/s/<share>/` or `/g/<source>/`, plus `#e=…&s=…`);
    - YouTube at `t_video_s`.
- For flags from an older run it also prints `run_id` and `key`, since display
  numbers can move.

## 4. How it is built (approved)

- `service/records.py`: `Flag` dataclass.
- `service/repo.py`:
  - `Repo` gains `put_flag`, `list_flags(status, limit)` and
    `resolve_flag(id, now)`;
  - `MemoryRepo` implements them, plus `export_all` and `import_all`.
- `service/firestore_repo.py`: the `flags` collection, the same three methods,
  and export and import.
- `service/restore.py`: `resolved_at` in `_TIME_FIELDS`.
- `service/api.py`: the three routes, and `MAX_FLAG_BYTES`, `FLAG_HOUR_LIMIT`
  and `FLAG_DAY_LIMIT` beside the existing limits.
- `frontend/core/flag.mjs` (pure, node-tested), re-exported from `index.mjs`:
  - `flagPlace(view, ei, si)` returns the `place` object and its display text;
  - `noteProblem(note)` returns why a note cannot be sent, or null.
- `frontend/runtime/flag.mjs`:
  - `whoIsFlagging()` does the lazy sign-in and resolves to `{email, token}` or
    null within 3 s;
  - `sendFlag(body, token)` does the POST.
- `frontend/viewer/Flag.jsx`: the dialog. `App.jsx` adds the button and
  `ui.flagging`. `style.css` adds the button's order in the header and menu,
  and the dialog's layout for phone and desktop.
- `scripts/flags.py`.
- `npm run build`, committing `viewer/app.js` and the build stamp.

## 5. Testing (approved)

**Python**
- `tests/test_service_api.py`, a new `TestFlags` on the `world` fixture:
  - a flag from each of `/c/`, `/s/` and `/g/` stores the right `where` (run,
    source, chart);
  - a superseded chart resolves;
  - unknown links give 404;
  - bad bodies give 400, 413 and 422;
  - the rate limit gives 429;
  - the admin list and resolve work, and refuse without the token;
  - a flag is in the backup export.
- `tests/test_service_auth.py`: with `FakeVerifier`, a signed-in flag carries
  `{uid, email}`, and a bad token is anonymous, not an error.
- `tests/test_repo_contract.py`: `put_flag`, `list_flags` and `resolve_flag`
  on both repositories, with `flags` in the cleanup list.
- `tests/test_review_mode.py`: POST under `/g/` is still 405. The flag button
  is in the built bundle.

**Node** (`tests/test_viewer_js.py`)
- `flagPlace` on a normal rock, an end with no rocks, a renumbered shot (the
  identity differs from the display number) and a trimmed end.
- `noteProblem` on blank, whitespace-only, 2,000 and 2,001 characters.

**Source and CSS assertions**
- `#flagBtn` sits in `#menu` next to `#reportBtn`, is not hidden in review mode,
  and is gated on `config.hosted`.
- The dialog snapshots the place on open rather than reading the live cursor.

**Headless Chrome** (`scripts/devserve.py` plus `scripts/cdp.mjs`)
- On desktop at 1440 × 900 and phone at 390 × 844, on `/c/`, `/s/` and `/g/`:
  - open the dialog and check the place line;
  - send, and read the flag back from `/api/admin/flags`;
  - on `/g/`, confirm the place does not change while the video plays.

## Risks

- **A view-only link reveals the edit key.** Found while designing this and
  separate from it:
  - `/s/<share>/` boots with `window.CHART.slug = chart.id` (`api.py:1092`);
  - its `timeline.json` carries `chart.slug = chart.id` (`api.py:360`);
  - so anyone holding a view link can open `/c/<id>/` and edit.

  This design never trusts the browser's slug, so it neither depends on nor
  worsens the leak. The fix is its own change.
- **Spam.** Anyone can flag. The rate limit, the 2,000-character cap and
  admin-only reading bound the harm. There is no captcha. If abuse shows up,
  `ip_hash` lets it be found and the limit tightened.
- **Loading `auth.js` lazily in an iife bundle.** esbuild inlines a dynamic
  import of a local module but defers its evaluation, and `auth.js` imports the
  SDK by a runtime URL. So the viewer should load no Firebase until the dialog
  opens. The plan checks this in the built `app.js` first. If it does not hold,
  the fallback is a 30-line copy of the sign-in check in `runtime/flag.mjs`.
- **Privacy.** A signed-in flag stores the account's email. Flags are readable
  only with the admin token, and Firestore rules stay deny-all.

## Out of scope

- A contact field for anonymous reporters.
- Notifying the owner when a flag arrives.
- An admin web page.
- Screenshots or clips attached to a flag.
- Reporters seeing, editing or deleting their flags.
- Showing flags in the viewer.
