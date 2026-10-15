# Charts only their maker can change

**Status:** design approved in chat on 2026-10-15. The user chose:
- **sign in to chart:** with accounts on, every new chart needs a signed-in person, so every new chart has an owner. Old unowned charts stay editable by link until someone claims them;
- **a sign-in token on each save**, not a session cookie or a per-chart edit pass;
- the three design sections below, as presented.

It follows the user roles change (main 309b073a), which stopped anyone signed in from renaming a game's teams.

## Context
Today the chart link is the permission. `api.py`'s module docstring says so: holding `/c/{slug}/` *is* the right to edit, signed in or not, and the claim route repeats it ("Holding the chart id is the whole permission"). Accounts are additive: the README says an account "changes what you can _find_, never what a URL lets you do". A test pins it: `test_a_teams_chart_is_still_editable_by_a_stranger_with_the_link`.

The charting page was kept free of sign-in on purpose. `site/auth.js` is "deliberately not loaded by the charting viewer", and `overridesStore.mjs` saves with plain `fetch` and an unload `sendBeacon`.

What exists to build on:
- **`Chart` already records its maker:** `owner_user_id` (set when a signed-in person makes it) and `team_id` (set when made as a team). Charts made signed-out have neither.
- **The only chart write is `POST /c/{slug}/overrides.json`.** It takes a whole document (local `curling-score serve`) or a merge, and is registered for both `/c/` and `/s/` by `register_chart_routes`. `/s/` already answers 403.
- **`lookup(kind, key)`** resolves a superseded chart to the one that replaced it (`live_chart`), so a loser link lands on its team's chart.
- **The viewer decides its mode once, before the first render** (`viewer/main.jsx`: `modeOf(window.CHART)`, then fetch the timeline and overrides, then render). In view mode the page uses the Watch layout that `/s/` links get.
- **The viewer can already ask who is signed in on demand:** `runtime/auth.mjs` `whoIsSignedIn()` / `accountsOn()` / `signInNow()`, 3 s cap. Flag, I played… and Name teams use it.
- **A browser beacon (`sendBeacon`) cannot carry an `Authorization` header.** `fetch(…, {keepalive: true})` can. Both cap the body at 64 KB.

## The rule
One function on the server decides who may save a chart's grading, `may_edit_chart(chart, uid)`:

| Chart | Who may save |
|---|---|
| Team chart (`team_id` set) | Current members of that team (`repo.team_members`). The person who made it loses edit rights if they leave the team. |
| Personal chart (`owner_user_id` set, no team) | Only `owner_user_id`. |
| Unowned (neither set) | Anyone holding the `/c/` link, as today. |
| Any chart, with accounts off (`auth.enabled` false) | Anyone holding the `/c/` link, as today. |

- **Reading doesn't change.** `/c/` link holders still read everything: `timeline.json`, `overrides.json` (and its 15 s poll), `overrides_meta.json`, `export.json`, `status.json`. `/s/` stays view-only and `/g/` stays public.
- **Admins get no special edit rights.**
- **Claiming stays as it is** (`POST /api/me/charts/{id}/claim`). Anyone holding the link may claim an unowned chart, which makes them its owner, so the chart now locks to them. Only the owner may move a personal chart onto a team.

## Design

### Server (`service/api.py`)
- **`uid_of(authorization) -> str | None`:** the verified token's `sub`, or None.
  - It does not read or create the `User` record. A save happens every 800 ms while someone charts, and the rule needs only the uid.
  - It shares `current_user_or_none`'s token parsing.
- **`may_edit_chart(chart, uid) -> bool`:** the table above.
  - Team charts cost one extra read (`team_members`). Personal and unowned charts cost none.
- **`POST {prefix}/overrides.json`:**
  - After the existing `/s/` 403, when `may_edit_chart` is false: answer 401 "sign in to change this chart" when there is no uid, and 403 "only the person or team who made this chart can change it" otherwise.
  - Refused before the body is read, so a refused save writes nothing.
