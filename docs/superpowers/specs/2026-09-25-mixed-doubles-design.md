# Mixed doubles support

## Context

The club also streams a Thursday Mixed Doubles league: 80 games in
[PLrMwR_YwOp3Kse6HI0SWeSQmxuP9Efay5](https://www.youtube.com/playlist?list=PLrMwR_YwOp3Kse6HI0SWeSQmxuP9Efay5),
each about 100 minutes, on the same five sheets and the same camera composite.
Titles look like `3/19 - Sheet 1 - Thursday Mixed Doubles League 2025-2026`.

Most of the pipeline carries over: panels, calibration, rock detection, the
release/arrival pairing, splits, the hog line, curl, the aim line and the board
reader. What breaks is structural. The code assumes an empty sheet at the start
of an end, 16 rocks per end, four players with two rocks each, and "a blank end
keeps the hammer". Nothing in the repo has a notion of game format.

**Goal:** a doubles video submitted to the service gets a correct shot list,
hammer, power play, score and viewer. Four-player output stays byte-identical.

## Rules that differ (WCF R17) and what each one touches

| Rule | Doubles | Code it hits |
|---|---|---|
| Stones | 6 per team, 1 **placed** before the end, 5 delivered → **10 deliveries/end** | `constants.py:41-46`; `fit_end` limits; `_fill_short_end`; `secondpass`; `endcheck`; `shots_expected` (which drives the practice trim and board settling) |
| Placed stones | One team's stone on the centre line, back edge at the back of the 4-foot (y ≈ −0.47). The other team's stone is a centre guard (marked spots, y ≈ 3.2–5 m, often above the panel's view at about 4.3–4.6 m). Both count for score. | `delivery.py:773-851` takes the guard as a "house-appear" delivery (a slid stone as a full "rest" delivery with a paired release); `shots.py:361-400` house-size fill invents blanks; rock 1's `house_delta`, delivered stone and ghosts are measured against an empty sheet |
| Power play | Once per team per game, never in an extra end. The in-house stone's back edge sits on the tee line where the 8- and 12-foot meet (x ≈ ±1.22, y ≈ +0.14); the guard is off-centre on the same side. | placement gates; a new per-end record |
| Hammer | The team with the in-house placed stone throws second. The team that didn't score gets the placement decision; **after a blank, the team that threw first gets it**. The decision is a choice, so the chain is only an expectation. | `rules.next_hammer`/`hammer_chain`/`first_hammer_given` (`rules.py:104-156`), `hammer_consistent` (`timeline.py:253-281`). `has_hammer = n % 2 == 0` still holds. |
| Throw order | Player A throws team rocks 1 & 5, player B throws 2–4; they may swap between ends | `rules.throw_info`/`shot_label`; JS `throwInfo`/`renumber` (`frontend/core/timeline.mjs:37-65`); `POSITIONS`; `stats.mjs:121-140` buckets; `line.mjs:46-69` `playerHacks` |
| No removal | No stone may be removed before the 4th delivered stone | Not modelled (the fours free-guard-zone rule isn't either) |
| Length | 8 ends; at most 6 points in an end | Nothing caps the number of ends; `MAX_SCORE_PER_END` 8 is merely loose |
| Broom | The partner either holds the broom in the house or sweeps | `broomtime.pick_target` may take a broom lying on the ice as the target |

Sources: [Wikipedia: Doubles curling](https://en.wikipedia.org/wiki/Doubles_curling),
[Ardsley rules summary (WCF text)](https://www.thesalmons.org/lynn/curling/mixeddb.html),
[USA Curling 2026-27 guard update](https://www.usacurling.org/press-releases/2026-2027-world-curling-mixed-doubles-guards),
[club doubles page](https://curlingseattle.org/mixed-and-open-doubles).

## Decisions made with you

- **Players:** fixed pairs. Player A (team rocks 1 & 5) and player B (2–4) for
  the whole game. A charter can mark "team swapped roles this end", and stats
  and the hack call then follow the person.
- **Format source:** the form choice, else "Doubles" in the title or league,
  else fours. The pipeline checks for the placed-stone signature and flags a
  mismatch, but never overrides.
- **Rollout:** submissions only. Deploy so a submitted doubles link works; the
  playlist is not added to the poller.
- **Data:** the worker downloads 3–4 doubles games into its cache (198 GB free).
  This laptop has 1.2 GB free, so replays and harvests run on the worker.

## Working method

- This is architectural work. First step of implementation: copy this plan to
  `docs/superpowers/specs/2026-09-25-mixed-doubles-design.md` (repo
  convention), then write per-phase plans under `docs/superpowers/plans/`.
- Work on a `doubles` branch in its own worktree, because another session
  shares the checkout. Commit only our own hunks, and deploy from a clean
  worktree.
- Run test subsets, not the whole suite (the full suite OOMs on this box).
  `TestAgainstHandMarkedCrossings` already fails on main.

## Phase 0: Look before building (worker)

1. Download 4 games into `/data/wdd/curling-cache/videos` on
   `administrator@10.0.0.182`, spread over sheets and dates: `brMO74e6ZZU`
   (3/19 S1), `n7ifEk4Zfl8` (3/26 S4), `8J3r5FhFFd4` (4/2 S3) and `ih59IKFUHXk`
   (2/19 S5).
2. Baseline: run today's pipeline unchanged on one game and record the failure
   modes (phantom deliveries, invented blanks, practice trim, withheld scores).
   Use `scripts/replay_end.py` to see what it makes of the placed stones.
3. Pull frames around 6–8 end starts per game (ffmpeg crops on the worker) and
   answer these questions:
   - **How are the stones placed?** By hand at the far end, slid down the sheet
     from the delivery end, or pushed in from behind the house? Sliding means
     phantom releases as well as phantom arrivals.
   - What is the shortest gap from placement settling to the first release?
     What is the spread between the two stones settling? Is the previous end
     still being cleared?
   - Where is the guard relative to each panel's top edge, and how often is it
     out of view?
   - Does the partner hold a broom in the house, sweep, or both? Is a broom
     ever laid on the ice?
   - Is there practice at the start? How many ends fit in the slot? Is the board
     kept, and do cards 7/8 appear and read?
   - How often are power plays used?
4. Hand-mark 2 games in `datasets/doubles/marks/<video>.json` and commit
   straight away. Per end: house colour, guard colour, power-play side or null,
   `t_placed`, the 10 deliveries as (time, colour), and the board score. This
   is the acceptance set.
5. Side benefit: any real cards 7/8 go into the digit reader's gate set
   (`datasets/board-cards`). League fours games almost never produce them (see
   the digit-ceiling note).

**Gate:** write the findings into the spec and review them with you. The
placement method sets the placement stage's gates.

## Phase 1: Format object (fours output unchanged)

New file `src/curling_score/game/format.py`:

```python
@dataclass(frozen=True)
class GameFormat:
    name: str; stones_per_team: int; placed_per_team: int
    throw_table: tuple[int, ...]   # team stone k -> player slot (unswapped)
    positions: tuple[str, ...]; blank_passes_hammer: bool; swappable: bool
    # derived: delivered_per_team, delivered_per_end, max_score_per_end
    def throw_info(self, n, swapped=False) -> rules.ThrowInfo
    def shot_label(self, end, n, swapped=False) -> str   # "3rd end, B's second rock"
    def to_json(self); from_json(d); of_document(doc)     # missing block -> FOURS
FOURS   = GameFormat("fours",   8, 0, (1,1,2,2,3,3,4,4), ("lead","second","third","skip"), False, False)
DOUBLES = GameFormat("doubles", 6, 1, (1,2,2,2,1),       ("A","B"),                        True,  True)
```

- `rock_of_player` is how often the slot appears in `table[:k]`. For FOURS
  this reproduces `rules.py:29-36` exactly, and a test pins it.
- **Thread it as an explicit `fmt=FOURS` keyword, not a module global.** The
  API applies overrides and `trim_to_start` to documents of both formats, so
  document-level code reads `GameFormat.of_document(doc)`. The duplicated
  per-end pipelines in `cli.py:127-155` and the replay scripts stay fours with
  no edits.
- **Use sites:**
  - `rules.throw_info`/`shot_label`;
  - `fit.fit_end(per_end, per_team)` (already parameterised; `analyze.py:402`);
  - `secondpass.gaps_to_search` (:57, :67); `endcheck.check` (:42-60);
  - `shots.from_deliveries` and `_fill_short_end`;
  - `segment` `MIN_END_S` (:30);
  - `timeline.build_end` (:110-128, :214-215), `build_game`, `_renumber`,
    `apply_overrides`, `trim_to_start` (:594);
  - the "/16" log lines (`analyze.py:454`).
- `constants.py` stays the source of the FOURS values. `misses.py` (CLI only)
  and the scoreboard's score cap are deferred.

## Phase 2: Carry the format from submission to worker

Copy the path `sheet` already takes:
- `Run.format` (`records.py:26-57`), plus `format_source` (form or title).
- `POST /api/submissions` accepts `format` (`api.py:740-825`). If it's absent,
  resolve it from the title with a new `format_from_title` next to
  `league_from_title` in `ingest/source.py:87-110`, matching `\bdoubles\b`.
- The claim dict carries it (`api.py:1288-1293`).
- `worker.py:167` passes `game_format=` to `analyze()` (`analyze.py:178`).
- The CLI gets `analyze --format` (`cli.py:591-617`).
- **Reuse is keyed on format** (`dedupe.find_reusable_run`, `dedupe.py:63-74`).
  Otherwise a doubles submission gets a fours run back.
- `admin_reprocess` copies format (`api.py:1500-1508`).
- `find_or_create_source` copies it onto `Source`, and `/api/games` returns it.
- The worker echoes format into `meta.json`. The API refuses an upload whose
  format differs from the run's, so a not-yet-updated worker can't silently
  build fours.

## Phase 3: Pipeline for doubles

**Hammer rules** (`rules.py:104-156`)
- `next_hammer`, `hammer_chain` and `first_hammer_given` take
  `blank_passes=fmt.blank_passes_hammer`.
- In doubles `build_game` seeds the chain from each end's placement read and
  treats `hammer_consistent` as a soft check.
- Game-level `power_plays: {red: [ends], yellow: [ends]}`. Flag more than one
  per team, or one used in an extra end.

**Placement stage**: new `game/placement.py`, doubles only. It runs right
after `seq` is built (`analyze.py:355`), before `find_deliveries`.
- `find(seq, from_s, end_s, fmt, setup)` slides a 6 s window in 1 s steps over
  `delivery._settled_stones`. It accepts the first arrangement with:
  - exactly one stone on a house spot: normal (|x| ≤ 0.30, |y + 0.465| ≤ 0.30)
    or power play (||x| − 1.22| ≤ 0.35, |y − 0.14| ≤ 0.30);
  - the guard, if seen, of the other colour and in a matching position
    (centre-line y ≥ 2.3, or same side for a power play);
  - persistence for ≥ 10 s, on a spot that was empty earlier;
  - the two stones settling close together in time (in fours the first two
    stones are a full delivery apart).
  Phase 0 sets the tolerances. The guard is often above the panel, so add
  `PanelSetup.view_y_max_m` (next to `profile.py:48`) and treat "house stone
  only" as a normal result.
- It returns the placed stones (colour, x, y, role), `t_placed`, the hammer
  (house stone's colour, or the guard's opposite), `power_play: {color, side}`,
  `spread_s`, `complete`, and `seed`, the house observed just after placement.
- **Exclusion** (the same whichever way the stones are placed):
  - releases use `since=max(from_s, t_placed)` (`analyze.py:387-390`);
  - arrivals keep `t_enter ≥ t_placed + release.MIN_LAG_S`, and any candidate
    that comes to rest on a placed stone's spot shortly after `t_placed` is
    dropped and counted;
  - second-pass gaps start after that gate (`secondpass.py:52-53`).
  Dropping only the arrival isn't enough: its release would come back as a
  "release-add" through `release.settle` (`release.py:382, 459`).
- **Seeding:** `from_deliveries(..., fmt, before=seed)` starts `previous` from
  the placed stones (`shots.py:487-499`). Rock 1's `house_delta`, delivered
  stone index and ghosts then work, and the "added stone of the colour that
  didn't throw" count stays down (see `version.py:38-42`). `_fill_short_end`
  gets `base = placed stones located` (2 if placement wasn't located), so they
  don't invent blanks. FOURS keeps base 0.
- If no placement is found: fall back to today's gates, set
  `placement: null`, and record a problem.
- `run_up_from`: docstring only. The panel holds this end's placed stones,
  then its first deliveries.
- **Per-end fields (doubles only):**
  - `placed`, `power_play`, `hammer` (from placement, otherwise shot 1) and
    `hammer_source`;
  - `placement: {t_s, spread_s, complete, agrees_with_shots, candidates_dropped}`.
  - `agrees_with_shots` compares the guard's colour with shot 1's, and only
    when shot 1 was seen rather than inferred.
- The guard's position when it's above the panel is recorded as unseen. Reading
  it from the destination long camera (the `SideView.to_house` calibration) is
  a follow-up, taken on only if phase 0 shows it matters.

**Format check**, a pure function over the built games:
- Declared doubles looks like fours if placement is found in fewer than 40% of
  ends, or the median of candidates offered per end is 14 or more.
- Declared fours looks like doubles if the median offered is 12 or fewer and
  each team offers 6 or fewer in 75% of ends.
- Recorded as `format.check` for doubles, and as a document-level
  `format_warning` for fours, written only when it fires. The worker copies it
  to `meta.json` for the status page.

## Phase 4: Schema 7 and the role-swap override

- `SCHEMA_VERSION` goes to 7 (`timeline.py:15`, `frontend/core/wire.mjs:101-110`).
  The document-level `format` block is **written only for non-fours**
  documents. A missing block, or schema 6 or earlier, means fours. It carries
  the name, source, stones, placed and delivered counts, positions,
  `throw_table`, `blank_passes_hammer` and the check.
- In doubles `thrower_slot` is the **person** (1 = A, 2 = B) after swaps. That
  lets `playerHacks` (keyed `color|thrower_slot`) and the stats follow the
  person with no change to their keys. `shots_expected` is 10, so
  `settle_board_scores` and `trim_to_start` (`timeline.py:501-571`) work
  unchanged.
- **Swap override key:** `"g.<end identity>"` with patch
  `{"roles_swapped": {"red": true}}`.
  - The server already stores any `{key: dict}` (`api.py:1135-1192`).
  - Old Python skips two-part keys (`timeline.py:448-451`).
  - Split `_assign_throwers(shots, end_no, fmt, swapped)` out of `_renumber`.
    It rewrites thrower fields only, never colours.
  - JS `layout()` mirrors it, and the key is left out of the "charted shots"
    counts.
  - Use the end *identity* on both sides. JS builds its colour-fix prefix from
    `e.number` today (`timeline.mjs:102`), while Python uses `end_identity`
    (`timeline.py:455`).

## Phase 5: Viewer and site

- New `frontend/core/format.mjs`: `formatOf(doc)` (FOURS fallback),
  `throwInfo(n, fmt, swapped)`, labels and positions. `timeline.mjs`
  `renumber`, `constants.mjs` `POSITIONS`, `stats.mjs` `gatherStats`,
  `Report.jsx` and `ChartPanel.jsx:163` read from it instead of hard-coding
  four positions. `gatherStats` must not silently drop an unknown position
  (`stats.mjs` `if (!bucket) continue`).
- Doubles labels: "3rd end, B's second rock". The thrower row reads
  "Player B (rock 2 of 3)". Report cards are "A · 1st & 5th" and
  "B · 2nd–4th", following the person.
- End bar (doubles): hammer from placement, a power-play badge ("Power play ·
  red, left"). In the editing surface, "Red swapped roles" and "Yellow swapped
  roles" toggles write the `g.e` override.
- Rock 1's house draws the placed stones, with ghosts wherever rock 1 moved
  one, because the seed now comes from the pipeline.
- Site: the Submit form gets a Format select (Auto from title / 4-player /
  Doubles, `site/Submit.jsx:28-34`). The games list shows a "Doubles" tag. The
  status page shows `format_warning`.
- Rebuild with `npm run build` only; the build stamp is checked by a test.

## Phase 6: Target broom in doubles

- On the cached games, measure broom2's coverage and hand-check a sample of
  targets against the destination long camera (`/data/wdd/curling/broom_waves/coverage_probe.py`).
- If a partner who is sweeping, or a broom lying on the ice, yields false
  targets, add a doubles gate. For example: the pad must be held, with a shaft
  and a person above it. Otherwise return `None`, which the aim-line code
  already handles. The data decides which.

## Phase 7: Deploy for submissions

- Deploy both halves per the deploy note: rsync the worker from a clean
  worktree (never `--delete`, exclude `worker.env`/`.git`), then
  `docker compose up -d --build`, then `deploy-api.sh`.
- Bump `PIPELINE_VERSION` only if fours output changed. It shouldn't, and
  format in the reuse key keeps existing fours runs reusable.
- With your go-ahead, submit one doubles game through the live form and check
  the chart.

## Verification

- **Fours unchanged:** after each phase, run the 16-game harness on the worker
  (`/data/wdd/curling/ds15/games`, detection caches warm) at the base commit and
  at head. Strip `schema_version`, `processing_version` and
  `source.analysed_at` with `jq`, then `cmp` the results. Any difference blocks
  the phase.
- **Unit tests** (targeted subsets):
  - `test_format`: the FOURS table matches today's `throw_info` for 1–16; the
    DOUBLES table; shot 11 raises.
  - `test_rules`: a blank passes the hammer in doubles, forward and backward.
  - New `test_placement` (using `test_delivery.py`'s `det`/`merge` helpers):
    - stones placed by hand, slid with releases, and pushed from behind;
    - power play on each side;
    - the guard out of view;
    - rejections: a fours opening (two stones 60 s apart) and a same-colour
      pair.
  - Exclusion: a slid placement plus 10 deliveries keeps exactly 10, shot 1 is
    the guard's colour, and there's no release-add.
  - `test_shots`: a seeded rock 1 adds no stones of the colour that didn't
    throw; the delivered index skips placed stones; base 2 adds no leading
    blanks, but a missed rock 1 still gets one.
  - `test_timeline`: a `g.e` swap changes one colour's slots only; a schema-6
    document reads as fours; `trim_to_start` relabels with the document's
    format.
  - `test_viewer_js`: JS and Python agree on a doubles document with a swap.
  - Service tests: the claim carries format; reuse is keyed on format; a
    "Mixed Doubles" title resolves to doubles; a mismatched upload is refused.
- **Doubles acceptance** on the two hand-marked games (then spot checks on the
  other two):
  - hammer and power play 100% right wherever the placement is visible;
  - zero placed stones counted as deliveries;
  - delivery recall and precision (same colour, ±5 s) and shot-number accuracy
    at least as good as the fours harness;
  - detected score agrees with the board at least as often as fours (40/54);
  - no rock-1 diffs that add a stone of the colour that didn't throw;
  - the format check is right on every game.
- **Viewer:** `npm run build` plus `tests/test_viewer_js.py` and
  `test_frontend_build.py`. Also run a headless phone check (devserve +
  `cdp.mjs` at 390×844) on a doubles timeline: labels, the power-play badge,
  the swap toggle and its effect on the report and hack call.
