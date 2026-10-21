# Sign in with an emailed link

**Status:** design approved in chat on 2026-10-21. The user chose:
- **keep Google** sign-in exactly as it is, and add an emailed link beside it;
- **ask for a name** the first time someone signs in by link, so teammates don't see a bare email address;
- **phones matter**: the members who need this are mostly on phones, so the sign-in must finish in the browser where it started, wherever the link opens;
- for that cross-browser case, **the code shows on the page the link opens**, and Firebase keeps sending the email (no mail service of our own).

A builder check the same day raised four questions. All four are settled below, each marked *(my call)*.

## Context
A couple of club members have no Google account, so they can't sign in today. The only way in is Google, through Firebase's popup (`frontend/site/auth.js` `signIn()`), and every account-only feature sits behind it: My games, My shots, teams and invites, Name teams, I played…, clips, entered scores, switched ends, practice.

Firebase Auth already offers passwordless email-link sign-in (`sendSignInLinkToEmail`, `isSignInWithEmailLink`, `signInWithEmailLink`), in the same gstatic `firebase-auth.js` module the site already loads. Its tokens are ordinary Firebase ID tokens, so `auth.py`'s verifier and everything keyed by uid work as they are.

What a plain emailed link gets wrong is phones. Many mail apps (Outlook, Yahoo) open links in their own built-in browser, which has its own storage. A link opened there signs the member in *inside the mail app*, not in the browser they were using.

