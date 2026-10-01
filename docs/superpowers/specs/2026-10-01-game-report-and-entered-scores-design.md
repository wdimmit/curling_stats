# Game report redesign, then entered scores

**Status:** design approved in chat on 2026-10-01, in two sections (Phase 1,
then Phase 2 with storage approach A). Nothing is built. Mockups: the "Game
Report Readability" canvas, https://claude.ai/artifact/GbhLQgTmv5JQetLKaaBWpW.
Its boards B (desktop), C (print) and E (phone) are the target. Their sources
and the script that drew them from real data are in `out/mockups/report/`.

## Context

The game report (the **Report** button, `frontend/viewer/Report.jsx`) is hard
to read. On Dimmit v Grant (`/c/2pVlP9b1ZHjadsYuuK8I53`, Tuesday Super League,
sheet 3, 2026-09-29):

- The page is 2,250 px tall on desktop and 3,400 px on a phone. Team
  percentage and thinking time sit at the very bottom, and two long amber
  warnings come first.
- It shows no score at all, though the wall-board read is in the timeline.
- Each team gets four small tables whose columns don't line up, so the same
  position can't be compared across teams. Rows such as "Draw › Draw" repeat
  the group, and the `avg` column is the percentage divided by 25.
- The thinking chart fills about 60% of its card, with 10 px labels and no
  team names on the lines.
- Defects: on a phone the rule `h1, #src {display:none}` also hides the
  report's title. The team-colour dots are CSS backgrounds, so they vanish in
  print. Print runs to three pages, and page 1 holds only the chart, because
  `.card {break-inside:avoid}` pushes each tall team card down.

The user liked the proposed by-end scoreboard and added a requirement: if the
report shows a scoreboard, **any signed-in user must be able to fill in an
end's score where none was detected.**

**Success:**
- On this game, the report fits a 1280 px screen in about the height of the
  mockup and prints on one letter page. Lead reads 63% v 83%, end 4 reads "?",
  and the longest think is 1:28 (Grant skip, end 1).
- After Phase 2, a signed-in person picks "Grant 3" for end 4. Every chart
  of the game then shows end 4 as an entered 3, with totals of 4 and 5.

## Sequencing

