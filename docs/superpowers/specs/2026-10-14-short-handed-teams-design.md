# Short-handed teams: a game played with three

**Status:** design approved in chat on 2026-10-14. The user chose:
- whole game only;
- the three players named Lead / Second / Skip;
- the mark kept on the game and applied when the game is served.

Example game: `/g/s_1UuJFNsEmVfYZaPkz/` (10/09, Sheet 1, Friday Evening League).

## Context
A team sometimes plays with three. It still throws eight rocks an end, but the rule splits them 3/3/2: the first two players throw three each and the last throws two. Detection is unaffected; the example game reads 16 rocks an end.

The problem is who gets credit. Every rock is credited to a player through the format's throw table. For fours that is `(1,1,2,2,3,3,4,4)`, and it is the same for both teams (`game/format.py`, mirrored in `frontend/core/format.mjs`). For a three-player team that table is wrong for three of its eight rocks:
- Rock 3 belongs to the lead, not the second.
- Rock 6 belongs to the second, not the third.
- Rock 7 belongs to the skip, not the third.

The wrong credit reaches everything that reads `thrower_slot` / `position` / `rock_of_player`:
- shot labels and the Detail row;
- the game report's by-position tables (`core/report.mjs`, `core/stats.mjs`);
- the hack call (`core/line.mjs`, keyed `color|thrower_slot`);
- "I played…" and My shots (`timeline.for_player`);
- game clips (`gameclips.player_rocks`).

Nothing in the video says a team is short, so a person has to say so.

Decisions the user made:
- **Whole game only.** A team that was three for part of a game is out of scope. The data shape can take "from end N" later without a migration.
- **Lead / Second / Skip**, named by throwing order:
  - Lead throws the team's 1st–3rd rocks;
  - Second throws the 4th–6th;
  - Skip throws the 7th and 8th.

Assumptions, not yet contradicted:
- The split is always 3/3/2 (WCF and Curling Canada rules); there are no league variants.
- Anyone signed in may set it, as with team names.
- Both teams may be short (3 v 3).
- Fours only. A doubles team is two players by definition.

## Design

### Data
- **`records.py` `Source`:** add `short_handed: list[str] | None = None`, the colours that played with three. It sits next to `team_red`/`team_yellow` and for the same reason: it is learned long after the run, and every chart pinned to that run should get it, including charts made earlier. The Firestore repo serializes `Source` generically. Even so, run `tests/test_repo_contract.py`'s Firestore half on the emulator.

### Format: a second throw table for a team of three
- **`GameFormat`:** add `short_throw_table: tuple[int, ...] | None`.
  - `FOURS` gets `(1, 1, 1, 2, 2, 2, 4, 4)`; `DOUBLES` gets `None`.
  - `to_json` emits the field.
- **Slots keep their four-player meaning:** 1 lead, 2 second, 4 skip. Slot 3 (third) throws nothing for a short team. This is deliberate:
  - a play already saved as skip (slot 4) stays skip;
  - `positions[slot - 1]` needs no change;
  - the report's position rows still line up (lead v lead, skip v skip), with the short team's "third" row empty;
  - the hack call's `color|slot` key is unchanged.
- **`throw_info(n, swapped=False, short=False)`** reads `short_throw_table` when `short` is true and the format has one. `shot_label` takes `short` as well.
  - The JS mirrors all of this: `throwInfo(n, fmt, swapped, short)`, `shotLabel`, and `roleText(fmt, slot, short)`. With `short`, `roleText` reads "1st–3rd", "4th–6th", "7th & 8th".
- **`format.short_handed_of(doc)`:** the set of short colours in a document's format block, filtered to `red`/`yellow`. It is empty for doubles or when the block is missing or malformed. Its JS mirror, `shortOf(fmt)`, reads the block `formatOf` returns.

### Serving: `game_doc` re-credits the short team
- **`api.py` `game_doc`:** when `src.short_handed` is non-empty and `format.of_document(doc)` is `FOURS`, after the entered scores and before the team names:
  1. Write `short_handed` into `doc["format"]`, creating the block from `FOURS.to_json()` when an older document has none. Also write `short_throw_table`, so the viewer never has to know the table by name.
  2. Call `timeline.assign_short_handed(doc)`. It re-runs `_assign_throwers` on every end, which sets slot, position, rock-of-player and label for the short colours' rocks.