- **`GET {prefix}/access.json`** (new, for both `/c/` and `/s/`). It answers `{"edit": bool, "reason": ..., "team": {"name"} | null}`, where reason is one of:
  - `owner` / `team`: may edit;
  - `open`: may edit; an unowned chart, or accounts off;
  - `signin`: no uid on a chart that needs one;
  - `not-yours`: signed in, but not the owner or a member;
  - `view-only-link`: any `/s/` link.

  `team` carries the team's name when the chart is a team's, so the page can say whose it is. It never names the person who owns a personal chart. Cache-Control is `private, no-store`.
- **`chart_page`:** `window.CHART` gains `"guarded": true` for a `/c/` page whose chart has an owner or a team, when accounts are on. An unguarded page boots exactly as today.
- **`POST /api/charts` and `POST /api/submissions`:** with accounts on (`auth.enabled`), a missing or invalid token answers 401 "sign in to chart". With accounts off, both stay anonymous, as the README promises for `FIREBASE_PROJECT` unset.
- **Docstrings that state the old rule are rewritten:** the module docstring, the claim route's, and the account-routes comment ("They gate nothing").

### Viewer (`frontend/`)
- **`core/wire.mjs` `modeOf`** passes `guarded` through. A new pure function, `accessMode(config, access)`, turns an access answer into the config the page renders with:
  - `readOnly` is `!access.edit`;
  - `viewOnly` is `{reason, team}` when read-only because of the rule, and null otherwise.
  - With no answer (an offline check, or sign-in slow to reply), the result is read-only with reason `unchecked`.
- **`viewer/main.jsx`:** when `config.guarded`, a third fetch joins the existing `Promise.all`:
  1. `whoIsSignedIn()` (it loads the sign-in SDK; 3 s cap);
  2. `access.json` with the token, or without one when nobody is signed in.

  The page renders once, with `accessMode(config, answer)`. The checks run beside the 350–500 KB timeline download, so the wait they add is usually small, and edit controls never appear and then vanish.
- **View-only line.** When `config.viewOnly` is set, the header shows a `#viewOnly` pill where the save status (`#save`, hidden in view mode) would be:
  - `signin`: "View only — sign in to edit if this chart is yours or your team's", plus a **Sign in** button (`signInNow()`, then `location.reload()`);
  - `not-yours`: "View only — only the person or team who made this chart can change it". For a team chart it names the team ("only team Thistles…");
  - `unchecked`: "View only — couldn't check sign-in. Reload to try again."
- **`runtime/overridesStore.mjs`:**
  - **Token cache.** On a guarded edit page the store keeps a cached token from `whoIsSignedIn()`. It refreshes it before every save, every 5 minutes, and when the tab becomes visible again. Firebase renews a token in the last 5 minutes of its hour, so a 5-minute tick keeps the cache valid.
  - **Saves** send `Authorization: Bearer <token>` whenever there is a token. Unguarded pages send none, as today.
  - **Close-of-tab save.** The `pagehide` save becomes `fetch(url, {method: "POST", keepalive: true, headers, body})` with the cached token, replacing `sendBeacon` everywhere. It still sends only the unsaved shots.
  - **A refused save (401/403):**
    1. Force one token refresh (`getIdToken(true)`) and retry once.
    2. If that is refused too, set the status to "not saved — sign in again" and keep the keys dirty.
    3. Stop the 5-second retry loop, which cannot fix a refusal. ⬇ download stays as the escape hatch.

    Every other failure keeps today's retry.
- **`site/auth.js` and `overridesStore.mjs` header comments** are rewritten. The charting page loads sign-in only on guarded `/c/` pages. `/s/`, `/g/`, unowned charts and the local `curling-score serve` never load it.

### Making charts (site)
- **`site/Games.jsx` `Actions.chart()`:** with accounts on and nobody signed in, it calls `signIn()` first, waits for the signed-in user, then carries on to `/api/charts` or `/api/submissions`. Closing the popup does nothing.
- **The game list's intro (`chartnote`):** "Chart gets you your own charting link, private to whoever has it…" becomes "**Chart** starts your own chart of the game. Only you, or your team, can change it; send the link and others can watch."
- **`site/Submit.jsx`:** signed out with accounts on, the button reads "Sign in to get your link". It signs in, then submits.

