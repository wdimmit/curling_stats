"""``scripts/release/cut.py`` and ``train.py``, loaded by path as
``test_mark_release.py`` loads its script. The release model is trained on
what these two make, so what they pin is that the crops come out of the
production crop code, the labels follow the hand marks to the frame, and the
trainer scores and saves the model the way the pipeline will load it.
"""
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

from curling_score.detect import longview
from curling_score.game import linetime, releasetime

torch = pytest.importorskip("torch")

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "release"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"release_{name}", _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cut = _load("cut")
train = _load("train")

TEE_Y = linetime.TEE_Y

# 10/20 Tuesday sheet 2's left view, as the timeline's calibration records it.
LEFT = {"rect": [0, 0, 810, 1080], "tee_row": 360.8, "hog_row": 433.0,
        "centre_col": 399.21, "lat_px_per_m_at_tee": 117.425,
        "centre_line": [394.038, 0.007471]}


class TestLabels:
    def test_off_is_zero_before_the_mark_one_from_it_and_two_frames_either_side_left_out(self):
        o = np.arange(60) / 30
        lab = cut.labels(o, "off", 1.0)
        assert lab.dtype == np.int8
        assert (lab[:28] == 0).all()
        assert (lab[28:33] == -1).all()
        assert (lab[33:] == 1).all()

    def test_a_mark_up_to_a_frame_before_the_true_frame_times(self):
        # mark_release labelled frames t0 + i/30 while the keeper keys the
        # source frame at or after t0, so a mark can sit a fraction of a
        # frame before the frame it names: two frames before it and the
        # frames within two after are left out, no more.
        o = np.arange(60) / 30
        lab = cut.labels(o, "off", 1.0 - 0.4 / 30)
        assert (lab[:28] == 0).all()
        assert (lab[28:32] == -1).all()
        assert (lab[32:] == 1).all()

    def test_held_is_all_on(self):
        lab = cut.labels(np.arange(30) / 30, "held")
        assert lab.dtype == np.int8 and (lab == 0).all() and len(lab) == 30

    def test_cant_tell_has_no_labels(self):
        with pytest.raises(ValueError):
            cut.labels(np.arange(30) / 30, None)


class TestTags:
    def test_a_tagged_file_keeps_its_tag(self):
        assert cut.tag_of({"source_id": "s_0hT4wgp2neSrsEmdi", "tag": "SL1"}) == "SL1"

    def test_the_1020_game_is_T1020_and_others_their_source_ids_start(self):
        assert cut.tag_of({"source_id": "s_1TH0dJNrbHeKLARuj"}) == "T1020"
        assert cut.tag_of({"source_id": "s_0abcdefghij"}) == "s_0abc"


# A throw from a metre behind the throwing tee to 3 m past it in 1.2 s,
# drifting a little to the thrower's right: `[t, y, x]` as line.delivery is.
DELIVERY = [[1.2 * i / 12, TEE_Y - (-1.0 + 4.0 * i / 12), 0.05 + 0.1 * i / 12]
            for i in range(13)]
# Off the 1/30 s grid, so the decode's labels and the source frames differ.
T_REL = 100.01


def timeline():
    """One game, one end played to the bottom house (so thrown from the top,
    the left camera facing the thrower), two rocks on DELIVERY."""
    shots = [{"number": n, "color": c, "position": "lead", "t_release_s": T_REL + 20 * n,
              "line": {"delivery": DELIVERY}} for n, c in ((1, "red"), (2, "yellow"), (3, "red"))]
    return {"calibration": {"left": LEFT},
            "games": [{"ends": [{"number": 1, "house": "bottom", "shots": shots}]}]}


def fake_decode(view):
    """`longview._decode_now` as ffmpeg answers it: the source frames from the
    first at or after ``t0``, labelled ``t0 + i/fps``, each the view's pixels
    with a bright dot at the rock's image point at the frame's true time."""
    def decode(video, rect, t0, t1, fps, lead):
        assert tuple(rect) == tuple(view.rect) and fps == 30.0
        first = math.ceil(t0 * 30 - 1e-6)
        idx = list(range(first, math.ceil(t1 * 30 - 1e-6)))
        _x, _y, w, h = rect
        frames = np.zeros((len(idx), h, w, 3), np.uint8)
        for i, k in enumerate(idx):
            for n in (1, 2, 3):
                at = releasetime.rock_at(DELIVERY, k / 30 - (T_REL + 20 * n))
                if at is not None:
                    col, row = view.to_image(-at[1], TEE_Y - at[0])
                    frames[i, round(row) - 1:round(row) + 2, round(col) - 1:round(col) + 2] = 255
        return frames, [t0 + i / fps for i in range(len(idx))]
    return decode


