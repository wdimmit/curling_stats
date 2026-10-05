# "I played…" position tags and the private "My shots" report

**Status:** design approved in plan mode on 2026-10-04 (user picks: fours tag =
throwing order; report = tables + miss chart + overlaid delivery paths; tag
from the viewer menu). Built on branch `my-shots`.

## Context
Players want to see their own delivery tendencies across many games. Today a chart has no player identity: it knows each rock's colour and throwing slot, never who threw it.

This feature lets a signed-in user tag one game at a time with **team colour + position**. A private page then shows every rock they threw across all their tagged games. The rocks are grouped by **shot type** (Hit / Draw / Guard, plus Other) × **turn** (Clockwise / Counter-clockwise, plus "turn not measured").

The data already exists per rock:
- **Shot type:** the automatic `shot_type`, refined by the charter's overrides and mapped to groups by `groupOf` in `core/report.mjs:120`.
- **Turn:** `turnOf` in `core/line.mjs:217`. Curl right = clockwise.
- **Delivery figures:** the six from `lineFigures` in `core/line.mjs:281`.
- **Delivery path:** `line.delivery`, from schema 8.

Decisions the user made:
- **Fours tag = throwing order.** One pick: Lead (team's 1st & 2nd rocks), Second (3rd & 4th), Third (5th & 6th), Skip (7th & 8th). A skip who throws third picks Third.
- **Doubles:** player A / player B. Per-end role swaps are honoured through the chart's overrides.
- **Report per group:** a summary row, a miss-vs-weight scatter, **an overlay of every rock's delivery path**, and a table of every rock with its six figures, each row deep-linking to that shot.
- **Tagging:** an "I played…" button in the viewer header menu, next to ⚑ Flag, on /c/, /s/ and /g/ links. The report page lists tagged games and lets the user change or clear a tag.

Assumptions, not yet contradicted:
- Tags are private to the user.
- A tag saved from a chart link (/c/ or /s/) uses that chart's grading: refined types, `before` reorders, role swaps.
- A tag saved from /g/ uses pipeline data only.
- One tag per user per game; saving again replaces it.

## Design

### Data: new top-level Firestore collection `plays`
- **`records.py`:** `Play{id, user_id, source_id, color, slot, format, chart_id|None, link: "c"|"s"|"g", created_at, updated_at}`.
  - The id is `sha256(f"{user_id}|{source_id}")[:32]`, like the `chart_claims` id. That enforces one tag per user per game.
  - `chart_id` is an edit capability and is **never sent to a browser**.
- **Repo:** Protocol `repo.py:36`, MemoryRepo `repo.py:131`, FirestoreRepo `firestore_repo.py:51`.
  - Methods: `put_play`, `get_play(uid, sid)`, `delete_play(uid, sid)`, `plays_for_user(uid)`.
  - `plays_for_user` is a single-equality query sorted in Python, like `charts_for_owner`, so it needs no composite index.
  - Add `"plays"` to `export_all`/`import_all` in both repos.
- **Why a collection, not a map on User:** `put_user` overwrites the whole document, each tag is its own write, and it backs up as its own key.

### API: new section in `create_app` after flags (`api.py:~1734`)
Every route starts with `require_user` (503 when accounts are off, 401 when signed out).

1. **`PUT /api/me/plays/{source_id}`**, body `{path?, color, slot}`.
   - `path` is the viewer's `location.pathname`, matched with `FLAG_PATH` (`api.py:110`):
     - /g/: `lookup_source`.
     - /c/ and /s/: `lookup()`, which follows `superseded_by`. Require a ready run and `chart.source_id`, then store the resolved `chart.id`.
     - If the path resolves to a different game than `source_id` (after following `merged_into`), return 409.
   - With no `path` (sent by the report page): an existing tag is required; only colour and slot change.
   - Validation: colour must be red or yellow. Slot must be an int (not a bool) in `1..len(fmt.positions)`, with `fmt = format_mod.of_document(...)`. Return 422 otherwise.
   - Keep `created_at`. Rate limit with `bump_rate_limit("play:"+uid, 60/h, 300/day)`.
2. **`GET /api/me/plays/{source_id}`** returns `{play|null}`. The dialog uses it to preselect.
3. **`DELETE /api/me/plays/{source_id}`**.
4. **`GET /api/me/plays`** returns the list, newest `played_at` first. Each item has:
   - title, played_at, league, team names, and `format.to_json()` for position labels;
   - `link` ("chart" or "review");
   - `view_path`: the chart's `/s/{share_slug}/` if it has one, else `/g/{sid}/`, **never `/c/`**;
   - `status`.
5. **`GET /api/me/plays/{source_id}/doc`** returns a document cut down for this player:
   - Chart tag: `timeline.apply_overrides(chart_doc(chart, run, read_only=True), chart.overrides)`, the same input `export.json` uses (`api.py:1596`).
   - If the chart or its run is gone, fall back to the /g/ path and return `fallback: "chart unavailable"`.
   - /g/ tag: `game_doc(run, src.game_index, src, src.play_start_s)`.
   - Then `timeline.for_player(doc, color, slot)`.
   - Serve through `json_revalidated(request, payload, "private")` (`api.py:616`).

**New `timeline.for_player(doc, color, slot)`** in `src/curling_score/timeline.py`:
- **Strip `before` from every shot.** Python's `apply_overrides` reorders but leaves `before` on the shot (`timeline.py:647-656`). If the browser's `layout()` saw it, it would reorder again and renumber with no fixed colours and no role swaps, which is wrong for doubles.
- The player's own rocks keep everything.
- Every other shot keeps only `id, number, color, color_inferred, thrower_slot, position, rock_of_player, has_hammer, missing, state_known, shot_type, label`.
- `stones` is kept only on the last read shot before each of the player's rocks. That is what `buildGameView`'s `stones_before` needs (`timeline.mjs:160-166`).
- `end.placement` is kept, for doubles rock 1.
- `calibration` and `final_stones` are dropped.
- Size: about 30-40 KB gzipped per game, against about 100+ KB for the full document.

### Frontend core (pure, tested from pytest via node)
- **`core/line.mjs`:**
  - Add `lineNumbers(shot, doc)`, returning raw numbers: `miss_m, on_broom, hog_off_m, split_s, split_estimated, curl{m,hit,dir}, turn, tick, hack, rest, reason`.
  - Rewrite `lineFigures` to format from it. **Its output must not change**; the existing `TestLineFigures`/`TestCurlStopsAtAHit`/`TestBroomlessLine` tests pin it.
  - Export `narrowOf(v, turn)`, the same sign convention as `sideWord`.
- **`core/report.mjs`:** export `groupOf`.
- **New `core/myshots.mjs`:**
  - `positionChoices(fmt)` gives labels like "Skip · 7th & 8th" or "Player B · 2nd–4th", via `roleText`/`positionText`.
  - `slotOf(s, fmt)` uses `thrower_slot`, falling back to the index of `position`.
  - `playerRocks(play, doc)` runs `buildGameView(doc, 0, {})`, then selects rocks where `color` and slot match and the rock is not missing. Each row carries: type, group, turn, `lineNumbers`, `lineFigures`, path, and `href = view_path + #e=…&s=…`, using the viewer's hash format (`parseHash`, `App.jsx:59`).
  - `shotGroups(rows)`: fixed group × turn order, empty groups dropped.
  - `summarize`/`summaryText` produce:
    - n;
    - weight median and min–max;
    - broom-miss median with wide / narrow / on counts (left / right when the turn is unknown);
    - counts of rocks that couldn't be plotted.
    
    All strings are built in core, so the JSX does no arithmetic.
  - `missScatter(rows, turn, SCATTERBOX)`:
    - x is the signed miss at the broom in the thrower's frame, with wide/narrow side labels taken from the turn.
    - y is the split, heavier at the top.
    - There's an on-broom band. An estimated split draws a hollow point; a rock whose line the camera behind the thrower disagrees with is dimmed.
- **`core/delivery.mjs`:** add `deliveryOverlay(paths, OVERLAYBOX)`. **Common frame:**
  - Along the sheet: metres from the throwing tee (hack, tee, hog). That is the same for every rock.
  - Across: `dx`, each rock's distance from its own foothold-to-broom line (`hackAimX`, the same aim line `deliveryGeometry` draws).
  - A perfect delivery is then the vertical line dx = 0, whatever the broom or hack.
  - Draw one polyline per rock, a median path (0.25 m bins with at least 3 rocks), and ticks every 10 cm. Reuse `acrossWindow`.
  - Broomless doubles rocks and rocks from before schema 8 are skipped and counted.
- **`core/constants.mjs`:** add `SCATTERBOX`, `OVERLAYBOX`.
- Export the new functions from `core/index.mjs`, and add them to `tests/js/singleton.mjs`, which is a hand-written list.

### Viewer: tagging
- **New `frontend/runtime/plays.mjs`:** `fetchPlay`, `savePlay`, `clearPlay`, each passing the token by hand like `runtime/scores.mjs`.
- **New `frontend/viewer/Played.jsx`:** `PlayedDialog`, a native `<dialog>` like `Flag.jsx`.
  - Sign-in is asked lazily, inside the form's effect: `accountsOn` → `whoIsSignedIn` → a "Sign in" button calling `signInNow` → `fetchPlay`.
  - A team toggle with `teamNames(view.game)` and colour dots.
  - Position buttons from `positionChoices(view.format)`.
  - Save (sends `path: location.pathname`), Clear, Cancel. After saving: "Saved — see My shots" linking to `/shots`.
  - On /g/, when the existing tag is from a chart: a one-line warning that saving switches the report to the pipeline's reading.
- **`App.jsx` header menu** (`:598-606`): `#playedBtn` "I played…", `hidden={!config.hosted || !doc.chart?.source_id}`. `PlayedDialog` sits beside `FlagDialog`.
- **`style.css`:** dialog rules copied from `#scoreDialog`, 44 px targets.

### Site: `/shots` page ("My shots")
- **Route:** `GET /shots` near `/thinking` (`api.py:~797`); `static/shots.html` with `data-page="shots"`; `PAGES.shots` in `site/main.jsx:19`.
- **Links:** "My shots" in `IdentityChip` (`site/ui.jsx`) when signed in, plus a link from `Mine.jsx`.
- **New `site/Shots.jsx`:**
  - Signed out: the same card as Mine.
  - "Games you played" table, from `useResource("/api/me/plays")`, with columns:
    - date and teams;
    - "Red · Skip (7th & 8th)";
    - grading source: chart or pipeline;
    - inline change of team and position (PUT with no path);
    - Clear;
    - Watch (`view_path`).
  - Then fetch `/api/me/plays/{sid}/doc` with `authedFetch`, at most 3 at a time. Pass each through `playerRocks`, keep only the rows, and render progressively ("Loaded 12 of 40 games…").
  - One section per group, in this order: summary row, then scatter and overlay side by side (stacked on a phone), then the shot table. Table columns: date · end/rock · type · six figures (note as `title`) · ↗ link.
  - Empty states: no tags ("open a game → I played…"), no rocks, unplotted counts.
- **New `site/ShotCharts.jsx`:** `MissScatter` and `DeliveryOverlay`, pure SVG with the literal `PAINT` colours and a `useId()` clipPath. Import `core/myshots.mjs` and `core/delivery.mjs` directly so esbuild tree-shakes.
- **`site.css`:** `body[data-page="shots"]` width; two chart columns at ≥700 px; tables turn into cards at 640 px (the `data-label` pattern, `site.css:92-137`).

## Steps (each committed on its own; `npm run build` after every frontend step)
0. Write this design as `docs/superpowers/specs/2026-10-04-my-shots-design.md`, following the repo's habit.
1. `Play` record and repo methods (memory and Firestore), export/import. Tests: `tests/test_repo_contract.py::TestPlays`, `"plays"` added to the cleanup list (`:40-43`), and a play included in `test_service_core.py::TestBackup`.
2. `timeline.for_player`.
   - `tests/test_timeline.py::TestForPlayer`: what is kept and dropped, `before` stripped, the position fallback, doubles.
   - **JS parity test** in `tests/test_viewer_js.py`: JS `buildGameView(doc,0,ov)` against `buildGameView(for_player(apply_overrides(doc,ov)),0,{})`, on a move, a role swap, a swap with a move, and a hand-coloured blank. Compare `number/id/color/thrower_slot/position`, and the player's `stones_before`/`hack`.
3. API routes, with new `tests/test_plays.py` (fixtures `w` with SARAH/ALEX from `test_service_auth.py`, `work_through`). Cover:
   - 401 signed out, 503 with accounts off;
   - saving from /g/, /c/ and /s/;
   - **the chart id never appears in any response**;
   - `view_path` is /s/;
   - saving again replaces the tag and keeps `created_at`;
   - a path to another game gives 409;
   - bad colour or slot gives 422;
   - ALEX cannot see SARAH's tag;
   - DELETE;
   - the doc applies overrides (refined type, `before` renumber), with no `before` in the output;
   - /g/ ignores overrides;
   - the doc is cut down;
   - `superseded_by` and `merged_into` are followed;
   - 304 on a matching ETag;
   - 429 past the rate limit.

   The API can be deployed on its own after this step.
4. `lineNumbers` refactor and the `groupOf` export. Existing figure tests stay green; rebuild `app.js`.
5. `core/myshots.mjs`, `deliveryOverlay`, the boxes and exports. Tests: `TestLineNumbers`, `TestPlayerRocks` (on `tests/fixtures/report/dimmit_grant.json` baked by Python, plus synthetic `lined()` shots for figures and paths), `TestShotGroups`, `TestMissScatter`, `TestDeliveryOverlay`, `TestPositionChoices`.
6. Viewer: `runtime/plays.mjs`, `Played.jsx`, `App.jsx`, CSS. JSX source-text tests: the `playedBtn` hidden rule, and sign-in asked only inside the form's effect.
7. Site: html shell, route, `Shots.jsx`, `ShotCharts.jsx`, `PAGES`, chip link, CSS. Add `/shots` to the page-route tests (`tests/test_service_api.py:744-763`) and `scripts/sitesmoke.mjs:28`. A source-text test that `Shots.jsx` has no `toFixed`.
8. Deploy the API only, from a clean worktree (another session shares this checkout). No worker or pipeline change, no new Firestore index or rules; `/api/admin/export` picks up `plays` automatically.

## Verification
- **Tests:** run the affected test subsets, not the whole suite (it OOMs on this box):
  - `pytest tests/test_plays.py tests/test_timeline.py tests/test_repo_contract.py tests/test_service_auth.py tests/test_entered_scores.py tests/test_service_api.py tests/test_frontend_build.py`
  - `pytest tests/test_viewer_js.py -k "LineFigures or CurlStops or Broomless or LineNumbers or PlayerRocks or ShotGroups or MissScatter or DeliveryOverlay or PlayerSlice or PositionChoices or Played or Shots"`
  - The Firestore half of the contract tests runs with the emulator (Java 21 PATH).
- **Locally:** run `scripts/devserve.py` on a schema-8 timeline. Accounts are off there, so check the 503 messages. Signed-in flows are covered by the FakeVerifier tests.
- **After deploy, on the live site, signed in:**
  - Tag one game from /c/, one from /s/ and one from /g/ (a doubles game among them).
  - Open `/shots` and check:
    - groups, counts and summary text;
    - the scatter, and that the overlay paths sit around dx = 0;
    - deep links open the right shot.
  - Check at 390×844 with `scripts/cdp.mjs` phone emulation, and run `scripts/sitesmoke.mjs` for console errors.
  - Change and clear a tag from `/shots`.
- **Hand check of one game:** compare one tagged game's rocks against the viewer's Detail pane: same figures, same turn, same group.

## Risks and edge cases
- **Reprocessed game:** the tag is colour + slot, so it survives renumbering.
- **Superseded chart:** followed through `lookup`. If the chart is gone, the report falls back to /g/ and says so.
- **Old charts:** before schema 6, rocks count but show "–" under "Turn not measured". Before schema 8 there's no path; groups show the skipped counts.
- **Doubles:** role swaps are only known from a chart's overrides. Broomless rocks have no miss and no overlay, and are counted as not plotted.
- **Wrong colour picked:** can't be checked. Team names in the dialog reduce the chance.
- **About 40 games:** 40 small cached requests, 3 at a time. Server memory stays bounded by the existing `load_doc` LRU (64).
