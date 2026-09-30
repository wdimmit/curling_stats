# slots — scoreboard card slots, labelled card or blank

Every card slot of every readable board read, sampled every 300 s over ten
videos: the five 2026-09-28 Monday Open League VODs (1xmDI5EkwOU, VVgLH4uBxYs,
VTldBAiftKk, orvBvuFdLgk, LVpNIsWstHA) and the five 2026-09-29 Tuesday Super
League live recordings (MVnwn2R8Rt8, UIHi7VJySg0, 4l60dFdwgVo, w_aFsB4HwUE,
5CVNnER02aM). 415 board reads, 11,620 slots. For `game/slotmodel.py`, which
decides whether a card hangs in a slot; the digit on it is still `digits`'.

| file | what |
| --- | --- |
| `slots.jsonl` | one row per slot: `id`, video, time, row, slot, the old threshold rule's verdict (`old`), its statistics (`bright`, `dark8`..`dark2`, `b95`/`b98`/`bmax`, `ink`, `ink20`, window `wb98`/`wink`) and the digit model's read (`digit`, `conf`) |
| `windows.npz` | each slot's raw `scoreboard.card_window` (grayscale): `ids`, `shapes`, `pixels` flat — see `scripts/slots/train.py:_windows` |
| `labels-auto.json` | the automatic label per slot: card, blank or ask (`scripts/slots/review.py:auto_label`) |
| `edits/slots-*.json` | the review sessions (2026-09-30), `{"labels": {id: card/blank/skip}, "reviewed": [...]}`; the latest wins |

Board reads are the median of the keyframes within 20 s (as `board_states` and
`read_cards_at` read), so passers-by show as ghosts and a card hung or taken
down inside the 40 s shows faint — both are in the set on purpose. A card
partly covered is still a card; a person, arm or head with no card, glare and
the board's own edges are blank.

**Review (2026-09-30):** 955 slots seen — the 350 ask tiles (835 reads, one
decision per unchanged run of a slot) and a 60 + 60 spot-check of the
automatic labels. 24 of the 80 slots the old rule or a confident digit called a
card were people or edges; 6 guessed blank were faint cards; 1 of 60
automatic cards was the banner's lettering read as a "1" at 1.0.

```bash
# harvest (on the worker, in a throwaway curling-worker:local container, main's src on PYTHONPATH)
python scripts/slots/survey.py          # writes /s/slots.jsonl and /s/win/
# review: builds the page and serves it on 127.0.0.1:8779
python scripts/slots/review.py <survey-dir>
# train, leave-one-video-out, export src/curling_score/game/slot_weights.npz
PYTHONPATH=$PWD/src .venv/bin/python scripts/slots/train.py datasets/slots
```