def frames_on_the_path(t_rel):
    """Source frame times in the cut's window whose rock is on the path and
    no further behind the tee than the crops start."""
    o_from = next(t for t, y, _x in DELIVERY if TEE_Y - y >= releasetime.FROM_M)
    t0, t1 = t_rel + o_from - cut.BEFORE_S, t_rel + DELIVERY[-1][0] + cut.AFTER_S
    out = []
    for k in range(math.ceil(t0 * 30 - 1e-6), math.ceil(t1 * 30 - 1e-6)):
        at = releasetime.rock_at(DELIVERY, k / 30 - t_rel)
        if at is not None and TEE_Y - at[0] >= releasetime.FROM_M:
            out.append(k / 30 - t_rel)
    return np.array(out)


class TestCut:
    @pytest.fixture
    def made(self, tmp_path, monkeypatch):
        from curling_score.game import clips
        view = clips.sideviews_from_calibration({"left": LEFT})["left"]
        monkeypatch.setattr(longview, "_decode_now", fake_decode(view))
        monkeypatch.setattr(longview, "_lead", lambda video, t0: 0.0)
        (tmp_path / "tl").mkdir()
        (tmp_path / "tl" / "s_0testgame1.json").write_text(json.dumps(timeline()))
        rocks = [
            {"id": "e01r01", "end": 1, "rock": 1, "color": "red", "position": "lead",
             "t_release_s": T_REL + 20, "chosen": "random", "mark": "off", "o": 0.6, "yp": 1.0},
            {"id": "e01r02", "end": 1, "rock": 2, "color": "yellow", "position": "lead",
             "t_release_s": T_REL + 40, "chosen": "hard", "mark": "held"},
            {"id": "e01r03", "end": 1, "rock": 3, "color": "red", "position": "lead",
             "t_release_s": T_REL + 60, "chosen": "random", "mark": None},
        ]
        marks = tmp_path / "s_0testgame1.json"
        marks.write_text(json.dumps({"source_id": "s_0testgame1", "video_id": "vid",
                                     "tag": "TG", "rocks": rocks}))
        cut.main([str(marks), "--timelines", str(tmp_path / "tl"),
                  "--videos", str(tmp_path / "videos"), "--out", str(tmp_path / "out")])
        return np.load(tmp_path / "out" / "TG.npz", allow_pickle=False)

    def test_the_npz_has_the_layout(self, made):
        assert set(made.files) == {"crops", "o", "yp", "label", "offs", "meta"}
        n = len(made["crops"])
        assert made["crops"].shape == (n, 107, 160) and made["crops"].dtype == np.uint8
        assert made["o"].shape == made["yp"].shape == (n,)
        assert made["o"].dtype == made["yp"].dtype == np.float32
        assert made["label"].shape == (n,) and made["label"].dtype == np.int8
        meta = json.loads(str(made["meta"]))
        assert [m["id"] for m in meta] == ["e01r01", "e01r02"]          # can't tell left out
        assert len(made["offs"]) == len(meta) + 1 and made["offs"][0] == 0 and made["offs"][-1] == n
        off, held = meta
        assert off["game"] == "TG" and off["color"] == "red" and off["position"] == "lead"
        assert off["mark"] == "off" and off["mark_o"] == pytest.approx(0.6)
        assert off["mark_yp"] == 1.0 and off["t_release_s"] == T_REL + 20
        assert off["delivery"] == DELIVERY
        assert held["mark"] == "held" and held["mark_o"] is None and held["mark_yp"] is None
        assert off["chosen"] == "random" and held["chosen"] == "hard"

    def test_each_rock_says_where_it_came_from_and_the_geometry_it_was_cut_with(self, made):
        for m in json.loads(str(made["meta"])):
            assert (m["source_id"], m["video_id"], m["camera"]) == ("s_0testgame1", "vid", "left")
            crop = m["crop"]
            assert tuple(crop["CROP_PX"]) == releasetime.CROP_PX
            assert (crop["CROP_ACROSS_M"], crop["CROP_UP_M"], crop["CROP_DOWN_M"]) == (
                releasetime.CROP_ACROSS_M, releasetime.CROP_UP_M, releasetime.CROP_DOWN_M)
            assert crop["FROM_M"] == releasetime.FROM_M
            assert tuple(crop["STACK_S"]) == releasetime.STACK_S
            assert crop["MARGIN_S"] == cut.MARGIN_S

    def test_the_trainer_takes_a_fresh_cut(self, made):
        assert cut.STAMPED == train.STAMPED
        train.check_stamp("TG.npz", json.loads(str(made["meta"])))

    def test_one_crop_per_source_frame_whose_rock_is_on_the_read_span(self, made):
        offs = made["offs"]
        for r, t_rel in enumerate((T_REL + 20, T_REL + 40)):
            want = frames_on_the_path(t_rel)
            got = made["o"][offs[r]:offs[r + 1]]
            assert len(got) == len(want)
            assert np.allclose(got, want, atol=1e-5)

    def test_the_crops_are_the_production_crops_centred_on_the_rock(self, made):
        for c in made["crops"]:
            row, col = np.unravel_index(c.argmax(), c.shape)
            assert abs(col - 80) <= 2 and abs(row - 88) <= 2

    def test_labels_follow_the_mark_and_held_is_all_on(self, made):
        offs = made["offs"]
        o_off, lab_off = made["o"][offs[0]:offs[1]], made["label"][offs[0]:offs[1]]
        assert (lab_off == cut.labels(o_off, "off", 0.6)).all()
        assert {0, 1, -1} <= set(lab_off.tolist())
        assert (made["label"][offs[1]:offs[2]] == 0).all()