One spec, two phases, each shipped and checked on its own (the user's choice):

1. **Phase 1:** the report redesign. Frontend, plus two fields in the chart
   document. Unread ends show "?".
2. **Phase 2:** entered scores. A field on the game, one API route, the
   serving merge, and entry from the "?" cells.

## Phase 1: the report redesign (approved)

### Layout

Top to bottom, as on canvas board B:

1. **Title block.** League, sheet and date; "Dimmit v Grant" with team
   swatches; a coverage pill ("48 of 64 rocks graded · end 1 still to grade →").
2. **By end** (left) and **By position / By shot type** (right) on desktop,
   stacked on a phone.
   - *By end* has three blocks with the ends as columns and a Total column:
     **Score** (with a hammer dot), **Shooting** (the better figure of the two
     in bold) and **Thinking time**. Under it are a key and "About these
     numbers", which replaces the two amber banners.
   - *By position* compares the two teams' percentage for each position and
     the team, as mirrored bars. *By shot type* does the same for Draw, Guard
     and Hit, with a rock count under each figure.
3. **Thinking time**, full width: the running-total chart and the per-rock
   bars, plus a "Longest thinks" list of five.
4. **Detail.** One position × shot-type table per team. Columns are the
   positions plus All, and each cell is "33% 3/5" (percentage, then rocks
   graded, of rocks thrown when some were not graded). Group rows are always
   shown. A group's type rows appear only when it has more than one type, so
   there is no "Draw › Draw". There is no `avg` column.

### Pieces

- **`frontend/core/report.mjs` (new).** It works out everything the report
  prints from the game view (`buildGameView`). It has no React, so node can
  test it like `stats.mjs`. Its exports:
  - `byEnd(view)`: per end, the score, hammer and source (board, unread,
    withheld or not readable), each team's shooting (graded rocks only) and
    thinking, plus the totals.
  - `headToHead(stats)`: the position and team rows, and the Draw, Guard and
    Hit rows.
  - `detailRows(view, colour)`: the matrix for one team.
  - `longestThinks(series, n)`.
  - `coverage(view)`: rocks graded and thrown, the ends with nothing graded,
    and the first ungraded rock.
  - Percentages keep `pct`'s rule: graded rocks only, rounded half up, the way
    `toFixed(0)` rounds.
- **`frontend/viewer/Report.jsx`, rewritten** as `ByEnd`, `HeadToHead`,
  `Thinking`, `Detail` and `Notes`. It reads `report.mjs` and holds no
  arithmetic itself.
- **`frontend/viewer/Charts.jsx`** gains report variants of the two charts.
  They take their width from the card (a `ResizeObserver`) and draw at 1:1
  px, so labels stay 13 px at any width. They add:
  - team names and totals at the line ends;
  - "End n" labels;
  - a labelled median;
  - labels on the three longest bars, skipping a bar next to one already
    labelled.

  The sidebar clock keeps today's scaled charts. Geometry stays in
  `core/charts.mjs`, which takes the box as it does now.
- **`chart_doc`** (`service/api.py`) adds `played_at` and `source_id` to
  `doc.chart`. The date fills the title block, and Phase 2 sends scores to
  `source_id`. Neither is secret: `/api/games` already lists both.
- **CSS** (`src/curling_score/viewer/style.css`):
  - The report section is rewritten. Source Sans 3 is used for `#report`
    only, with tabular figures, as on the games page. It is loaded with the
    viewer's existing font link.
  - The phone rule that hides `h1` is narrowed to the header's own title.
  - Team colours are drawn as inline SVG, which prints, not as CSS
    backgrounds.
- **Header.** While the report is open, the Report button reads
  "Close report", and Print moves into the report's own header row.

### Behaviour

- **By-end table:**
  - "?" marks an end the board didn't read, and "—" a shooting cell with
    nothing graded.
  - The hammer dot comes from `end.hammer`.
  - If the chart predates board reading (`boardReadable(doc)` is false), or
    the board's scores were withheld, the Score rows say so in today's
    sidebar wording rather than showing "?".
- **Coverage pill.** It jumps to the first ungraded rock, through the same
  path the queue uses, and is hidden once every rock is graded.
- **Thinking.**
  - A bar or a "Longest thinks" row opens that rock, through
    `actions.goToBarFromReport`.
  - With no measured thinking the card is left out, as today.
- **Formats.** Positions and roles come from `view.format`, so doubles gets
  two positions and the swap labels the old `TeamCard` used.
- **Many ends.**
  - On a phone, the by-end table scrolls sideways inside its card, with the
    team column pinned.
  - Past five ends, the chart's end labels drop the word "End".
- **Live games.** The report covers the ends played so far.
- **Print.**
  - Colours are forced to the light tokens under `@media print`, whatever the
    device's scheme, and the report fits one letter page.
  - The thinking bars, "Longest thinks" and the detail's type rows appear on
    screen only. The detail prints group rows only, as on board C.

### Testing

- Node unit tests (`tests/test_viewer_js.py`, through `tests/js/singleton.mjs`)
  for `report.mjs`:
  - against this game's timeline and overrides: Lead 63% v 83%, Second
    79% v 63%, team 61% v 70%, end 4 unread, end 1 ungraded, 1:28 longest,
    median 0:21, and Dimmit's Third Draw cell "33% 3/5";
  - a doubles fixture: two positions, and the swap labels;
  - a 10-end fixture;
  - a game with nothing graded, and one with no thinking measured.
- `style.css` rule tests: the phone title is visible, print forces the light
  tokens, and print hides the screen-only parts.
- A headless check (`scripts/cdp.mjs`, as in the phone-check notes): the
  report renders at 1280 and 390 px with no sideways page scroll, and
  `Page.printToPDF` on letter gives one page for this game.

## Phase 2: entered scores (approved, approach A)

### Where they live

**Approach A, on the game, applied when served.** This is how team names
work. The scores reach every chart, view-only link and `/g/` review of the
game, including charts made before, and a reprocess keeps them.

Rejected:
- **Per-chart edits.** Each chart would need them separately, and anyone with
  a chart's edit link can change its edits, signed in or not.
- **Writing into the run's timeline.** The run's document is meant to stay
  as the pipeline left it. A reprocess or a live update would overwrite it,
  and charts pinned to older runs would not see the scores.

### Storage

- `Source.entered_scores`: a map from the game's end number (as a string) to
  `{red, yellow, by, at}`. `by` is the user's id and `at` an ISO time.
- The key is the end number on the wall board's cards. It follows the same
  rules as a card when warm-up ends are trimmed off: it names the end the
  report shows as that number.
- A new repo method, `set_entered_score(source_id, end, entry | None)`, in
  both the memory and Firestore repos. It touches only that end (a Firestore
  field path, `DELETE_FIELD` to clear), so two people filling different ends
  at once can't overwrite each other.

### API

`POST /api/games/{source_id}/scores` with `{end, red, yellow}`, or
`{end, clear: true}`:

- `require_user`. Signed out gets 401.
- 404 for an unknown game. 409 if the game has no finished run, or its run
  predates board reading (schema < 4).
- `end` must be a played end of the game as served (1 to n), or 422. "As
  served" means `game_doc(current run, src, src.play_start_s)`, the view the
  `/g/` review link shows. The same board figure is checked for the 409
  below.
- Exactly one team scores, from 1 to the format's `stones_per_team` (8 in
  fours, 6 in doubles), or 0–0 for a blank end. Anything else gets 422.
