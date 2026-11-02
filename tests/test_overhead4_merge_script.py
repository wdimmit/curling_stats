"""Merging members' player labels into the overhead-3 set, as overhead-4's training data."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m4 = _load("overhead4_merge")

STONES = "0 0.500000 0.400000 0.100000 0.100000\n1 0.200000 0.700000 0.100000 0.100000\n"
PLAYER = "2 0.600000 0.300000 0.200000 0.400000\n"


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.write_bytes if isinstance(text, bytes) else path.write_text)(text)


def base(root: Path, frames: dict[str, str]) -> tuple[Path, Path]:
    """``{"train/a": stone label}`` as images/train/a.jpg and labels/train/a.txt."""
    for rel, label in frames.items():
        _write(root / "images" / f"{rel}.jpg", b"jpeg " + rel.encode())
        _write(root / "labels" / f"{rel}.txt", label)
    return root / "images", root / "labels"


def players(root: Path, rows: dict[str, tuple[str, str]], dataset="overhead-3", fold="all",
            fixes=True) -> Path:
    """``{stem: (item id, label text)}`` as players/labelled/ from labels_export.
    With ``fixes``, each row says it has a stone fix (labels_export's ``stones``),
    which ``fixed`` then writes."""
    lab = root / "labelled"
    lab.mkdir(parents=True, exist_ok=True)
    with open(lab / "players.jsonl", "w") as fh:
        for stem, (item, label) in rows.items():
            _write(lab / "labels" / f"{item}.txt", label)
            _write(lab / "images" / f"{item}.jpg", b"jpeg")
            row = {"id": item, "origin": {"dataset": dataset, "fold": fold, "stem": stem}}
            if fixes:
                row["stones"] = []
            fh.write(json.dumps(row) + "\n")
    return lab


def test_a_merged_label_is_the_stone_lines_plus_the_player_lines(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == STONES + PLAYER
    assert (tmp_path / "out/images/train/a.jpg").read_bytes() == b"jpeg train/a"
    assert got["kept"] == 1


def test_class_2_lines_already_in_the_base_are_replaced(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES + "2 0.1 0.1 0.1 0.1\n"})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == STONES + PLAYER


def test_a_reviewed_frame_with_no_players_keeps_just_its_stones(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", "")})
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == STONES


def test_a_logo_crop_with_an_empty_label_and_no_stones_is_written_empty(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/x_logo1": ""})
    pl = players(tmp_path / "p", {"x_logo1": ("p_1", "")})
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/x_logo1.txt").read_text() == ""


def test_keeps_train_and_val_apart(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "val/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "b": ("p_2", "")})
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/val/b.txt").is_file()
    assert (tmp_path / "out/images/val/b.jpg").is_file()
    yaml = (tmp_path / "out/data.yaml").read_text()
    assert "train: images/train" in yaml and "val: images/val" in yaml


def test_data_yaml_names_the_three_classes(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    yaml = (tmp_path / "out/data.yaml").read_text()
    assert f"path: {(tmp_path / 'out').resolve()}" in yaml
    assert "names:\n  0: red_stone\n  1: yellow_stone\n  2: player\n" in yaml


def test_raises_on_a_frame_with_no_player_label(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    with pytest.raises(ValueError, match="b"):
        m4.merge(bi, bl, pl, tmp_path / "out", set())


def test_pilot_drops_the_unlabelled_frame_and_counts_it(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "pilot", set(), pilot=True)
    assert got["kept"] == 1 and got["unlabelled"] == 1
    assert not (tmp_path / "pilot/images/train/b.jpg").exists()
    assert not (tmp_path / "pilot/labels/train/b.txt").exists()


def test_pilot_refuses_an_overhead_4_path(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    out = tmp_path / "overhead-4" / "folds" / "all"
    with pytest.raises(ValueError, match="overhead-4"):
        m4.merge(bi, bl, pl, out, set(), pilot=True)
    assert not out.exists()


def test_a_full_merge_may_write_to_overhead_4(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    m4.merge(bi, bl, pl, tmp_path / "overhead-4" / "folds" / "all", set())
    assert (tmp_path / "overhead-4/folds/all/data.yaml").is_file()


def test_leave_out_drops_and_counts_a_frame_even_without_a_label(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "out", {"b"})
    assert got["kept"] == 1 and got["left_out"] == 1 and got["unlabelled"] == 0
    assert not (tmp_path / "out/images/train/b.jpg").exists()


def test_leave_out_wins_over_a_label(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "out", {"a"})
    assert got["kept"] == 0 and got["left_out"] == 1


def test_players_are_found_by_stem_not_item_id(tmp_path):
    # the item id says nothing about the frame; two frames, ids in the opposite order
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"b": ("p_1", PLAYER), "a": ("p_2", "")})
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == STONES
    assert (tmp_path / "out/labels/train/b.txt").read_text() == STONES + PLAYER


def test_a_stem_labelled_twice_the_same_is_fine_and_differently_is_refused(tmp_path):
    bi, bl = base(tmp_path / "b", {"val/a": STONES})
    twice = players(tmp_path / "p1", {"a": ("p_1", PLAYER)})
    with open(twice / "players.jsonl", "a") as fh:      # the same frame, published as ds11 val too
        _write(twice / "labels" / "p_9.txt", PLAYER)
        fh.write(json.dumps({"id": "p_9", "origin": {"dataset": "ds11", "fold": "val",
                                                     "stem": "a"}}) + "\n")
    m4.merge(bi, bl, twice, tmp_path / "out", set())
    _write(twice / "labels" / "p_9.txt", "")
    with pytest.raises(ValueError, match="a"):
        m4.merge(bi, bl, twice, tmp_path / "out2", set())


def test_follows_a_symlinked_base_frame(tmp_path):
    # the laptop's folds are symlinks into ds11/set
    real = tmp_path / "real.jpg"
    real.write_bytes(b"jpeg real")
    (tmp_path / "b/images/train").mkdir(parents=True)
    (tmp_path / "b/images/train/a.jpg").symlink_to(real)
    _write(tmp_path / "b/labels/train/a.txt", STONES)
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    m4.merge(tmp_path / "b/images", tmp_path / "b/labels", pl, tmp_path / "out", set())
    out = tmp_path / "out/images/train/a.jpg"
    assert not out.is_symlink() and out.read_bytes() == b"jpeg real"


def hard(root: Path, rows: dict[str, str | None]) -> Path:
    """The box export's agreed/ for frames ``{stem: stone label or None for no row}``."""
    a = root / "agreed"
    (a / "images").mkdir(parents=True, exist_ok=True)
    with open(a / "agreed.jsonl", "w") as fh:
        for i, (stem, label) in enumerate(rows.items()):
            if label is None:
                continue
            _write(a / "images" / f"b_{i}.jpg", b"jpeg hard " + stem.encode())
            _write(a / "labels" / f"b_{i}.txt", label)
            fh.write(json.dumps({"id": f"b_{i}", "origin": {"stem": stem}}) + "\n")
    return a


def test_a_hard_frame_needs_agreed_stones_and_a_player_label(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    # h1 has both; h2 has players but its stones were never agreed; h3 has stones, no players
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h1": ("p_2", PLAYER),
                                  "h2": ("p_3", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "h", {"h1": STONES, "h2": None, "h3": STONES})
    got = m4.merge(bi, bl, pl, tmp_path / "out", set(), hard_stones=agreed)
    assert (tmp_path / "out/labels/train/h1.txt").read_text() == STONES + PLAYER
    assert (tmp_path / "out/images/train/h1.jpg").read_bytes() == b"jpeg hard h1"
    assert not (tmp_path / "out/labels/train/h2.txt").exists()
    assert not (tmp_path / "out/labels/train/h3.txt").exists()
    assert (got["kept"], got["hard"], got["hard_no_stones"], got["hard_no_player"]) == (2, 1, 1, 1)


def test_hard_frames_are_not_taken_without_the_flag(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h1": ("p_2", PLAYER)}, dataset="hard-1")
    got = m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert got["hard"] == 0 and not (tmp_path / "out/labels/train/h1.txt").exists()


def test_a_hard_frame_in_leave_out_is_dropped(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h1": ("p_2", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "h", {"h1": STONES})
    got = m4.merge(bi, bl, pl, tmp_path / "out", {"h1"}, hard_stones=agreed)
    assert got["hard"] == 0 and got["left_out"] == 1


def test_a_hard_stem_that_is_also_a_base_stem_is_refused(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    agreed = hard(tmp_path / "h", {"a": STONES})
    with pytest.raises(ValueError, match="a"):
        m4.merge(bi, bl, pl, tmp_path / "out", set(), hard_stones=agreed)


def test_a_missing_label_file_is_an_error_not_an_empty_label(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    (pl / "labels" / "p_1.txt").unlink()
    with pytest.raises(ValueError, match="p_1"):
        m4.merge(bi, bl, pl, tmp_path / "out", set())


def test_accepts_the_players_folder_above_labelled(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    m4.merge(bi, bl, tmp_path / "p", tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == STONES + PLAYER


def test_main_reads_leave_out_and_prints_counts(tmp_path, capsys):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    (tmp_path / "leave.txt").write_text("b\n\n")
    rc = m4.main(["--base", str(tmp_path / "b"), "--players", str(pl),
                  "--leave-out", str(tmp_path / "leave.txt"), "--out", str(tmp_path / "out")])
    assert rc == 0 and (tmp_path / "out/labels/train/a.txt").is_file()
    assert "left_out" in capsys.readouterr().out


def test_main_reports_an_unlabelled_frame_instead_of_a_traceback(tmp_path, capsys):
    base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {})
    rc = m4.main(["--base", str(tmp_path / "b"), "--players", str(pl), "--out", str(tmp_path / "out")])
    assert rc == 1 and "a" in capsys.readouterr().err


# What the owner settles in the box editor comes back as a boxedit export.

def to_user(root: Path, rows: dict[str, str]) -> Path:
    """players/to_user/ as labels_export writes it: ``{item id: origin stem}``."""
    t = root / "to_user"
    (t / "images").mkdir(parents=True, exist_ok=True)
    with open(t / "to_user.jsonl", "w") as fh:
        for item, stem in rows.items():
            _write(t / "images" / f"{item}.jpg", b"jpeg " + item.encode())
            fh.write(json.dumps({"id": item, "why": "cant_tell",
                                 "origin": {"dataset": "overhead-3", "fold": "all",
                                            "stem": stem}}) + "\n")
    return t


def test_settle_turns_reviewed_edits_into_labelled_entries(tmp_path):
    root = tmp_path / "p"
    lab = players(root, {"a": ("p_1", PLAYER)})
    t = to_user(root, {"p_2": "b", "p_3": "c", "p_4": "d"})
    export = tmp_path / "edits.json"
    export.write_text(json.dumps({
        "scope": "member-labels-players",
        # p_2: a player and a stone the owner left alone; p_3: reviewed, nobody on the ice
        # p_4: boxes drawn but never marked reviewed
        "boxes": {"p_2": [[0, 0.5, 0.4, 0.1, 0.1], [2, 0.6, 0.3, 0.2, 0.4]], "p_3": [],
                  "p_4": [[2, 0.1, 0.1, 0.1, 0.1]]},
        "reviewed": ["p_2", "p_3"]}))
    got = m4.settle(export, t, lab)
    assert got == {"settled": 2, "unsettled": ["d"]}
    assert (lab / "labels" / "p_2.txt").read_text() == "2 0.600000 0.300000 0.200000 0.400000\n"
    assert (lab / "labels" / "p_3.txt").read_text() == ""
    assert (lab / "images" / "p_2.jpg").read_bytes() == b"jpeg p_2"
    rows = [json.loads(line) for line in (lab / "players.jsonl").read_text().splitlines()]
    assert [r["origin"]["stem"] for r in rows] == ["a", "b", "c"]
    # and settling again changes nothing
    assert m4.settle(export, t, lab)["settled"] == 0
    assert len((lab / "players.jsonl").read_text().splitlines()) == 3


def test_settled_frames_then_merge(tmp_path):
    root = tmp_path / "p"
    lab = players(root, {"a": ("p_1", PLAYER)})
    t = to_user(root, {"p_2": "b"})
    export = tmp_path / "edits.json"
    export.write_text(json.dumps({"boxes": {"p_2": [[2, 0.6, 0.3, 0.2, 0.4]]}, "reviewed": ["p_2"]}))
    m4.settle(export, t, lab)
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    m4.merge(bi, bl, lab, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/b.txt").read_text() == STONES + PLAYER


def test_settle_via_the_cli(tmp_path, capsys):
    root = tmp_path / "p"
    players(root, {"a": ("p_1", PLAYER)})
    to_user(root, {"p_2": "b", "p_3": "c"})
    export = tmp_path / "edits.json"
    export.write_text(json.dumps({"boxes": {"p_2": []}, "reviewed": ["p_2"]}))
    assert m4.main(["settle", "--export", str(export), "--players", str(root)]) == 0
    out = capsys.readouterr().out
    assert "settled 1" in out and "c" in out        # the stem still to leave out


def test_a_base_frame_with_no_stone_label_file_is_an_error(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    (bl / "train" / "b.txt").unlink()
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "b": ("p_2", PLAYER)})
    with pytest.raises(ValueError, match="b"):
        m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert not (tmp_path / "out").exists()


def test_a_wrong_base_labels_folder_is_an_error_not_a_set_without_stones(tmp_path):
    bi, _ = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    with pytest.raises(ValueError, match="stone label"):
        m4.merge(bi, tmp_path / "elsewhere", pl, tmp_path / "out", set())
    assert m4.main(["--base", str(tmp_path / "b" / "images"), "--players", str(pl),
                    "--out", str(tmp_path / "out")]) == 1


def test_a_missing_stone_label_does_not_matter_for_a_frame_left_out_or_dropped(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES, "train/c": STONES})
    (bl / "train" / "b.txt").unlink()
    (bl / "train" / "c.txt").unlink()
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "b": ("p_2", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "out", {"b"}, pilot=True)    # c unlabelled, b left out
    assert got["kept"] == 1


def test_a_non_empty_out_is_refused(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    _write(tmp_path / "out/images/train/stale.jpg", b"old")
    with pytest.raises(ValueError, match="already holds"):
        m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert not (tmp_path / "out/labels").exists()
    # and a frame left in labels/ alone is just as stale
    _write(tmp_path / "out2/labels/train/stale.txt", "")
    with pytest.raises(ValueError, match="already holds"):
        m4.merge(bi, bl, pl, tmp_path / "out2", set(), pilot=True)


def test_an_out_with_only_empty_folders_is_fine(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    (tmp_path / "out/images/train").mkdir(parents=True)
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").is_file()


# --- the owner's settled stones for the hard batch ----------------------------

def disputed(root: Path, rows: dict[str, str]) -> Path:
    """The box export's disputed/ for ``{item id: stem}``."""
    d = root / "disputed"
    (d / "images").mkdir(parents=True, exist_ok=True)
    with open(d / "disputed.jsonl", "w") as fh:
        for item, stem in rows.items():
            _write(d / "images" / f"{item}.jpg", b"jpeg disputed " + stem.encode())
            fh.write(json.dumps({"id": item, "why": "rubber_stamp", "origin": {"stem": stem}}) + "\n")
    return d


def edits(path: Path, boxes: dict, reviewed: list[str]) -> Path:
    path.write_text(json.dumps({"scope": "member-labels", "boxes": boxes, "reviewed": reviewed}))
    return path


def test_a_hard_frame_the_members_rubber_stamped_enters_on_the_owners_settled_stones(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h1": ("p_2", PLAYER),
                                  "h2": ("p_3", PLAYER), "h3": ("p_4", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "x/boxes", {"h1": STONES})
    disputed(tmp_path / "x/boxes", {"b_10": "h2", "b_11": "h3"})
    export = edits(tmp_path / "edits.json",
                   {"b_10": [[0, 0.5, 0.4, 0.1, 0.1], [1, 0.2, 0.7, 0.1, 0.1]],
                    "b_11": [[0, 0.5, 0.5, 0.1, 0.1]]},
                   ["b_10"])                         # b_11 was touched, never marked reviewed
    got = m4.merge(bi, bl, pl, tmp_path / "out", set(), hard_stones=agreed, hard_settled=export)
    assert (tmp_path / "out/labels/train/h2.txt").read_text() == STONES + PLAYER
    assert (tmp_path / "out/images/train/h2.jpg").read_bytes() == b"jpeg disputed h2"
    assert (tmp_path / "out/labels/train/h1.txt").read_text() == STONES + PLAYER
    assert not (tmp_path / "out/labels/train/h3.txt").exists()
    assert (got["kept"], got["hard"], got["hard_settled"], got["hard_no_stones"]) == (3, 2, 1, 1)


def test_settled_stones_are_only_classes_0_and_1_and_a_reviewed_empty_frame_is_kept(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h2": ("p_3", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "x/boxes", {})
    disputed(tmp_path / "x/boxes", {"b_10": "h2"})
    export = edits(tmp_path / "edits.json", {"b_10": [[2, 0.1, 0.1, 0.1, 0.1]]}, ["b_10"])
    m4.merge(bi, bl, pl, tmp_path / "out", set(), hard_stones=agreed, hard_settled=export)
    assert (tmp_path / "out/labels/train/h2.txt").read_text() == PLAYER


def test_an_agreed_frame_keeps_its_agreed_stones_over_a_settled_one(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h1": ("p_2", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "x/boxes", {"h1": STONES})
    disputed(tmp_path / "x/boxes", {"b_10": "h1"})
    export = edits(tmp_path / "edits.json", {"b_10": [[0, 0.9, 0.9, 0.1, 0.1]]}, ["b_10"])
    got = m4.merge(bi, bl, pl, tmp_path / "out", set(), hard_stones=agreed, hard_settled=export)
    assert (tmp_path / "out/labels/train/h1.txt").read_text() == STONES + PLAYER
    assert got["hard"] == 1 and got["hard_settled"] == 0


def test_a_settled_hard_frame_still_needs_a_player_label_and_obeys_leave_out(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "h3": ("p_4", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "x/boxes", {})
    disputed(tmp_path / "x/boxes", {"b_10": "h2", "b_11": "h3"})
    export = edits(tmp_path / "edits.json", {"b_10": [], "b_11": []}, ["b_10", "b_11"])
    got = m4.merge(bi, bl, pl, tmp_path / "out", {"h3"}, hard_stones=agreed, hard_settled=export)
    assert (got["hard"], got["hard_no_player"], got["left_out"]) == (0, 1, 1)


def test_hard_settled_without_hard_stones_is_refused_and_two_unlike_settlements_too(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    export = edits(tmp_path / "edits.json", {}, [])
    with pytest.raises(ValueError, match="hard-stones"):
        m4.merge(bi, bl, pl, tmp_path / "out", set(), hard_settled=export)
    d = disputed(tmp_path / "x/boxes", {"b_10": "h2", "b_11": "h2"})
    export = edits(tmp_path / "edits2.json", {"b_10": [[0, 0.5, 0.4, 0.1, 0.1]], "b_11": []},
                   ["b_10", "b_11"])
    with pytest.raises(ValueError, match="h2"):
        m4.settled_stones(export, d)


def test_main_takes_hard_settled_and_hard_disputed(tmp_path, capsys):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    players(tmp_path / "p", {"a": ("p_1", PLAYER), "h2": ("p_3", PLAYER)}, dataset="hard-1")
    agreed = hard(tmp_path / "x/boxes", {})
    d = disputed(tmp_path / "elsewhere", {"b_10": "h2"})
    export = edits(tmp_path / "edits.json", {"b_10": [[0, 0.5, 0.4, 0.1, 0.1]]}, ["b_10"])
    rc = m4.main(["--base", str(tmp_path / "b"), "--players", str(tmp_path / "p"),
                  "--hard-stones", str(agreed), "--hard-settled", str(export),
                  "--hard-disputed", str(d), "--out", str(tmp_path / "out")])
    assert rc == 0 and "hard_settled 1" in capsys.readouterr().out
    assert (tmp_path / "out/labels/train/h2.txt").read_text() == \
        "0 0.500000 0.400000 0.100000 0.100000\n" + PLAYER


# --- the sides file: only receiving-panel frames were published ----------------

def _sides(**keep):
    return {stem: {"keep": k, "side": "receiving" if k else "throwing", "by": "timeline"}
            for stem, k in keep.items()}


def test_an_unlabelled_throwing_frame_is_dropped_and_counted_not_an_error(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/t": ""})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "out", set(), sides=_sides(a=True, t=False))
    assert (got["kept"], got["throwing"]) == (1, 1)
    assert not (tmp_path / "out/labels/train/t.txt").exists()


def test_a_kept_frame_with_no_label_is_still_an_error_with_sides(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {})
    with pytest.raises(ValueError, match="no player label"):
        m4.merge(bi, bl, pl, tmp_path / "out", set(), sides=_sides(a=True))


def test_a_throwing_frame_labelled_anyway_is_kept(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/t": STONES})
    pl = players(tmp_path / "p", {"t": ("p_1", PLAYER)})
    got = m4.merge(bi, bl, pl, tmp_path / "out", set(), sides=_sides(t=False))
    assert (got["kept"], got["throwing"]) == (1, 0)


def test_a_base_frame_the_sides_file_does_not_cover_is_an_error(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "b": ("p_2", PLAYER)})
    with pytest.raises(ValueError, match="not in the sides file"):
        m4.merge(bi, bl, pl, tmp_path / "out", set(), sides=_sides(a=True))


def test_main_reads_sides(tmp_path, capsys):
    base(tmp_path / "b", {"train/a": STONES, "train/t": ""})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    sides = tmp_path / "sides.json"
    sides.write_text(json.dumps(_sides(a=True, t=False)))
    rc = m4.main(["--base", str(tmp_path / "b"), "--players", str(pl), "--sides", str(sides),
                  "--out", str(tmp_path / "out")])
    assert rc == 0
    assert "throwing 1" in capsys.readouterr().out


# --- stones a member fixed in the player flow ---------------------------------

def fixed(root: Path, item: str, label: str) -> None:
    _write(root / "labelled" / "stones" / f"{item}.txt", label)


def test_a_stone_fix_replaces_the_frames_stones(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES, "train/b": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER), "b": ("p_2", PLAYER)})
    fix = "1 0.300000 0.300000 0.100000 0.100000\n"
    fixed(tmp_path / "p", "p_1", fix)
    got = m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == fix + PLAYER
    assert (tmp_path / "out/labels/train/b.txt").read_text() == STONES + PLAYER
    assert got["stones_fixed"] == 1


def test_a_fix_to_no_stones_leaves_just_the_players(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    fixed(tmp_path / "p", "p_1", "")
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == PLAYER


def test_a_stone_fix_keeps_only_stone_classes(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)})
    fixed(tmp_path / "p", "p_1", "0 0.3 0.3 0.1 0.1\n2 0.5 0.5 0.1 0.1\n")
    m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == "0 0.3 0.3 0.1 0.1\n" + PLAYER


def test_a_stone_file_the_rows_do_not_name_as_a_fix_is_ignored(tmp_path):
    bi, bl = base(tmp_path / "b", {"train/a": STONES})
    pl = players(tmp_path / "p", {"a": ("p_1", PLAYER)}, fixes=False)
    fixed(tmp_path / "p", "p_1", "")      # left over from an earlier export
    got = m4.merge(bi, bl, pl, tmp_path / "out", set())
    assert (tmp_path / "out/labels/train/a.txt").read_text() == STONES + PLAYER
    assert got["stones_fixed"] == 0
