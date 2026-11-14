"""A kept camera game's files cut to begin where its stream began: what its
standby recorded before the broadcast is not kept."""

import json

import pytest

from curling_score.cameras import capture, trim
from curling_score.ingest import frames
from tests.test_cameras_capture import h264, read_ts

ROLES = ("house_a", "house_h", "long_a", "long_h")


def recording(d, start=None):
    """Four cameras, 3 s at 60 fps, keyframes every half second, each camera's
    first packet a little after the recording's zero."""
    d.mkdir(parents=True)
    for k, role in enumerate(ROLES):
        out = capture._TsFile(d / f"{role}.ts")
        for i, (data, key) in enumerate(h264(180, gop=30, noise=True)):
            out.write(data, 0.05 * k + i / 60, key)
    return d


def test_each_file_begins_at_the_cut_on_the_one_clock(tmp_path):
    d = recording(tmp_path / "rec")
    before = {role: read_ts(d / f"{role}.ts") for role in ROLES}
    assert trim.trim_files(d, 1.2) == 1.2
    for k, role in enumerate(ROLES):
        got = read_ts(d / f"{role}.ts")
        assert frames.stream_start_s(d / f"{role}.ts") == 0.0
        # The keyframe before the cut opens the file, at its zero.
        assert got[0][:2] == (0.0, True)
        # From the cut on, every packet is where it was, less the cut.
        later = [(t - 1.2, data) for t, _, data in before[role] if t >= 1.2]
        tail = got[-len(later):]
        assert [data for _, _, data in tail] == [data for _, data in later]
        # The packet at the cut may sit a few ticks on, after the held ones.
        assert [t for t, _, _ in tail] == pytest.approx([t for t, _ in later], abs=5e-4)
        # Nothing from before the keyframe ahead of the cut is left.
        assert len(got) - len(later) <= 30


def test_it_is_read_on_the_new_clock(tmp_path):
    d = recording(tmp_path / "rec")
    trim.trim_files(d, 1.2)
    got = [t for t, _ in frames.window(d / "long_a.ts", 0.5, 1.0, 60.0)]
    assert got[0] == pytest.approx(0.5, abs=1 / 60) and len(got) >= 29


def test_a_cut_at_the_start_changes_nothing(tmp_path):
    d = recording(tmp_path / "rec")
    before = (d / "long_a.ts").read_bytes()
    assert trim.trim_files(d, 0.0) == 0.0
    assert (d / "long_a.ts").read_bytes() == before


def test_the_clock_moves_with_the_cut():
    clock = {"kind": "cameras", "aligned": "segment", "offset_s": -1700.0,
             "steps": [[0.0, -1690.0], [900.0, -1700.0], [3000.0, -1702.0]]}
    moved = trim.shift_clock(clock, 1800.0)
    assert moved["offset_s"] == 100.0
    assert moved["steps"] == [[0.0, 110.0], [0.0, 100.0], [1200.0, 98.0]]
    from curling_score import timeline

    for t in (1800.0, 2500.0, 3500.0):
        assert timeline.offset_at(moved, t - 1800.0) + (t - 1800.0) == \
            timeline.offset_at(clock, t) + t


def test_kept_files_are_cut_and_their_clock_moved(tmp_path):
    d = recording(tmp_path / "kept" / "vid")
    clock = {"kind": "cameras", "aligned": "segment", "offset_s": -1.0,
             "steps": [[0.0, -1.0]]}
    (d / "clock.json").write_text(json.dumps({"sheet": 3, "wall_started": 1000.0,
                                              "clock": clock}))
    trim.trim_kept(d, 1.2)
    kept = json.loads((d / "clock.json").read_text())
    assert kept["wall_started"] == pytest.approx(1001.2)
    assert kept["clock"]["offset_s"] == pytest.approx(0.2)
    assert kept["start_trimmed_s"] == pytest.approx(1.2)
    assert read_ts(d / "long_a.ts")[0][:2] == (0.0, True)


def _pending_dir(tmp_path):
    d = tmp_path / "kept" / "vid"
    d.mkdir(parents=True)
    (d / "long_a.ts").write_bytes(b"ts")
    (d / "clock.json").write_text(json.dumps(
        {"sheet": 3, "wall_started": 1000.0, "clock": {"offset_s": -10.0},
         "kept_at": 2000.0, trim.PENDING: True}))
    return d


def test_trim_kept_clears_the_pending_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(trim, "trim_files", lambda d, cut: cut)
    d = _pending_dir(tmp_path)
    assert trim.trim_kept(d, 5.0) == 5.0
    meta = json.loads((d / "clock.json").read_text())
    assert trim.PENDING not in meta
    assert meta["start_trimmed_s"] == 5.0 and meta["kept_at"] == 2000.0
    assert meta["wall_started"] == 1005.0


def test_a_cut_too_small_still_clears_the_flag(tmp_path):
    d = _pending_dir(tmp_path)
    assert trim.trim_kept(d, 0.2) == 0.0
    meta = json.loads((d / "clock.json").read_text())
    assert trim.PENDING not in meta and "start_trimmed_s" not in meta


def test_clear_pending_leaves_a_clock_without_the_flag_alone(tmp_path):
    d = _pending_dir(tmp_path)
    trim.clear_pending(d)
    trim.clear_pending(d)
    assert trim.PENDING not in json.loads((d / "clock.json").read_text())
    trim.clear_pending(tmp_path / "nowhere")        # a directory gone is no error
