import json
import os
import stat

import numpy as np

from curling_score.fpframes import CHECKS
from curling_score.fpframes import store
from curling_score.fpframes.choose import choose
from curling_score.fpframes.record import Firing, Recorder

MODELS = {"overhead": "o-1", "side": None}


def firing(check, t, score=1.0, *, game=0, end=1, box=(10.0, 20.0, 50.0, 60.0)):
    return Firing(check=check, t=float(t), camera="side", color="red", conf=0.8,
                  score=float(score), info={"ratio": 0.3}, box=box,
                  rect=(1110, 0, 810, 1080), where="sheet2", house=None,
                  game=game, end=end)


def recorder_with(firings, models=None):
    rec = Recorder("vid", models=models or MODELS, pipeline="2026.10.21.1",
                   job="job-1", title="A game")
    rec._ends = {(0, 0): list(firings)}
    return rec


def frame_reader(video, t):
    return np.zeros((1080, 1920, 3), np.uint8)


def test_at_most_two_per_check_and_twelve_per_game():
    fs = [firing(c, 100 + 10 * i, score=5 - i, end=i)
          for c in CHECKS for i in range(5)]
    picks = choose(fs)
    assert len(picks) == 12
    for c in CHECKS:
        assert sum(p.check == c for p in picks) == 2


def test_the_game_cap_keeps_the_earlier_checks():
    fs = [firing(c, 100 + 10 * i, end=i) for c in CHECKS for i in range(3)]
    picks = choose(fs, per_game=7)
    # every check's first pick, then the first check's second
    assert [p.check for p in sorted(picks, key=lambda p: CHECKS.index(p.check))] \
        == ["side.width_far"] * 2 + list(CHECKS[1:])


def test_picks_within_a_check_are_five_seconds_apart():
    fs = [firing("ovh.quiet", t, score=s, end=1)
          for t, s in [(100, 4), (102, 3), (104, 2), (120, 1)]]
    assert [p.t for p in choose(fs)] == [100.0, 120.0]


def test_the_first_pick_of_a_check_spreads_across_ends():
    fs = [firing("ovh.quiet", 100, 9, end=1), firing("ovh.quiet", 200, 8, end=1),
          firing("ovh.quiet", 300, 1, end=2)]
    assert [(p.end, p.t) for p in choose(fs)] == [(1, 100.0), (2, 300.0)]


def test_firings_without_a_box_are_dropped():
    assert choose([firing("ovh.quiet", 100, box=None)]) == []


def test_caps_apply_per_game_not_per_video():
    fs = []
    for g in (0, 1):
        # 14 firings: two of every check and a third of the first two
        for i, c in enumerate(CHECKS * 2 + CHECKS[:2]):
            fs.append(firing(c, 100 + 10 * i, game=g, end=i))
    assert len(fs) == 28
    picks = choose(fs)
    assert len(picks) == 2 * 12
    assert sorted({p.game for p in picks}) == [0, 1]
    assert [(p.game, p.t) for p in picks] == sorted((p.game, p.t) for p in picks)


def test_saving_writes_jpegs_and_rows_readable_by_all(tmp_path):
    fs = [firing("side.lone", 100, 3), firing("ovh.quiet", 200, 2),
          firing("ovh.wander", 300, 1)]
    rec = recorder_with(fs)
    n = store.save_frames(rec, root=tmp_path, video="v.ts", reader=frame_reader,
                          now=lambda: 1_700_000_000.0)
    assert n == 3
    d = store.video_dir(tmp_path, "vid")
    assert d == tmp_path / "harvest" / "fp" / "vid"
    jpgs = sorted(d.glob("*.jpg"))
    assert len(jpgs) == 3
    for p in jpgs:
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o755
    assert stat.S_IMODE(os.stat(d / "frames.jsonl").st_mode) == 0o644
    rows = store.read_rows(tmp_path, "vid")
    assert len(rows) == 3
    by_check = {r["check"]: r for r in rows}
    row = by_check["side.lone"]
    assert row["stem"] == store.stem_for("vid", fs[0])
    assert row["stem"] == "vid_sheet2_000100_00_lone"
    assert (d / f"{row['stem']}.jpg").exists()
    assert row["models"] == MODELS
    assert row["box"] == [10.0, 20.0, 50.0, 60.0]
    assert row["t"] == 100.0 and row["game"] == 0 and row["end"] == 1
    assert row["video_id"] == "vid" and row["pipeline"] == "2026.10.21.1"
    assert row["job"] == "job-1" and row["title"] == "A game"
    assert row["saved_at"].startswith("2023-11-14T22:13:20")
    img = __import__("cv2").imread(str(d / f"{row['stem']}.jpg"))
    assert img.shape == (1080, 1920, 3)


