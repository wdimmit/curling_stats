# Charting on an iPad: the portrait layout

**Status:** design agreed on 2026-10-16.
- The user picked the portrait board, P ("I like the portrait layout").
- The user confirmed the playback below ("Looks good").
- The user limited it to portrait: "Lets just support portrait for now".
- After the builder check, the user ruled that ✔ Charted still jumps to the next rock needing charting: "It should jump".

Marked **(my call)** throughout: choices I made that the user has not ruled on. They are listed together at the end.

**Mockups:** [Charting on an iPad](https://claude.ai/artifact/7XofqtMe3if5htn3J5fhF8).
- The board "P · Portrait" is the layout.
- "A · the house opened large" shows the open house, drawn on the landscape board. Here it opens under the video instead; see §4.
- "Today · portrait" is the current page.
- The landscape boards A and B were not chosen.

The boards are the real `/c/` page at 820×1080 with touch emulation. React is forced into its wide branch, and the layout is injected as CSS, with a real video frame. The data is s_0cYt (10/14, sheet 2, Wednesday Womens), end 2 rock 4. The kit is in `out/mockups/ipad/` (gitignored):
- `mock/run.mjs` renders a board;
- `mock/touch.js` holds the touch styles every board shares;
- `mock/P.js` is this layout.

## Context
The user wants to chart away from the club, from recorded games or live streams, holding the iPad however suits.

**Their loop for each rock:**
1. the video, for the shot's intent and outcome;
2. the house map and the listed weight;
3. mark the shot type and the score.

**Today, on a portrait iPad** (820×1080 is an iPad Air or 10th gen in Safari with its bars):
- the page is the old one-column layout, 3,503 px tall;
- the house starts at 1,359 px;
- shot type and score start at 2,731 px, two and a half screens down.

Landscape (1180×740) is better, but shot type still starts at 1,387 px.

At both sizes, 43 controls are under 32 px tall, among them the rock chips (30×30), the score buttons, and the type buttons (about 28 px). The figures' notes, the house's hint and several button explanations exist only as hover tooltips. A finger cannot hover.

**Success:** on a portrait iPad, charting a rock needs no scroll and no hover:
- the video, the rock chips, the house, the seven figures, shot type and score are all on one screen;
- every control is at least 44 px;
- nothing important is only in a tooltip.

## Decisions
- **Portrait touch screens only.** The gate is
  `TABLET_QUERY = "(any-pointer: coarse) and (orientation: portrait) and (min-width: 700px) and (max-width: 1199px)"`.
  It lives in `frontend/core/constants.mjs` beside `PHONE_QUERY` and `WIDE_QUERY`. The stylesheet mirrors it byte for byte, and a test asserts the match, as it does for the other two.
  - `any-pointer`, not `pointer`, so that an iPad with a trackpad or mouse attached still counts **(my call)**.
  - It covers every iPad in portrait in Safari (744–1024 wide). iPad Safari is the target.
  - Large Android tablets match too, but they are not tested **(my call)**. An Android browser that shrinks the layout for its keyboard can flip the page to landscape while someone types in a sheet. That would swap the layout and close the sheet. This is not guarded against.
  - A portrait-shaped desktop browser window on a touch-screen laptop also matches. That is accepted: the layout works with a mouse.
  - Nothing else changes:
    - phones (≤640 px; `PHONE_QUERY` is unchanged);
    - widths of 641–699 px;
    - every landscape window, including an iPad turned sideways, which keeps today's layout (user: portrait only, for now);
    - desktops without touch at any size;
    - the wide layout (≥1700 px, which a portrait window under 1200 px wide never reaches).
- **Turning the iPad** crosses the gate. That swaps the layout the way crossing 1700 px does on a desktop:
  - React renders one layout or the other;
  - the video iframe is never reparented;
  - typing less than 150 ms old can be lost (accepted for the wide layout, and here too).
- **All three surfaces:**
  - `/c/` charts.
  - `/s/` shows the bar inert, as the chart panel is today.
  - `/g/` hides the bar. The space goes to the house and figures row.
- **No new data:** no pipeline, timeline or schema change.

## Design

### 1. The page (820×1080, the board)
`<main>` is a grid, two equal columns:

| | |
|---|---|
| the video (both columns) | |
| the rock chips and Replay (both columns) | |
| the house card | the figures card |
| the charting bar (both columns) | |

- The page is `100dvh` tall, never `100vh`: Safari's toolbars change the visible height.
- No page scroll at these sizes:
  - 820×1080 (iPad Air / 10th gen);
  - 744×1050 (mini);
  - 834×1110 (Pro 11);
  - 1024×1290 (Pro 13).

  The board measured at 820×1080:
  - video 732×412;
  - house card 389×320;
  - figures card 389×287;
  - the bar ends at 1066.
- **The video** is as wide as the column allows, but only as tall as the screen leaves once everything under it has its room. The plan does this with grid rows that share the height, rather than a fixed allowance. The video keeps 16:9: if its width is capped, the height follows the width, not the other way round. The mock's Pro 13 render showed a stretched box when the height was set alone.

### 2. The header, one line (56 px)
- **In the header:**
  - the site name;
  - the rock's chip and label ("2nd end, lead's second rock"), which gives way first and ends in "…";
  - the End select;
  - the charting pill ("all charted ✓" / "N to chart →");
  - Thinking;
  - Report;
  - ⋯.
- **Behind ⋯,** a dropdown like the phone's:
  - Game;
  - Copy link and View-only link;
  - ⬇;
  - ⚑ Flag, I played…, Teams… and Clip…;
  - YouTube →;
  - the playback settings (play on jump, lead-in).

  The source line (sheet · video id) is hidden, as on the phone **(my call; it could go in ⋯)**.
- `#save` shows only its failures ("not saved — use ⬇", "reloaded…"), as on the phone. Those are the states that must not be missed.
- Header buttons are 44 px tall.

### 3. Rock chips and Replay
- One row: the end's chips, then ↻ Replay.
  - The chips are 44 px tall. Their width shares what the row leaves, down to 36 px; at 820 they are 38 px.
  - The selected chip keeps its outline, and a graded chip keeps its dot (6 px here).
- No ←/→ buttons **(my call)**: a chip is one tap, and the bar's Next rock moves on. The arrow keys still work with a keyboard attached.
- No ✔ Mark charted here. It moves to the house (§4), because it confirms the house state.

### 4. The house card
- **In the card:** the house, drawn to fit (the SVG letterboxes its viewBox, as in the wide layout), with:
  - **✔ Charted** at the top left, on `/c/` only. This is today's Mark charted: it sets `state_known` and jumps to the next rock whose house needs charting. The user confirmed the jump on 2026-10-16 ("It should jump"). So, as on the desktop, a blank rock is graded before ✔ Charted, or later from its chip.
  - **⤢** at the top right, which opens the house large.
  - The hint "Tap the house to open it large and move stones." under it.
- **The tools are not in the card:** the swatches, ⌫, ◎, track and Reset.
- **Opening it large:** tapping the house, or ⤢, opens it. This reuses the phone's editor state, `houseMode: "edit"`; the phone already turns a tap on the house into "open the editor" (`House.jsx`). Open, it covers everything under the video, the full width down to the bottom of the page: about 790×570 px at 820×1080.
  - The video stays where it is and keeps playing **(my call)**. Its overhead panels show where the stones ended up while they are placed.
  - **The house takes that area's full height, uncropped** (the whole `houseViewBox`, so guards in front of the house can still be placed): about 330×546 at 820×1080, twice the card's house.
  - **The tools go in a column to its right, in the width the house leaves** **(my call)**. A row under the house would cost it height (the reviewer measured about 1.4× that way).
    - At the top of the column: ✔ Charted, and "Drag a stone · tap the ice to add one".
    - ✔ Charted jumps here too. The house stays open on the rock it jumps to, which needs its stones placed as well. If no rock is left to chart, it closes **(my call)**.
    - Then, at touch size: red, yellow, ⌫, ◎, track and Reset.
    - At the bottom: ⇄ and **Done**.
  - Pointer mapping already goes through `getScreenCTM`, so placing stays exact at the larger size.
  - The phone's crop rules (`shouldCrop`) are not applied here.
- **On `/s/` and `/g/`:**
  - the house is a picture, with no Charted, no ⤢ and no editor;
  - the track toggle, the one tool that is a viewing preference, goes in ⋯ there **(my call)**.

### 5. The figures card
- **The figures:** the seven of the wide layout (`FiguresCard`) in two columns:
  - At the broom;
  - Hack;
  - At the hog line;
  - Weight;
  - Rotation;
  - Curl;
  - Came to rest.

  Values are 18 px.
- **Each note wraps in full**, never cut off and never ending in "…" with a tooltip, because a finger cannot hover. Most notes take one or two lines at 820; a few take three. One example is a doubles rock with no broom, whose curl note joins two notes (70–80 characters). The wording is unchanged, because `core/line.mjs` also feeds the phone and the desktop.
- **The eighth cell is "Line & speed".** It opens the delivery dialog (§7). The line chart column of the wide layout is not drawn here.
- The caption ("Figures ±4 in · wide = the side away from the curl") is not in the card **(my call)**. The large view keeps its own captions.
- **Above the figures, only when they apply** (as in the wide layout):
  - the warnings (`#flags`);
  - the doubles end box.

  The house and figures row is at least as tall as the figures card. The video gives up height to make room, down to 560 px wide. Below that the page scrolls rather than overlapping cards **(my call)**.
  - **Warnings** are rare, and on those rocks the warning is the thing to read.
  - **The end box shows on every doubles rock** (`format.swappable`), so on the tablet it is one compact line **(my call)**:
    - "End 3 · Red has hammer · power play, red, left";
    - then, on `/c/`, a **Swapped roles…** button. It opens a sheet (§6) with the two swap checkboxes, and reads "Swapped: Red" when one is set.
  - The desktop and phone end boxes are unchanged.
- **On a never-seen rock:** the one reason line, as the wide layout shows (R16).

### 6. The charting bar, along the bottom
A card the width of the page, in three rows. Every control keeps its id, handler and keyboard behaviour (`GradingBar`).
1. **The groups:** Draw, Guard, Hit, Other (44 px tall, at least 70 wide), with the auto/confidence note at the right.
2. **The group's subtypes.** At 820 the longest, Hit's ten, fit on one line. At 744 they may wrap to a second line, and the plan measures that.
3. **The score row:**
   - 0, 1, 2, 3, 4 and —, each 50×48, figures at 20 px;
   - then **Miss…**, **Note…** and, on `/c/`, **Order…**;
   - then **Next rock →** at the right, filled, the row's main action.

**The sheets.** Miss…, Note…, Order… and the doubles Swapped roles… each open a small sheet: a native `<dialog>`, like the others. Once set, a button shows its value, for example "Miss: wide".
- **Placement:** each sheet opens at the top of the screen, over the video **(my call)**. The iPad's keyboard comes up to about y = 700–770 of 1080, so a sheet placed there is never under it. Safari has no reason to pan the page either, so the house and the bar stay where they are.
- **Miss:** the usual reasons (today's `#missList` options) as buttons, plus a text box.
- **Note:** a text area.
- **Order:** the move-before select, with the explanation under it that the wide layout puts in its tooltip.

Saving is unchanged (`useTypedField`). The on-screen keyboard appears only when a box is tapped. The rocks' warnings say "move it under Order", so Order stays reachable here rather than going behind ⋯.

**Next rock →** goes to the next rock in the end, then to the first rock of the next end. It is disabled on the game's last rock **(my call)**. It does not grade or mark anything: grading a rock does not move on today, and still doesn't.

**On `/s/`:** the type and score buttons are inert, as the chart panel is today, and Next rock still moves. Miss… and Note… open their sheets read-only, so a long note can be read in full **(my call)**. Order… and Swapped roles… are not shown.

**On `/g/`:** the bar is hidden, as today (`body[data-mode="review"] #grading`).

### 7. Thinking and the delivery, opened large
Both open as in the wide layout: Thinking from the header, the delivery from "Line & speed". Both are rendered on the tablet too, not only when wide.
- **Thinking:** at 820 it already fits and reads well (checked: 756 px wide).
- **Delivery:** at 820 the dialog is 756 px wide. `BIGDELIVERYBOX`'s 1000-unit viewBox then draws its labels at about 7 px. On the tablet, the large delivery and its speed chart are drawn for the dialog's width, so chart text is at least 11 px. The plan picks how: a box per width, or a narrower viewBox.
- **Close hint:** on touch it reads "Tap outside to close", not "Esc or click outside to close". ✕ is 44 px.
- **Shortcuts:** global shortcuts stay off while a dialog or sheet is open (`dialog[open]`).

### 8. Touch, everywhere in this layout
- Every control is at least 44 px. The headless check (Testing) counts any under 40 px, as the mock's measure did. The 0 it found on the board is the target.
- Controls get `touch-action: manipulation`, so a double tap doesn't zoom and a tap has no delay.
- No meaning is only in a `title`. Tooltips stay as a desktop extra and cost nothing.

**Constraints the implementation must keep:**
- The video iframe is never reparented (`runtime/player.mjs`).
- **The phone layouts render the same DOM as now:** domsnap of the 390×844 states is identical before and after.
- **The 1280×900 desktop domsnap states are identical too.**
- **The wide layout is unchanged:** `scripts/deskcheck.mjs` passes as it does now.
- **React renders one layout:**
  - the tablet takes the wide branch's components (the bar, the figures card, the header's rock name and Thinking, both dialogs), without `LineCard`, plus the pieces above;
  - every id is in the page once, and drawings carry no ids;
  - `runtime/wide.mjs` gets a sibling for `TABLET_QUERY`;
  - App.jsx's resize handler resets `houseMode` only when the layout really changes, so it no longer assumes that the editor exists only on a phone.

## Testing
- **Node:**
  - `TABLET_QUERY` matches the stylesheet byte for byte.
  - Next rock's target across ends and at the game's end (a pure function in `core/`).
  - The sheets' value labels.
- **Headless tablet check.** Extend `scripts/deskcheck.mjs`, or write a `scripts/tabletcheck.mjs` in its style. It runs against `devserve.py` with touch emulation on (`Emulation.setTouchEmulationEnabled`, which makes `any-pointer: coarse` match).
  - At 820×1080, 744×1050, 834×1110 and 1024×1290, on `/c/`, `/s/` and `/g/`:
    - no vertical or horizontal scroll;
    - every card above the fold;
    - no visible control under 40 px.
  - It also checks:
    - tapping the house opens it large, and Done closes it;
    - Miss…, Note… and Order… open their sheets, and keys stay out while one is open;
    - Next rock crosses an end;
    - Line & speed and Thinking open their dialogs.
  - **On blanks:** a blank rock (warnings showing) and a doubles rock (the end box) don't overlap cards, and their video is at least 560 px wide.
- **What must not change:**
  - At 1180×820 with touch (an iPad turned sideways), today's layout: domsnap against a base snapshot.
  - At 820×1080 without touch, today's one-column layout.
  - Phone and desktop domsnap identical, and deskcheck passes.
- **On a real iPad,** by the user, since iOS Safari can't be run here:
  - `100dvh` with Safari's bars;
  - YouTube's first play: it may need one tap on the video before the page can seek and play;
  - the sheets with the on-screen keyboard;
  - tapping a dialog's backdrop;
  - rotating to landscape and back mid-rock.

## Out of scope
- **Landscape on an iPad** (the user, for now). The landscape boards A and B stay on the canvas if that changes.
- **The full-height line chart** on the tablet: it is behind "Line & speed".
- **Laptop widths without touch:** they are unchanged.
- **iPad Split View and Slide Over.**
  - Narrow windows fall under the phone shell or today's layout.
  - Only windows of 700 px and wider in portrait get this layout.

## My calls, for checking
1. The gate uses `any-pointer: coarse`, so a portrait touch-screen laptop window matches too.
2. The source line is hidden, as on the phone, not moved into ⋯.
3. No ←/→ buttons, with Next rock instead. Next rock crosses into the next end.
4. ✔ Charted moves from the control row to the house card. It still jumps (the user's answer). Opened large, the house stays open on the rock it jumps to.
5. The house's tools appear only when it is opened large. The track toggle goes in ⋯ on the view-only surfaces.
6. Opened large, the house keeps the video visible above it. It is uncropped, at the area's full height, with its tools in a column beside it.
7. Figure notes wrap in full and are never cut. The figures caption is dropped from the card.
8. Miss, Note, Order and Swapped roles become buttons that open small sheets at the top of the screen. On `/s/`, Miss and Note open read-only.
9. On the tablet the doubles end box is one line, and the swap checkboxes are in a sheet.
10. When warnings need room, the video shrinks to 560 px wide before the page scrolls.
11. Android tablets get the layout but are not tested. Their keyboard can flip the page to landscape.
