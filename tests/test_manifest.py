import json

import pytest

from curling_score.harvest import candidates as C
from curling_score.harvest import manifest as M
from curling_score.train.dataset import Label


def cand(t=100.0, kind="grid", labels=(), vid="v", panel="top", n_red=1, n_yellow=1):
    return C.Candidate(video_id=vid, panel=panel, t_abs=t, clip_start_s=90.0,
                       kind=kind, n_red=n_red, n_yellow=n_yellow,
                       flight_id=2 if kind == "motion" else None,
                       lighting="lit", labels=tuple(labels))


class TestCandidateJson:
    def test_round_trips(self):
        c = cand(labels=[Label(0, 0.1, 0.2, 0.07, 0.04)])
        assert M.candidate_from_json(json.loads(json.dumps(M.candidate_to_json(c)))) == c

    def test_keeps_label_precision(self):
        c = cand(labels=[Label(1, 0.123456, 0.654321, 0.070001, 0.040001)])
        back = M.candidate_from_json(M.candidate_to_json(c))
        assert back.labels[0] == c.labels[0]

    def test_carries_the_stem_so_it_can_be_matched_without_recomputing(self):
        # Hand edits are keyed by stem. If a rebuild recomputes it differently,
        # every correction silently stops matching.
        assert M.candidate_to_json(cand(t=3204.4))["stem"] == "v_t_003204_40"


class TestManifest:
    def test_records_what_was_chosen_and_what_was_missed(self):
        doc = M.build_manifest(
            {"v1": ([cand(vid="v1")], {"busy": 2, "backfilled": 2})},
            splits={"v1": "train"}, quota=C.QUOTA)
        assert doc["videos"]["v1"]["shortfall"] == {"busy": 2, "backfilled": 2}
        assert doc["summary"]["frames"] == 1

    def test_counts_the_split_and_the_bins(self):
        doc = M.build_manifest(
            {"a": ([cand(vid="a", kind="motion"), cand(vid="a", t=200.0)], {}),
             "b": ([cand(vid="b")], {})},
            splits={"a": "train", "b": "val"}, quota=C.QUOTA)
        assert doc["summary"]["train_frames"] == 2
        assert doc["summary"]["val_frames"] == 1
        assert doc["summary"]["bins"]["motion"] == 1

    def test_round_trips_through_json(self):
        doc = M.build_manifest({"v1": ([cand(vid="v1")], {})},
                               splits={"v1": "train"}, quota=C.QUOTA)
        assert json.loads(json.dumps(doc)) == doc

    def test_selected_frames_can_be_read_back_as_candidates(self):
        doc = M.build_manifest(
            {"v1": ([cand(vid="v1", labels=[Label(0, 0.1, 0.2, 0.07, 0.04)])], {})},
            splits={"v1": "train"}, quota=C.QUOTA)
        got = list(M.iter_frames(doc))
        assert len(got) == 1
        split, c = got[0]
        assert split == "train" and c.labels[0].cls == 0

    def test_refuses_a_video_with_no_split(self):
        with pytest.raises(KeyError):
            M.build_manifest({"v1": ([cand(vid="v1")], {})}, splits={}, quota=C.QUOTA)


class TestReviewFields:
    def test_the_box_size_survives(self):
        c = C.Candidate(video_id="v", panel="top", t_abs=1.0, clip_start_s=0.0,
                        kind="grid", n_red=0, n_yellow=0, box_wh=(0.11, 0.06))
        assert M.candidate_from_json(M.candidate_to_json(c)).box_wh == (0.11, 0.06)

    def test_the_neighbours_survive(self):
        c = C.Candidate(video_id="v", panel="top", t_abs=1.0, clip_start_s=0.0,
                        kind="motion", n_red=0, n_yellow=0,
                        neighbours=("v_t_000000_80", "v_t_000001_20"))
        back = M.candidate_from_json(M.candidate_to_json(c))
        assert back.neighbours == ("v_t_000000_80", "v_t_000001_20")

    def test_an_old_manifest_without_them_still_loads(self):
        row = M.candidate_to_json(C.Candidate("v", "top", 1.0, 0.0, "grid", 0, 0))
        del row["box_wh"], row["neighbours"]
        assert M.candidate_from_json(row).box_wh == ()


class TestExtraCannotClobber:
    def test_refuses_to_overwrite_the_frame_index(self):
        # extra={"videos": path} once replaced the whole frame index with a
        # string; the failure surfaced two stages later as an unrelated
        # TypeError in the build.
        with pytest.raises(ValueError, match="videos"):
            M.build_manifest({"v1": ([cand(vid="v1")], {})},
                             splits={"v1": "train"}, quota=C.QUOTA,
                             extra={"videos": "somewhere.json"})

    def test_a_non_colliding_key_is_fine(self):
        doc = M.build_manifest({"v1": ([cand(vid="v1")], {})},
                               splits={"v1": "train"}, quota=C.QUOTA,
                               extra={"videos_file": "somewhere.json"})
        assert doc["videos_file"] == "somewhere.json"
        assert isinstance(doc["videos"], dict)
