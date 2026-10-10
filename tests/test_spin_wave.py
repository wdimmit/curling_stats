"""scripts/spin: the handle-point wave -- which frames are offered for labelling,
and that what a person labels is kept."""
import importlib.util
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

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