## What a member sees
1. **Choose.** Every "Sign in" opens one small dialog: **Continue with Google** · or · **Email** [____] **Email me a link**. That covers the header chip, the "Sign in with Google" cards (Mine, Shots, Join, Thinking, Practice, Users, Playlists) and the viewer prompts (Name teams, I played…, Clip, score "?", switched ends, view-only). The cards' text becomes plain "Sign in" (Join: "Sign in to join").
2. **Wait.** The dialog becomes "Check your email — we sent a link to jo@example.com", with a 6-digit code box ("If the link opens in another app, it shows a code. Type it here"), plus Send again · Use a different address. If the phone reloads the tab while they're in the mail app, the code box comes back from what the browser remembered.
3. **The link opens in the same browser** (a new tab). That tab signs in, asks for the name if needed, and goes to the page they started on. The original tab notices the sign-in through Firebase's shared browser storage, closes its dialog, and carries on with whatever was being done.
4. **The link opens somewhere else** (a mail app's browser, or the phone when they started on a computer). That page shows "Almost there — type this code where you asked to sign in: **482 913**. Works for 10 minutes." Typing it into the waiting dialog signs *that* browser in. A quieter "Sign in on this screen instead" asks for the email and signs in where the link opened, landing on the home page.
5. **Name, once.** After an email-link sign-in, if the account has no name: "Your name — what your teammates see" [____] [Save]. It is saved on the account, and as the Firebase display name. Closing it leaves them signed in; they're asked again on the next email-link sign-in. Google users never see it.
6. **A bad link or code.**
   - Expired or already used: "This link has run out or has already been used", with Sign in.
   - Wrong address on "sign in here instead": "That isn't the address the link was sent to."
   - Five wrong codes: "That code has run out — send a new link."
7. **Google on an address that already signs in by link.** This can only happen with a non-Gmail address; Firebase links Gmail ones itself. Instead of a raw error code: "This address signs in with an emailed link", with the email option filled in.

The same address, either way in, is the same account (Firebase's one-account-per-email setting), so games, teams and role carry over.

## Non-goals
- No passwords offered anywhere, no codes written in the email, no mail service of our own.
- No flow for connecting Google to an account that started by link (step 7 just points them to the link).
- No rename screen beyond the first-time name.

## Anti-goals
- **A link someone didn't ask for must never sign a stranger in.** Anyone can type a member's address and wait for them to tap the email. So the other-browser path needs the code typed where the request started, the link is never relayed automatically, and wrong guesses are capped per request.
- **A mistyped address must never hand a stranger a secret page.** Nothing from the current URL goes into the email: `/join/<token>` is a team invite, and a `/c/<slug>/` link is the edit permission of a chart nobody owns.
- **Nothing changes for Google users beyond one extra click** (the chooser). Every caller of `signIn()` and `ensureSignedIn()` keeps working unchanged.

## Design

### Browser
**`frontend/site/auth.js`** stays the only file that knows Firebase.
- `signIn()` opens the chooser and resolves once signed in by either path. Closing the dialog rejects with the code a shut popup gives (`auth/popup-closed-by-user`). So the 14 callers (Games, Submit, IdentityChip, ViewOnly, Teams, Played, ShotClip, Report, ChartPanel and the rest) are untouched. `ensureSignedIn()` is unchanged. IdentityChip stops printing an error on a plain dismissal.
- `sendLink(email)` makes a random request id and calls `sendSignInLinkToEmail` with `{url: <origin>/signin?r=<id>, handleCodeInApp: true}`. It remembers `{email, id, next, at}` in localStorage, inside try/catch, where `next` is the current path, query and hash. `next` lives only there, never in the link.
- `finishWithLink(email, link)` (`signInWithEmailLink`), `isLink(url)` and `pending()`.
- `setName(name)`: PUT `/api/me/name`, then `updateProfile`. `updateProfile` mutates the same `User` object and fires no event, so auth.js bumps a version in its store, which makes `useAuthUser` (`useSyncExternalStore`) re-render the chip.
- A Google popup that fails with `auth/account-exists-with-different-credential` gets step 7's message.

**New `frontend/site/signInDialog.js`**: plain DOM on a `<dialog>`, with its styles injected once.
- `signIn()` imports it on demand. It therefore behaves the same in the site bundle and the viewer bundle without touching either React tree, and `test_firebase_is_only_ever_imported_on_demand` stays true.
- It holds the chooser, the waiting state with its code box, and the name step, which it exports for `/signin` to reuse.
- The waiting state listens on `onUser`, so a same-browser sign-in in another tab closes it.
- It must fit 390 px wide.
- On a site page load, a pending request under about 15 minutes old, with nobody signed in, reopens the code box.

**New `/signin` page**: `frontend/site/SignIn.jsx`, `service/static/signin.html` (`data-page="signin"`), an entry in `site/main.jsx`'s PAGES, and a route in `api.py` beside the other page shells.
- **Not a sign-in link:** a Sign in button.
- **A link, and this browser remembers the request with the same `r`:** finish the sign-in, drop `oobCode` with `history.replaceState`, run the name step, then `location.replace(next)`. `next` must start with `/` and not with `//` or `/\`; anything else goes to `/`.
- **A link, and no matching request:** POST the relay with `location.href` and show the code, with "Sign in on this screen instead" beneath it. This page never consumes the link on load, so mail scanners that prefetch links are harmless.

### Server (`src/curling_score/service/`)
- **Record and repo.** `SigninRelay` in `records.py`, modelled on `Invite`: id (the request id), code, link, misses, created_at, expires_at. `repo.py` (protocol and memory implementation) and `firestore_repo.py` get, put and delete it by id. Claiming runs in a Firestore transaction: read, compare, then count a miss or delete. The collection is `signin_relays`, with a TTL on `expires_at`.
- **`POST /api/signin/relay`** `{link}` returns `{code, expires_at}`, with `no-store`.
  - 503 when accounts are off.
  - The link must be ours: `public_base_url`'s host, path `/signin`, `mode=signIn`, an `oobCode`, an `r` of the expected shape, `apiKey` equal to `settings.firebase_api_key`, and at most 2 KB.
  - It is idempotent per id: the same link again gets the same live code, and a different link replaces it.
  - The code is `secrets.randbelow(10**6)`, zero-padded, and expires after 10 minutes.
  - Rate-limited by `bump_rate_limit("relay:" + ip_hash(request), …)`.
- **`POST /api/signin/relay/{id}/claim`** `{code}` returns `{link}` once; then the relay is gone.
  - A wrong code counts a miss, and the fifth miss deletes the relay.
  - It is also limited per IP (`"claim:" + ip_hash`).
  - A miss or an expired relay gets 404: "that code isn't right, or has run out".
- **`claims_of`**: a token whose `firebase.sign_in_provider` is `password` and whose `email_verified` is false is anonymous. See the builder check, point 1. `FakeVerifier.add` gains a `provider=` argument that defaults to `google.com`.
- **`PUT /api/me/name`** `{name}`: `require_user`; trim it and collapse spaces; 1–60 characters with no control characters; rate-limited like `/api/me/shots-sharing`; saved with `repo.update_user(name=…)`.
- **`user_for_claims`**: when the stored name is empty and the token carries one (they later used Google), fill it in. Otherwise unchanged.

### Words
- **`static/privacy.html`:**
  - You sign in "with Google, or with a link emailed to you".
  - The account keeps your email address, plus your Google name and picture, or the name you give.
  - A link opened in another browser, with a 6-digit code, can be used for 10 minutes to finish signing you in where you started. It is deleted as soon as the code is used or after 5 wrong tries; any unused are removed automatically, usually within a day after the 10 minutes are up.
  - The address you typed, and the page you were on, sit in your browser's storage until you next sign in.
  - Google (Firebase Authentication) sends the sign-in email.
- **`static/terms.html`:** "sign in, with a Google account or a link emailed to you".
- **`README.md`:** "sign in with Google" becomes "sign in"; the `signInNow` comment in `runtime/auth.mjs` changes to match.

### Deploy
`deploy/README.md`'s accounts block gains:
- In Sign-in method, enable **Email/Password** and its **Email link (passwordless sign-in)** option. Firebase requires both. The site never offers passwords, and the server ignores unverified password accounts.
- Set the project's public-facing name, which appears in the email's subject.
- Keep "one account per email address".
- Optionally, a custom sender domain if the mail lands in junk.
- The `signin_relays` TTL: `gcloud firestore fields ttls update expires_at --collection-group=signin_relays --enable-ttl`.

The project has billing (Cloud Run), so Firebase allows 25,000 sign-in emails a day, not the free plan's 5.

The `claims_of` change ships before the provider is switched on, because the unverified-password gap opens the moment it is on. Switching it on in the real project, and each deploy, waits for the user's go-ahead at the time.

## Builder check
1. **Firebase won't enable email links without Email/Password, so password accounts become possible.** With the public API key, anyone could create an unverified password account under a member's address. Firebase's documentation says that finishing an email-link sign-in removes any earlier unverified sign-in method and invalidates its sessions, so an account set up in advance can't hold on once the real owner uses a link. Until then, the server treats unverified password tokens as anonymous, so such an account is never a user here. *(my call)*
2. **Google and email link on one address.** Firebase trusts Google only for Gmail addresses. Google first, then a link, should leave both working: the Google method is verified, so the link sign-in doesn't remove it. A link first, then Google on a non-Gmail address, fails with `account-exists-with-different-credential`. That case gets step 7's message, not a linking flow. Both orders are checked on the real project with test accounts. *(my call)*
3. **Guessing the code.** A per-IP limit alone isn't enough, because whoever typed the victim's address already knows it, and fresh IPs are cheap. So each relay is keyed by the request id the starting tab made, the claim names that id, and five misses delete it. *(my call)*
4. **Secrets in the emailed link.** Putting `next` in the continue URL would mail out invite tokens and chart edit links. So `next` stays in localStorage. A sign-in finished in another browser lands on `/`, which is fine, because in that case the member carries on in the starting tab. *(my call)*

## Calls I made
- One chooser for everyone, so Google users get one extra click.
- A dedicated `/signin` landing page.
- A 6-digit code that lasts 10 minutes, works once, and allows 5 wrong tries per request, plus a per-IP limit.
- Unverified password accounts are treated as signed out.
- No Google-connecting flow for accounts that started by link; a clear message instead.
- Closing the name step is allowed; they're asked again on the next email-link sign-in.
- Firebase's default sender address to start.

## Testing
- **Python:**
  - New `tests/test_signin_relay.py`: create, idempotent re-post, claim once, delete after five misses, expiry, a foreign or forged link refused, per-IP limits, 503 with accounts off.
  - `test_service_auth.py`: an unverified password token is anonymous and a verified one isn't; PUT name; a name filled in from a later Google token.
  - Relay cases in `test_repo_contract.py`.
  - The page path lists in `test_service_api.py`, and the sign-in text assertions in `test_viewer_js.py`.
- **Browser:** the Firebase Auth emulator, whose links come from `/emulator/v1/projects/<id>/oobCodes`, at 390×844 with touch:
  - The chooser fits.
  - A same-browser link signs in, asks for the name, returns to `next`, and lets the original tab's mid-action prompt carry on.
  - A fresh-profile link shows the code, and typing it into the first tab signs that tab in.
  - Reloading the waiting tab brings the code box back.
  - Expired and reused links, and five wrong codes.
  - The Google path still works.
- **Real project, with the user's go-ahead:**
  - A non-Google address, opened from a phone mail app that has its own browser, finished with the code.
  - On a non-Gmail Google account: Google, then a link, then Google again, ending as one account.
  - On a Gmail address: a link, then Google.
  - Afterwards, delete the test accounts.