- **Where the mark has to hold:**
  - `_assign_throwers` and `_renumber` in `timeline.py` work out each rock's `short` from its colour, after any recolouring.
  - `apply_overrides` passes the document's short set down. A charter who moves a rock with `before` therefore keeps 3/3/2.
  - The JS mirrors in `core/timeline.mjs` (`assignThrowers`, `renumber`, `layout`) do the same from `fmt.short_handed`.
- **Covered automatically:** `for_player`, `gameclips.player_rocks`, the hack call and the report all read the re-credited fields, so they need no change.
- **Not changed:** the thinking report, which goes through `refresh_thinking` (`game_doc` with `src=None`). It is per team, not per player.

### API
- **`POST /api/games/{source_id}/teams`** also takes `short_handed`, a list of colours.
  - Entries must be distinct, and each must be `red` or `yellow`; otherwise 422.
  - A doubles game (`src.format == "doubles"`) gets 422 with "a doubles team is two players".
  - An empty list clears the mark. Leaving the key out leaves the mark as it is.
  - The response returns the stored list, and the change is logged with the user, as names are.
- **`PUT /api/me/plays/{source_id}`:** when the chosen colour is short, slot 3 gets 422 with "that team played with three". The slot range is otherwise unchanged.
- **The plays list (`plays_list`):** each item gains `short_handed: src.short_handed or []`.

### Viewer
- **`Teams.jsx` ("Who played?"):** a **Played with three** checkbox under each team's name box.
  - It is hidden when the game is doubles (`view.format.name === "doubles"`).
  - The checkbox starts from `fmt.short_handed`.
  - It is sent in the same `sendTeams` request (`runtime/teams.mjs`), and the page re-reads the game as it does after names.
  - The ⋯ menu item is renamed from "Name teams…" to **"Teams…"**.
- **Showing it:** where the viewer names a short team (the report's head-to-head and team headings), add "· 3 players". A reader then knows why the third's row is empty.
- **`Played.jsx` ("I played…"):** once a team is picked, `positionChoices(fmt, short)` offers only that team's players: Lead (1st–3rd), Second (4th–6th), Skip (7th & 8th). If the picked team is short and the saved slot is 3, nothing is preselected.
- **My shots (`core/myshots.mjs` `playText`):** uses the play's `short_handed`, so a play reads "Dimmit · Lead (1st–3rd)".
  - A slot-3 play on a short team is kept, never deleted, and credits no rocks.
  - The list shows that play as "This team played with three. Pick your rocks again", linked to `view_path`.

### Edge cases
- **Unmarking:** the next read is plain fours again. Plays are untouched.
- **Clips:** they are named by rock. The report already asks for clips it is missing, so newly credited rocks get cut the next time it opens. Clips of rocks that are no longer the player's are no longer listed.
- **Blank and missing rocks:** these still carry numbers, and credit follows the number as it does today.
- **Charts made from an older run:** they get the mark too, because it is applied at serve time.

## Testing
- **`tests/test_format.py`:**
  - `throw_info(..., short=True)` for all 16 shot numbers: slots, `rock_of_player`, labels.
  - `DOUBLES` ignores `short`.
- **`tests/test_timeline.py`:**
  - `assign_short_handed` re-credits only the short colour.
  - `apply_overrides` with a `before` move on a short team keeps 3/3/2.
  - `for_player(doc, "red", 1)` returns the lead's three rocks.
- **`tests/test_service_api.py`:**
  - `short_handed` can be set and cleared, and anything that is not `red`/`yellow` gets 422.
  - Doubles gets 422.
  - `/g/{sid}/timeline.json` and a chart's timeline both carry the re-credited rocks and `format.short_handed`.
  - A slot-3 play on a short team gets 422.
  - The plays list carries `short_handed`.
- **`tests/test_game_clips.py`:** `player_rocks` on a served short-handed document picks three lead rocks.
- **`tests/test_viewer_js.py`:**
  - Add a short-handed document, with one reordered end, to the Python/JS parity check.
  - Unit-test `throwInfo`, `roleText`, `positionChoices` and `playText` for a short team.
- **Firestore emulator:** run `test_repo_contract.py`'s Firestore half.
- **Phone:** a headless check of the Teams and I played dialogs at 390×844 (devserve + cdp.mjs).
- **Test runs:** run the affected test files rather than the whole suite, which gets OOM-killed on this box.

## Rollout
- An API deploy only: no worker rebuild and no reprocess.
- After the deploy, mark the short team on `s_1UuJFNsEmVfYZaPkz`, using the dialog or the endpoint once the user says which colour.
- Check that `/g/` shows "lead's third rock" on that team's rock 3.
