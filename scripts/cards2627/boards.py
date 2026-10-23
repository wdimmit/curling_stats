"""Replay each game's board read under the shipped and the retrained reader.

    python scripts/cards2627/boards.py <harvest-dir> <manifest.json> <gate2627-reads.json>

Production walks back from a minute before the game's end and keeps the first
board state `per_end_from_cards` accepts (`read_game_board`). This does the same
over the harvested reads -- every `step` seconds, latest first -- with each
arm's held-out digits, and scores what it would have published against the
reviewed labels (`label.final_labels`): ends read, ends read wrong, games with
no usable board at all. The truth is the board every labelled card of the game
makes, not any one read of it -- a single read can miss a card a person stands
in front of -- and a game with a card nobody has labelled has no truth.
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import label as L                                     # noqa: E402

from curling_score.game import scoreboard as SB       # noqa: E402


def boards_by_game(root):
    reads = L.load_reads(root)
    games = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in reads:
        games[(r["source_id"], r["game_index"])][r["t_s"]].append(r)
    return games


def board_from(reads, digits, truth=None):
    cards = {"yellow": [], "red": []}
    for r in sorted(reads, key=lambda r: r["slot"]):
        if truth is not None:
            end = truth.get(r["card"])
            if end == "x":
                continue
            conf = 1.0
        else:
            d, conf = digits.get(r["id"], (None, 0.0))
            end = d if d is not None and conf >= SB.MIN_CONFIDENCE else None
        cards[r["color"]].append(SB.Card(slot=r["slot"], end=end, confidence=conf))
    return SB.CardBoard(yellow=tuple(cards["yellow"]), red=tuple(cards["red"]))


def truth_board(cards, labels):
    out = {"yellow": [], "red": []}
    for c in sorted(cards, key=lambda c: c["slot"]):
        if c["auto"] == "transient" and c["id"] not in labels:
            continue
        if c["id"] not in labels:
            return None
        if labels[c["id"]] == "x":
            continue
        out[c["color"]].append(SB.Card(slot=c["slot"], end=int(labels[c["id"]]), confidence=1.0))
    return SB.CardBoard(yellow=tuple(out["yellow"]), red=tuple(out["red"]))


def walk(times, n_ends, make):
    for t in sorted(times, reverse=True):
        board = make(t)
        if board is None or board.is_blank():
            continue
        try:
            return t, SB.per_end_from_cards(board, n_ends)
        except SB.ScoreboardError:
            continue
    return None, None


def main():
    root, manifest, reads_path = sys.argv[1:4]
    games_meta = {(g["source_id"], g["game_index"]): g for g in json.load(open(manifest))}
    cards, labels, _bad = L.final_labels(root)
    card_of, game_cards = {}, collections.defaultdict(list)
    for c in cards:
        game_cards[(c["source_id"], c["game_index"])].append(c)
        for rid in c["reads"]:
            card_of[rid] = c["id"]
    arms = {a: {k: tuple(v) for k, v in d.items()}
            for a, d in json.load(open(reads_path)).items()}
    games = boards_by_game(root)
    tally = {a: collections.Counter() for a in arms}
    for key, by_t in games.items():
        n_ends = len(games_meta[key]["ends"])
        for rs in by_t.values():
            for r in rs:
                r["card"] = card_of.get(r["id"])
        truth = None
        board = truth_board(game_cards[key], labels)
        if board is not None and not board.is_blank():
            try:
                truth = SB.per_end_from_cards(board, n_ends)
            except SB.ScoreboardError:
                truth = None
        for a, digits in arms.items():
            if not all(r["id"] in digits for rs in by_t.values() for r in rs):
                continue
            c = tally[a]
            c["games"] += 1
            _t, got = walk(by_t.keys(), n_ends, lambda t: board_from(by_t[t], digits))
            if got is None:
                c["no_board"] += 1
                continue
            c["ends_read"] += len(got.per_end)
            c["ends_unread"] += len(got.unread_ends)
            if not got.unread_ends:
                c["every_end"] += 1
            if not [e for e in got.unread_ends if e < n_ends]:
                c["all_but_last"] += 1
            if truth is not None:
                for e, s in got.per_end.items():
                    if e in truth.per_end:
                        c["ends_checked"] += 1
                        c["ends_wrong"] += truth.per_end[e] != s
    for a, c in tally.items():
        print(f"{a:>20}: games {c['games']}, no usable board {c['no_board']}, "
              f"every end read {c['every_end']} (all but the last {c['all_but_last']}), ends read {c['ends_read']} "
              f"(unread {c['ends_unread']}), wrong {c['ends_wrong']}/{c['ends_checked']}")


if __name__ == "__main__":
    main()
