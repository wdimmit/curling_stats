# Doubles: marking the ends a pair switched order

**Status:** design approved in chat on 2026-10-21. The user chose:
- mockup **A**: in "I played…", a **We switched during the game** checkbox opens the game's ends, and the player taps each end where they threw the other rocks;
- the marking is **a fact about the game**: one player's marking re-credits both partners, for everyone who views the game.

A builder check the same day raised five questions. All five are settled below, each marked *(my call)*.

Mockups: canvas https://claude.ai/artifact/AHpaoqvgtKZdJB32gD2se3. Board A is the chosen one; boards B and C were not chosen. Sources: `out/mockups/doubles-ends/project/Main.dc.html` (gitignored).

Example game: chart `/c/5eFAHyvb3mSui54QyaJ242/` (video `8J3r5FhFFd4`, 7 ends, power plays in ends 5 and 7).

## Context
A doubles team delivers five rocks an end. The throw table is `(1, 2, 2, 2, 1)`: player A throws the team's 1st and 5th rocks, player B the 2nd to 4th (`game/format.py` `DOUBLES`, mirrored in `frontend/core/format.mjs`). Pairs often trade roles between ends.

The code already models a trade, end by end:
- A chart override keyed `"<game>.<end identity>"` holding `{"roles_swapped": {"red": true}}` says Red's players threw the other way round in that end.
- `timeline._swapped` reads it, and `_assign_throwers` / `_renumber` move `thrower_slot`, `position`, `rock_of_player` and `label`. They never move `number`, `color` or `has_hammer`.
- The JS mirrors are `swappedOf`, `assignThrowers`, `renumber` and `layout` in `frontend/core/timeline.mjs`, parity-tested in `tests/test_viewer_js.py`.
- Everything downstream reads the re-credited fields: My shots (`timeline.for_player`), game clips (`gameclips.player_rocks`), the report's player rows and the hack call.

Few players can reach it:
- The only control is the chart's End box (`frontend/viewer/ChartPanel.jsx` `EndBox`). It is shown to someone who may edit that chart, in charting mode, and reads "Red swapped roles (player B threw first and last)".
- "I played…" (`frontend/viewer/Played.jsx`) saves `{color, slot}` for the whole game: Player A (1st & 5th) or Player B (2nd–4th).
- A play saved from a game link (`/g/`) reads the game as detected (`api.play_doc` → `game_doc`), so no chart's swap ever reaches it. Only a play saved from a chart reads that chart's overrides.

So when a pair switched, both players' My shots, clips, report rows and hack call are wrong for those ends, and neither player can fix them.

## Decisions
The user's:
- **Dialog A**, as drawn on board A.
- **Stored on the game**, not on a claim: on `Source`, applied when the game is served, as `Source.short_handed` is ([2026-10-14-short-handed-teams-design.md](2026-10-14-short-handed-teams-design.md)).

My guesses, played back and accepted ("Looks good"):
- **Who may set it:** anyone signed in who saves a claim on a team may set that team's switched ends. No validated role is needed, which is looser than Teams….
- **Clearing a claim** leaves the switched ends, because they are a fact about the game.
- **One answer per end:** the chart End box's checkboxes become this same setting.
- **No notice:** a later change by either partner simply re-credits, without telling the other.

## Design

### What a switched end means
In a switched end, the team's slot-2 player threw the 1st and 5th rocks and slot 1 threw the 2nd to 4th. This is exactly today's `roles_swapped`.

**The first end is never switched.** A claim's slot means "who threw 1st & 5th in the game's first end", so a switched first end would credit a player with the wrong rocks in every end. *(My call, from the builder check.)* "First end" means the first end of the game as served, after the practice trim.
- Both routes return 422 for it.
- The End box offers no checkbox in it.
- The migration normalises old first-end swaps (see below).