### Terms, privacy, README
- **`terms.html` "Accounts":** "You don't need an account to watch games or to edit a chart you have a link to" becomes "You don't need an account to watch games or to look at a chart someone sends you". Charting and the existing list (naming teams, scores, I played…, clips, teams) need sign-in, and only the person or team who made a chart can change it. Bump "Last updated".
- **`privacy.html`:** nothing new is kept ("Your charts and teams" already lists charts and edits), but two sentences stop being true:
  - "A chart's link opens that chart for whoever has it." becomes "A chart's link shows that chart to whoever has it. Only the person or team who made it can change it."
  - Under "What is kept if you don't sign in", "Charts you make, edits made through a chart's link, and flags are kept without a name" becomes "Flags, and edits made through the link of an older chart nobody owns, are kept without a name". The IP-code sentence after it says "charts created and flags sent".
  - Bump "Last updated".
- **README "Accounts" paragraph:** rewritten to the new rule; it keeps the accounts-off promise.

## Rollout
- **No data migration.** Charts already carry `owner_user_id` / `team_id`, so owned charts lock as soon as the API deploys. The deploy is API only (the viewer is served by the API); the workers don't change.
- **Shared personal links lose editing.** A personal chart whose edit link the owner shared, so others could grade too, becomes view-only for everyone but the owner. Those people need a team on the site, and the owner moves the chart onto it ("Move to <team>" on My games). `overrides_meta` records when a shot changed, not by whom, so how many charts this affects can't be measured.
- **Tabs open across the deploy** run the old page code. Their next save to an owned chart is refused, and they show "not saved — use ⬇" until reloaded. Deploy outside league hours: the watched leagues play most evenings and Sunday, so a weekday afternoon is quietest.

## Testing
- **API (`tests/test_service_auth.py`, FakeVerifier world):**
  - The save matrix on `overrides.json`, merge and whole-document: owner, teammate, a teammate after leaving, stranger, signed out, unowned chart, unowned then claimed, and a `/s/` link.
  - Each `access.json` reason, and that `team.name` appears only for team charts.
  - `guarded` in `window.CHART` only for owned or team `/c/` pages.
  - `/api/charts` and `/api/submissions` give 401 signed-out with accounts on, and stay anonymous with accounts off (`no_accounts` world).
  - A refused save writes nothing.
  - Existing tests that chart anonymously in the accounts-on world are updated. `test_a_teams_chart_is_still_editable_by_a_stranger_with_the_link` becomes its opposite.
- **Accounts-off world (`tests/test_service_api.py`, NoAuth):** unchanged and still green. This is the "exactly as before" guarantee.
- **Viewer (`tests/test_viewer_js.py`, node):**
  - `accessMode` for every reason, and for no answer;
  - a save carries the token and an unguarded one doesn't;
  - the `pagehide` save is a keepalive fetch with the header;
  - a 401 leads to one forced refresh and then stops retrying, with the keys still dirty;
  - `modeOf` passes `guarded` through.
- **Firestore:** no repo change is planned. If one turns up, run `test_repo_contract.py` against the emulator.
- **Browser:** the app run in memory with `FakeVerifier` accounts, in headless Chrome. CDP `Fetch` interception serves a fake Firebase SDK from the `www.gstatic.com` URLs, so each fake account can "sign in" (the method used to check the roles change). An owner edits and a save lands with the token; navigating away delivers the close-of-tab save; a teammate edits a team chart; a stranger and a signed-out visitor each get view-only with the right line; and the line fits 390 px with no sideways scroll.

## Out of scope
- An admin override to edit any chart.
- Recording who made each edit (`overrides_meta` "by").
- Asking a chart's owner for edit access from the page.
- Claiming an unowned chart from the viewer itself. Claiming stays on My games.