def test_a_rerun_with_the_same_models_saves_nothing(tmp_path):
    rec = recorder_with([firing("side.lone", 100)])
    assert store.save_frames(rec, root=tmp_path, video="v.ts",
                             reader=frame_reader) == 1
    assert store.save_frames(rec, root=tmp_path, video="v.ts",
                             reader=frame_reader) == 0
    other = recorder_with([firing("side.lone", 100)],
                          models={"overhead": "o-2", "side": None})
    assert store.save_frames(other, root=tmp_path, video="v.ts",
                             reader=frame_reader) == 1
    assert len(store.read_rows(tmp_path, "vid")) == 2


def test_an_unreadable_frame_is_skipped_and_the_rest_saved(tmp_path):
    fs = [firing("side.lone", 100), firing("ovh.quiet", 200),
          firing("ovh.wander", 300)]

    def reader(video, t):
        if t == 100.0:
            raise RuntimeError("decode")
        if t == 200.0:
            return None
        return frame_reader(video, t)

    n = store.save_frames(recorder_with(fs), root=tmp_path, video="v.ts",
                          reader=reader)
    assert n == 1
    assert [r["check"] for r in store.read_rows(tmp_path, "vid")] == ["ovh.wander"]


def test_a_torn_line_is_skipped(tmp_path):
    d = store.video_dir(tmp_path, "vid")
    d.mkdir(parents=True)
    good = {"stem": "a", "models": MODELS}
    (d / "frames.jsonl").write_text(json.dumps(good) + "\n" + '{"stem": "x')
    assert store.read_rows(tmp_path, "vid") == [good]
    assert store.has_models([good], MODELS)
    assert not store.has_models([good], {"overhead": "o-9", "side": None})


def test_a_row_appended_after_a_torn_line_stays_readable(tmp_path):
    d = store.video_dir(tmp_path, "vid")
    d.mkdir(parents=True)
    (d / "frames.jsonl").write_text('{"stem": "x')
    store.save_frames(recorder_with([firing("side.lone", 100)]), root=tmp_path,
                      video="v.ts", reader=frame_reader)
    assert [r["check"] for r in store.read_rows(tmp_path, "vid")] == ["side.lone"]


def test_over_the_cap_nothing_is_saved(tmp_path):
    fp = tmp_path / "harvest" / "fp"
    fp.mkdir(parents=True)
    (fp / "big.bin").write_bytes(b"\0" * 2_000_000)
    assert store.size_gb(tmp_path) > 0.001
    n = store.save_frames(recorder_with([firing("side.lone", 100)]),
                          root=tmp_path, video="v.ts", reader=frame_reader,
                          cap_gb=0.001)
    assert n == 0
    assert store.read_rows(tmp_path, "vid") == []


def test_size_of_nothing_is_zero(tmp_path):
    assert store.size_gb(tmp_path) == 0.0


def test_saving_never_raises(tmp_path):
    # no video at all, and a root that cannot be written under
    rec = recorder_with([firing("side.lone", 100)])
    assert store.save_frames(rec, root=tmp_path, reader=frame_reader) == 0
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert store.save_frames(rec, root=blocker, video="v.ts",
                             reader=frame_reader) == 0
    assert store.save_frames(None, root=tmp_path, video="v.ts") == 0