### Data
- **`records.py` `Source`:** add `switched_ends`, holding per colour the ends that team switched plus who set them and when. For example: `{"red": {"ends": [4, 5, 6, 7], "by": "<uid>", "at": "<iso>"}}`. `None` or a missing colour means none.
  - Ends are keyed by **end identity** (`timeline.end_identity`: the number the end was detected with). That is the key chart overrides use, and it survives the practice trim.
  - The dialog shows each end's displayed number.
  - **Accepted limitation:** a reprocess, or a live game re-cutting its ends, can move the marks onto other ends, as it can for chart overrides.
  - **The marks apply to every document of the game,** whichever run it was built from: the current run, or an older run a chart is pinned to. They are not tied to a run id, because that would drop them on every reprocess. *(My call.)*
  - **They are read from the source `game_doc` is given,** as `short_handed` is. Saves land on the canonical source, because the play route resolves it. So a chart whose game was later merged into another does not get marks saved after the merge. Its end numbers belong to the older reading of the game, where those marks would land on the wrong ends. *(My call. It corrects the first draft's "canonical source everywhere", after reading `chart_doc`.)*
- The Firestore repo serializes `Source` generically. Run `tests/test_repo_contract.py`'s Firestore half on the emulator anyway.

### Serving: `game_doc` writes the marks into the document
- **`api.py` `game_doc`:** when the document's format is swappable and `src.switched_ends` names a colour:
  1. set `end["roles_swapped"] = {colour: True, …}` on each matching end, matched by `end_identity`;
  2. re-credit those ends with `_assign_throwers`, the way `assign_short_handed` does.

  Do this after `trim_to_start` and the entered scores. It cannot clash with the short-handed step, which is fours only.
- **The marks must survive a renumber.** `_swapped` in Python and `swappedOf` in JS read the **document's** `end.roles_swapped`, with any end-level override key layered over it (see the End box section). A charter's `before` move therefore keeps the switch.
- **Parity:** add Python/JS tests on the pattern of `TestShortHandedParity`.
- **Covered without change:** `for_player`, `gameclips.player_rocks`, the report and the hack call, since all of them read the re-credited fields.
- **Not changed:** the thinking report (`refresh_thinking`, `src=None`), which is per team.

### API
- **`PUT /api/me/plays/{source_id}`** takes an optional `switched_ends`: a list of end identities for the colour being claimed.
  - Present, it replaces that colour's marks; an empty list clears them. Absent, they are left as they are.
  - The marks travel with the player's own claim on that colour, so "a claim on the team" holds by construction.
  - **422** for:
    - a fours game;
    - the game's first end;
    - an end the game does not have, checked against the document the saver is looking at (the chart's when `path` is a chart, otherwise the `/g/` document);
    - entries that are not distinct;
    - non-integers.
  - **No live gate.** A live game can be marked like any other; the accepted limitation covers a re-cut. *(My call, replacing the first draft's "once the game is over".)*
  - The play rate limit already covers it. Log the change with the user, as team names are.
- **A route for the chart End box**, `POST /api/games/{source_id}/switched-ends`, taking one end's tick as `{path, color, end, on}`.
  - The tick is applied to what the game holds. Two quick ticks therefore both land, and a mark this chart does not show stands. (Built first with the whole list; the final review changed it.)
  - A chart whose game was merged into another gets 409, from this route and from the play route: "this chart's game is now part of another: mark switched ends on that game's page". It is allowed for a signed-in user who may edit a chart of this game, using the chart write rules ([2026-10-15-chart-write-protection-design.md](2026-10-15-chart-write-protection-design.md)). A charter need not be a player. *(My call.)*
- **Clips.** Saving a play asks for clips of the saver's rocks. The partner's clips follow the next time their report opens: `ask_for_clips` asks for anything missing. The builder confirms that a clip of a rock that is no longer the partner's is not shown to them.

### Viewer: "I played…"
Board A is the reference for layout and copy. For a swappable format:
- **The checkbox.** **We switched during the game** sits under the Player A / Player B buttons. It is ticked when the chosen team already has switched ends in the served document, for example after the partner marked them.
- **The end grid.** Ticked, it opens a grid of the game's ends, four to a row on a phone.
  - Each chip shows "End N" and this player's rocks in it ("1st & 5th" or "2nd–4th"), worked out from their Player A/B pick.
  - The first end is shown but cannot be tapped.
  - A switched end has the amber outline.
- **Slot buttons** read "End 1: 1st & 5th" and "End 1: 2nd–4th" while the box is ticked.
- **Picking the other team** shows that team's marks.
- **Save.**
  - A ticked box sends the chosen team's switched ends.
  - Unticking and saving sends an empty list, which clears them for both partners.
  - Untouched, the box sends nothing.
- **The note line** says that the partner gets the other rocks in every end, and that saving marks the game for both of them.
- **After a save,** the page shows the re-credited labels without a manual reload. The builder chooses whether that is a refetch or a local patch.
- **What the dialog needs.** It needs the game's ends (identity and displayed number) and the current marks. Today App passes the dialog a snapshot (`ui.playing`), which will have to carry them.

### Viewer: the chart's End box
- **On the hosted site,** `EndBox` reads the document's `end.roles_swapped` and writes through the End box route, not the chart's override map.
- **Wording:** "Red switched this end: B threw 1st & 5th". *(My call; the builder may improve it.)*
- **Who sees the checkboxes:** the same people as today, those who may edit this chart. There is none in the game's first end.
- **Where no one is signed in** *(my calls, from the builder check)*:
  - **A chart nobody owns, edited by link while signed out:** the checkboxes stay. Ticking one while signed out changes nothing, and shows "Sign in to mark switched ends" with a Sign in button. Asking up front would load sign-in on every view of such a chart, which the page avoids (`authLater`).
  - **The local `curling-score serve` viewer**, which has no Source and no API, keeps today's behaviour. It writes end-level chart overrides to `overrides.json`, and `analyze.write` bakes them in.
  - **Accounts switched off:** the same as the local viewer.
  - So `apply_overrides` and `layout` **keep honouring end-level override keys**, layered over the document's marks. An override can add a switch to an end. Only `true` counts, as today, so it never removes a switch held on the game. Their parity tests stay. On the hosted site nothing writes them once the migration below has run, so each end has one answer.

### Existing chart-level swaps
- **First, count them:** chart overrides in production with a `roles_swapped` end key.
- **If there are any,** a one-off admin script copies each onto its game's canonical `switched_ends` and removes it from the chart. *(My calls.)*
  - Only charts on the source's current run are copied. Swaps on charts pinned to other runs are reported and left in place.
  - Where the game already has marks for that colour that disagree, the script reports the conflict and changes nothing.
  - **A first-end swap is normalised.** The script stores that team's complement, every other end not swapped on the chart, and lists the plays saved from that chart for the colour. Those players' slot now names the other person, so the admin can tell them.
- **If there are none,** there is nothing to migrate. The hosted End box simply stops writing override keys.

## Non-goals
- Fours: a three-player team that was short for part of a game stays out of scope.
- Detecting the switch from the video. The face-ID spike could feed this later.
- Telling the partner about a change.
- A history view of who set what. `by` and `at` are kept for the admin.
- Marking the other team's ends from your own claim.
- Changing switches from the My shots page, whose "I played…" changes only colour and slot.

## Acceptance
- **Python:**
  - `game_doc` applies the marks by end identity on a trimmed game;
  - `apply_overrides` with a `before` move keeps them;
  - `for_player` returns each partner's own rocks in switched and unswitched ends;
  - the API validates: fours rejected, unknown ends rejected, absent and empty treated differently;
  - the End box route rejects someone who may not edit a chart of the game;
  - both routes reject the first end;
  - the migration normalises a first-end swap.
- **JS:** parity of `layout` and `renumber` with document-held `roles_swapped` against Python.
- **Firestore:** the contract test on the emulator.
- **Browser:** a headless phone check of the dialog at 390×844, signed in with the fake Firebase SDK. Tick, tap ends 4–7, save, reopen as the partner and see the marks.
- **Real game, on a local dev server only** (`devserve` with a copy of the example game). The example game's players are real, and ends 4–7 are illustrative, not what they did. So nothing is marked on production as a test. *(My call, from the builder check.)* Mark Red's ends 4–7 as switched.
  - Red's Player A's My shots then shows 2 rocks in each of ends 1–3 and 3 in each of ends 4–7. Player B's shows the reverse.
  - The report and the hack call agree.

## Left to the builder
- The exact `switched_ends` field shape.
- The End box route's name.
- How the page refreshes after a save.
- The End box wording.
- Splitting the work into phases.
