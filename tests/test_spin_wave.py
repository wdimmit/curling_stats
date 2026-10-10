"""scripts/spin: the handle-point wave -- which frames are offered for labelling,
and that what a person labels is kept."""
import importlib.util
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

_DIR = Path(__file__).parents[1] / "scripts" / "spin"
sys.path.insert(0, str(_DIR))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"spin_{name}", _DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wave = _load("wave")
serve = _load("serve")
page = _load("page")


def rock(seconds=14.0, fps=30.0, glitch_at=None, lost_at_m=26.0):
    """A stone coming down the view from 3 m behind the tee until it is lost
    (by default near the bottom of the frame), its box bottom row and width
    growing together along one line, as the perspective has them."""
    recs = []
    for i in range(int(seconds * fps)):
        t = 100.0 + i / fps
        y = -3.0 + (lost_at_m + 3.0) * i / (seconds * fps)
        bottom = 340.0 + 26.0 * (y + 3.0)
        w = 0.12 * bottom - 5.0
        if glitch_at is not None and abs(t - glitch_at) < 1e-6:
            w *= 1.6                          # a box that swallowed a broom head
        x0 = 400.0 - w / 2
        recs.append(dict(t=t, y_m=y, box=(x0, bottom - 0.7 * w, x0 + w, bottom),
                         origin=(int(x0 - 10), int(bottom - w)),
                         patch=np.zeros((int(w) + 10, int(w) + 20, 3), np.uint8)))
    return recs


class TestUsable:
    def test_only_frames_after_the_hand_is_off(self):
        assert min(r["y_m"] for r in wave.usable(rock())) >= wave.Y_START_M

    def test_a_box_leaving_the_bottom_of_the_frame_is_not_offered(self):
        assert max(r["box"][3] for r in wave.usable(rock())) < wave.FRAME_BOTTOM_ROW

    def test_a_box_off_the_stones_width_line_is_dropped(self):
        recs = rock(glitch_at=100.0 + 300 / 30.0)
        kept = {round(r["t"], 4) for r in wave.usable(recs)}
        assert round(100.0 + 300 / 30.0, 4) not in kept
        assert round(100.0 + 301 / 30.0, 4) in kept


class TestPick:
    def test_two_frames_from_different_stretches_of_the_path(self):
        got = wave.pick(rock(), 2, random.Random(1))
        bins = [next(i for i, (a, b) in enumerate(wave.BINS_M) if a <= r["y_m"] < b) for r in got]
        assert len(got) == 2 and len(set(bins)) == 2

    def test_every_pick_has_its_whole_animation_second(self):
        recs = rock()
        for seed in range(20):
            for r in wave.pick(recs, 3, random.Random(seed)):
                assert all(wave.nearest(recs, r["t"] + dt) is not None for dt in wave.ANIM_OFFSETS_S)

    def test_the_same_seed_picks_the_same_frames(self):
        a = [r["t"] for r in wave.pick(rock(), 2, random.Random(7), rotate=1)]
        b = [r["t"] for r in wave.pick(rock(), 2, random.Random(7), rotate=1)]
        assert a == b

    def test_rotation_spreads_the_stretches_across_rocks(self):
        seen = set()
        for k in range(3):
            for r in wave.pick(rock(), 1, random.Random(k), rotate=k):
                seen.add(next(i for i, (a, b) in enumerate(wave.BINS_M) if a <= r["y_m"] < b))
        assert seen == {0, 1, 2}

    def test_a_rock_lost_before_the_hand_is_off_offers_nothing(self):
        assert wave.pick(rock(lost_at_m=7.5), 2, random.Random(1)) == []


class TestStem:
    def test_names_the_video_rock_and_centisecond(self):
        meta = {"end": 3, "shot": 7}
        assert wave.stem_for("PHbZ3EKOhMI", meta, {"t": 3048.567}) == "PHbZ3EKOhMI_e3s07_0304857"