def test_cut_skips_a_remarks_file_and_does_not_count_it(tmp_path, capsys):
    # datasets/releasemarks/*.json takes in remarks-*.json, which is no game's marks.
    remarks = tmp_path / "remarks-2026-10-21.json"
    remarks.write_text(json.dumps({"marked": "2026-10-21", "rocks": [{"id": "S3k-e02r13"}]}))
    cut.main([str(remarks), "--timelines", str(tmp_path), "--videos", str(tmp_path),
              "--out", str(tmp_path / "out")])
    assert list((tmp_path / "out").iterdir()) == []
    assert capsys.readouterr().out.splitlines()[-1] == "0 games, 0 rocks, 0 frames"


def test_training_stacks_are_the_frames_releasetime_stacks():
    # Training gathers each stack from frame indices rather than holding every
    # stack in memory; they have to be the frames production stacks, gaps and all.
    rng = np.random.default_rng(0)
    o = np.delete(np.arange(40) / 30, [7, 8, 20])
    crops = rng.integers(0, 256, (len(o), 6, 8), dtype=np.uint8)
    rows = train.stack_rows(o)
    assert rows.shape == (len(o), len(releasetime.STACK_S))
    gathered = (crops[rows] / 255.0).astype(np.float32)
    assert np.array_equal(gathered, releasetime.stacks(crops, o))


def synthetic_game(path, game, rng, frames=16, step=8, crop=None):
    """Two short rocks of noise crops, the hand coming off at frame ``step``:
    the first chosen at random, the second "hard". Stamped with today's
    geometry unless ``crop`` says otherwise (the crops are smaller than it
    says, to keep training fast)."""
    crop = cut.crop_stamp() if crop is None else crop
    crops, os_, yps, labs, meta, offs = [], [], [], [], [], [0]
    delivery = [[0.0, TEE_Y + 0.5, 0.0], [frames / 30, TEE_Y - 3.0, 0.0]]
    for r in range(2):
        o = np.arange(frames) / 30
        yp = TEE_Y - np.interp(o, [d[0] for d in delivery], [d[1] for d in delivery])
        crops.append(rng.integers(0, 256, (frames, 16, 24), dtype=np.uint8))
        os_.append(o)
        yps.append(yp)
        labs.append(cut.labels(o, "off", o[step]))
        meta.append({"id": f"{game}-e01r{r + 1:02d}", "game": game, "color": ("red", "yellow")[r],
                     "position": "lead", "chosen": ("random", "hard")[r], "mark": "off",
                     "mark_o": float(o[step]), "mark_yp": float(yp[step]),
                     "t_release_s": 100.0 + r, "delivery": delivery, "crop": crop})
        offs.append(offs[-1] + frames)
    np.savez_compressed(path, crops=np.concatenate(crops), o=np.concatenate(os_).astype(np.float32),
                        yp=np.concatenate(yps).astype(np.float32), label=np.concatenate(labs),
                        offs=np.array(offs), meta=json.dumps(meta))