- If the board has a figure for that end, the answer is 409, and the board's
  figure comes back in the body.
- The change is logged as team edits are: `scores end %d set to %s on %s by %s`.
- It returns the game's `entered_scores` without `by`.

### Serving

`timeline.apply_entered_scores(doc, entries)` runs in `game_doc` after
`trim_to_start` and the team names, whenever a `Source` is passed:

- It fills only ends whose `score` is null, and sets
  `score_source: "entered"`. The board's figure always wins.
- It then rebuilds the game, the way `trim_to_start` does, through
  `build_game`. That recomputes the running totals and the hammer check.
  Then:
  - `final` is the last running total when every end is known, and the
    board's own `final` otherwise;
  - `scoreboard.unread_ends` drops the filled ends.
- Withheld scores: entries fill those ends too. The withheld note stays,
  because the board's own figures are still unplaced.
- A board never read (`scoreboard` null): entries fill ends on their own.
- Schema < 4: nothing is applied, because those ends hold the detector's
  guesses, which the viewer refuses to show. Entry is not offered.
- Entries for an end number the served game doesn't have are ignored.

### Viewer

- **Signed in** (auth settled through the Flag dialog's on-demand helper,
  moved to a shared `runtime/auth.mjs`): each "?" in the by-end table is a
  `<button>` that opens a small picker. It offers "Blank end", a row of
  1…`stones_per_team` for each team (44 px tall on a phone), and "Clear" on
  an entered end. Entered ends open the same picker.
- **Signed out:** the "?" reads "Sign in to add the score" and links to sign
  in.
- **Until auth answers:** plain "?", no button.
- **After a save,** the chart document is fetched again. The report, the
  sidebar scoreboard and the phone watch view all read `end.score`, so they
  update together.
- **Errors:**
  - 409 fetches again and says "The board has a score for this end now";
  - 401 says "Sign in again to save";
  - anything else says "Couldn't save. Try again."
- **Style.** Entered figures (`score_source === "entered"`) get their own
  style in the report, the sidebar scoreboard and the watch view, and the key
  says "entered by hand". Who entered them is not shown.

### Testing

- `apply_entered_scores` unit tests:
  - it fills only unread ends, and the board wins over an entry;
  - running totals and `final` are recomputed, and `unread_ends` shrinks;
  - withheld scores and a board never read are filled;
  - schema < 4 is untouched;
  - on a trimmed game the key names the displayed end;
  - an unknown end number is ignored.
- API tests (memory repo):
  - signed out gets 401, and bad scores get 422;
  - a board-read end gets 409, with the board's figure;
  - set and clear both work, and two ends can be set independently;
  - the score appears in `/c/…/timeline.json`, `/s/` and `/g/`.
- Node tests for the picker's choices in fours and doubles, and for entered
  cells in `byEnd`.
- A headless check of the picker at 390 px. Saving is checked against the
  API tests, since a headless browser can't sign in.

## Out of scope

- Correcting an end the board read. Entry fills gaps only.
- Showing who entered a score, or a history of changes beyond the log.
- Scores on the games list or the thinking report.
- Entering scores outside the report (sidebar, watch view). Those surfaces
  show entered scores but don't edit them.