class TestSave:
    P = {"scope": "spin:wave1", "sitting": "20261010T120000",
         "points": {"a": {"neck": [1.0, 2.0], "grip": [3.0, 4.0]}}, "flags": {"b": "unclear"}}

    def test_a_sitting_is_written_beside_the_frames(self, tmp_path):
        out = serve.save_edits(tmp_path, self.P)
        assert out == {"ok": True, "path": str(tmp_path / "edits" / "spin-wave1-20261010T120000.json"),
                       "points": 1, "flags": 1}
        assert json.loads(Path(out["path"]).read_text()) == self.P

    def test_saving_again_in_a_sitting_replaces_its_own_file_only(self, tmp_path):
        serve.save_edits(tmp_path, self.P)
        serve.save_edits(tmp_path, {**self.P, "sitting": "20261011T090000"})
        more = {**self.P, "points": {**self.P["points"], "c": {"neck": [5, 6], "grip": [7, 8]}}}
        serve.save_edits(tmp_path, more)
        files = sorted(p.name for p in (tmp_path / "edits").iterdir())
        assert files == ["spin-wave1-20261010T120000.json", "spin-wave1-20261011T090000.json"]
        assert "c" in json.loads((tmp_path / "edits" / files[0]).read_text())["points"]

    def test_an_empty_save_is_refused_not_reported_as_success(self, tmp_path):
        out = serve.save_edits(tmp_path, {"scope": "s", "sitting": "x", "points": {}, "flags": {}})
        assert out["ok"] is False and not (tmp_path / "edits").exists()

    def test_a_frame_with_one_point_is_refused(self, tmp_path):
        out = serve.save_edits(tmp_path, {"scope": "s", "sitting": "x",
                                          "points": {"a": {"neck": [1, 2]}}})
        assert out["ok"] is False and "a" in out["reason"]


class TestPage:
    def test_the_page_carries_its_frames_and_saves_through_the_server(self, tmp_path):
        items = [dict(stem="v_e1s01_0000100", size=[80, 60], stone_box=[10, 15, 60, 50], color="red",
                      video="v", end=1, shot=1, y_m=12.0)]
        page.render(tmp_path, items, "spin:test")
        html = (tmp_path / "index.html").read_text()
        assert '"v_e1s01_0000100"' in html and '"spin:test"' in html
        assert 'fetch("/save"' in html
        assert "__ITEMS__" not in html and "__SCOPE__" not in html


reorder = _load("reorder")


def frames(counts):
    return [dict(stem=f"{v}_{k:03d}", video=v) for v, n in counts.items() for k in range(n)]


class TestInterleave:
    COUNTS = {"PHbZ": 188, "0SWB": 96, "VVgL": 92, "QQpN": 93, "dea7": 92}

    def test_every_run_of_frames_holds_every_game_in_proportion(self):
        order = wave.interleave(frames(self.COUNTS), seed=3)
        total = sum(self.COUNTS.values())
        for prefix in (50, 100, 250, 400):
            seen = [it["video"] for it in order[:prefix]]
            for v, n in self.COUNTS.items():
                assert abs(seen.count(v) - prefix * n / total) <= 2.5, (prefix, v)

    def test_frames_already_done_stay_first_in_their_order(self):
        items = frames(self.COUNTS)
        done = [it["stem"] for it in items[:47]]
        order = wave.interleave(items, first=done, seed=1)
        assert [it["stem"] for it in order[:47]] == done
        assert sorted(it["stem"] for it in order) == sorted(it["stem"] for it in items)

    def test_the_same_seed_gives_the_same_order(self):
        a = [it["stem"] for it in wave.interleave(frames(self.COUNTS), seed=5)]
        b = [it["stem"] for it in wave.interleave(frames(self.COUNTS), seed=5)]
        assert a == b


class TestReorder:
    def test_keeps_the_scope_the_page_already_has(self, tmp_path):
        items = [dict(stem="v_e1s01_0000100", size=[80, 60], stone_box=[10, 15, 60, 50], color="red",
                      video="v", end=1, shot=1, y_m=12.0)]
        page.render(tmp_path, items, "spin:wave1")
        assert reorder.page_scope(tmp_path) == "spin:wave1"

    def test_done_is_every_frame_with_points_or_a_flag_in_any_sitting(self, tmp_path):
        serve.save_edits(tmp_path, {"scope": "s", "sitting": "a",
                                    "points": {"x": {"neck": [1, 2], "grip": [3, 4]}}})
        serve.save_edits(tmp_path, {"scope": "s", "sitting": "b", "points": {}, "flags": {"y": "unclear"}})
        assert reorder.done_stems(tmp_path) == {"x", "y"}