def test_train_scores_each_held_out_game_and_saves_a_loadable_model(tmp_path, monkeypatch):
    from torchvision.models import resnet18

    from curling_score.detect import releasemodel

    # Held-out rocks are scored through releasemodel._load, which takes CUDA
    # when there is one; this test stays on the CPU.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    rng = np.random.default_rng(0)
    for game in ("GA", "GB"):
        synthetic_game(tmp_path / f"{game}.npz", game, rng)
    torch.manual_seed(0)
    torch.save(resnet18(weights=None).state_dict(), tmp_path / "imagenet.pth")
    out, saved = tmp_path / "out", tmp_path / "release.pt"
    train.main([str(tmp_path / "GA.npz"), str(tmp_path / "GB.npz"), "--out", str(out),
                "--init", str(tmp_path / "imagenet.pth"), "--epochs", "1", "--batch", "8",
                "--device", "cpu", "--save", str(saved)])

    results = json.loads((out / "results.json").read_text())
    assert sorted(r["id"] for r in results) == ["GA-e01r01", "GA-e01r02", "GB-e01r01", "GB-e01r02"]
    for r in results:
        assert r["fold"] == r["game"]
        assert {"color", "mark", "mark_yp", "pred_yp", "err", "p"} <= set(r)
        assert r["chosen"] == ("random" if r["id"].endswith("r01") else "hard")
        assert len(r["p"]) == 16
    # Each fold's model was scored as the weights file the pipeline would load.
    assert (out / "fold-GA.pt").exists() and (out / "fold-GB.pt").exists()

    state = torch.load(saved, map_location="cpu", weights_only=True)
    assert all(v.dtype == torch.float16 for v in state.values() if v.is_floating_point())
    model = releasemodel._load(str(saved))
    assert releasemodel.p_off(model, np.zeros((2, 3, 16, 24), np.float32)).shape == (2,)


def test_crops_cut_with_another_geometry_are_refused(tmp_path):
    rng = np.random.default_rng(2)
    synthetic_game(tmp_path / "GA.npz", "GA", rng)
    synthetic_game(tmp_path / "GB.npz", "GB", rng,
                   crop={**cut.crop_stamp(), "FROM_M": releasetime.FROM_M - 0.5})
    with pytest.raises(SystemExit, match="FROM_M"):
        train.main([str(tmp_path / "GA.npz"), str(tmp_path / "GB.npz"), "--out",
                    str(tmp_path / "out"), "--init", str(tmp_path / "none.pth"), "--device", "cpu"])


def test_crops_with_no_stamp_are_refused(tmp_path):
    rng = np.random.default_rng(3)
    synthetic_game(tmp_path / "GA.npz", "GA", rng, crop={})
    with pytest.raises(SystemExit, match="no crop stamp"):
        train.Data([tmp_path / "GA.npz"])


def row(err, chosen="random", color="red", mark="off"):
    """A results.json row: ``err`` None is a rock read as no release."""
    return {"chosen": chosen, "color": color, "mark": mark, "err": err,
            "pred_yp": None if err is None else 3.0 + err}


class TestScores:
    ROWS = [row(0.1), row(0.4, color="yellow"), row(None), row(2.0, chosen="hard")]

    def test_within_is_of_the_rocks_read_and_off_within_05_of_every_rock_marked_off(self):
        s = train.stats(self.ROWS)
        assert (s["off"], s["read"]) == (4, 3)
        assert s["within"][0.5] == pytest.approx(2 / 3)
        assert s["off_within_05"] == pytest.approx(2 / 4)
        assert s["over_1m"] == 1

    def test_a_hard_rock_is_scored_apart_from_the_random_ones(self):
        random_line, all_line = train.report("ALL", self.ROWS)
        assert random_line.startswith("ALL random:") and all_line.startswith("ALL all:")
        assert "2/3 read" in random_line and "over 1 m 0" in random_line
        assert "3/4 read" in all_line and "over 1 m 1" in all_line
        assert "within 0.5 m of all marked off 67%" in random_line
        assert "within 0.5 m of all marked off 50%" in all_line

    def test_no_random_rocks_says_so(self):
        assert train.report("fold X", [row(0.2, chosen="hard")])[0] == "fold X random: none"


def test_a_fold_naming_a_game_not_in_the_data_is_refused(tmp_path):
    rng = np.random.default_rng(1)
    synthetic_game(tmp_path / "GA.npz", "GA", rng)
    with pytest.raises(SystemExit):
        train.main([str(tmp_path / "GA.npz"), "--out", str(tmp_path / "out"),
                    "--init", str(tmp_path / "none.pth"), "--folds", "GA+SL9", "--device", "cpu"])