make_tree = _load("make_tree")


class TestMakeTree:
    def test_a_later_sitting_wins_and_a_flag_drops_the_points(self, tmp_path):
        a = tmp_path / "s-20261010T100000.json"
        b = tmp_path / "s-20261010T110000.json"
        a.write_text(json.dumps({"points": {"x": {"neck": [1, 1], "grip": [2, 2]},
                                            "y": {"neck": [1, 1], "grip": [2, 2]}}, "flags": {}}))
        b.write_text(json.dumps({"points": {"x": {"neck": [5, 5], "grip": [6, 6]}}, "flags": {"y": "unclear"}}))
        points, flags = make_tree.merged([b, a])          # file-name order, not argument order
        assert points == {"x": {"neck": [5, 5], "grip": [6, 6]}} and flags == {"y": "unclear"}

    def test_the_box_takes_in_the_handle_above_the_detectors_box(self):
        item = {"stone_box": [10.0, 20.0, 60.0, 50.0]}      # ds13c's box stops at the cap
        line = make_tree.label_line(item, {"neck": [35.0, 22.0], "grip": [20.0, 12.0]}, (80, 60))
        cls, cx, cy, bw, bh, nx, ny, nv, gx, gy, gv = line.split()
        top = float(cy) * 60 - float(bh) * 60 / 2
        assert cls == "0" and top < 12.0 and (nv, gv) == ("2", "2")
        assert float(gx) * 80 == pytest.approx(20.0, abs=1e-3)

    def test_both_frames_of_a_rock_land_on_one_side(self, tmp_path):
        wave_dir = tmp_path / "w"
        (wave_dir / "images").mkdir(parents=True)
        items = []
        for r in range(10):
            for k in range(2):
                stem = f"v_e1s{r:02d}_{k:07d}"
                Image.new("RGB", (80, 60)).save(wave_dir / "images" / f"{stem}.png")
                items.append({"stem": stem, "stone_box": [10, 20, 60, 50]})
        (wave_dir / "items.json").write_text(json.dumps(items))
        ed = tmp_path / "e.json"
        ed.write_text(json.dumps({"points": {it["stem"]: {"neck": [35, 22], "grip": [20, 12]} for it in items}}))
        counts, _ = make_tree.build(wave_dir, [ed], tmp_path / "tree", hold=["v_e1s00"], val_frac=0.3)
        assert counts == {"train": 12, "val": 6, "held": 2}
        val = {p.stem.rsplit("_", 1)[0] for p in (tmp_path / "tree" / "labels" / "val").iterdir()}
        train = {p.stem.rsplit("_", 1)[0] for p in (tmp_path / "tree" / "labels" / "train").iterdir()}
        assert not val & train and "v_e1s00" not in val | train


class TestTiered:
    def items(self):
        out = []
        for v in ("a", "b"):
            for k in range(20):
                out.append(dict(stem=f"{v}_{k:03d}", video=v, color="yellow" if k % 2 else "red",
                                y_m=10.0 if k % 4 < 2 else 24.0))
        return out

    def test_done_then_colour_then_early(self):
        items = self.items()
        done = [items[0]["stem"], items[25]["stem"]]
        order = reorder.tiered(items, done, seed=1, colour_first="yellow", early_first=True)
        assert [it["stem"] for it in order[:2]] == done
        tiers = [(it["color"] != "yellow", it["y_m"] >= reorder.EARLY_MAX_M) for it in order[2:]]
        assert tiers == sorted(tiers)
        assert sorted(it["stem"] for it in order) == sorted(it["stem"] for it in items)

    def test_each_tier_still_spans_the_games(self):
        order = reorder.tiered(self.items(), [], seed=1, colour_first="yellow", early_first=True)
        first_tier = [it for it in order if it["color"] == "yellow" and it["y_m"] < reorder.EARLY_MAX_M]
        assert {it["video"] for it in first_tier[:4]} == {"a", "b"}
