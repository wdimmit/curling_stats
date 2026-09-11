import numpy as np
import pytest

from curling_score.harvest import build as B
from curling_score.harvest import candidates as C
from curling_score.harvest import manifest as M
from curling_score.train.dataset import Label


def pool_dir(tmp_path, stems, size=(60, 40)):
    root = tmp_path / "pool"
    for vid, stem in stems:
        d = root / vid
        d.mkdir(parents=True, exist_ok=True)
        import cv2
        cv2.imwrite(str(d / f"{stem}.jpg"), np.full((size[0], size[1], 3), 200, np.uint8))
    return root


def cand(vid, t, labels=(), kind="grid"):
    return C.Candidate(video_id=vid, panel="top", t_abs=t, clip_start_s=90.0,
                       kind=kind, n_red=len(labels), n_yellow=0,
                       flight_id=None, lighting="lit", labels=tuple(labels))


def doc_for(cands, splits):
    by = {}
    for c in cands:
        by.setdefault(c.video_id, ([], {}))[0].append(c)
    return M.build_manifest(by, splits=splits, quota=C.QUOTA)


class TestBuild:
    def test_writes_images_and_labels_into_the_split(self, tmp_path):
        c = cand("v1", 100.0, [Label(0, 0.5, 0.5, 0.07, 0.04)])
        pool = pool_dir(tmp_path, [("v1", c.stem)])
        out = tmp_path / "ds"
        stats = B.build(doc_for([c], {"v1": "train"}), pool, out)
        assert stats["written"] == 1
        assert (out / "images" / "train" / f"{c.stem}.jpg").exists()
        assert (out / "labels" / "train" / f"{c.stem}.txt").read_text().startswith("0 ")

    def test_an_empty_frame_gets_an_empty_label_file(self, tmp_path):
        # The whole point of the empty bin: the model has never once been shown
        # a frame whose right answer is "nothing".
        c = cand("v1", 100.0, [])
        pool = pool_dir(tmp_path, [("v1", c.stem)])
        out = tmp_path / "ds"
        B.build(doc_for([c], {"v1": "train"}), pool, out)
        assert (out / "labels" / "train" / f"{c.stem}.txt").read_text() == ""
        assert (out / "images" / "train" / f"{c.stem}.jpg").exists()

    def test_val_frames_go_to_the_val_split(self, tmp_path):
        a, b = cand("v1", 100.0, []), cand("v2", 100.0, [])
        pool = pool_dir(tmp_path, [("v1", a.stem), ("v2", b.stem)])
        out = tmp_path / "ds"
        B.build(doc_for([a, b], {"v1": "train", "v2": "val"}), pool, out)
        assert (out / "images" / "train" / f"{a.stem}.jpg").exists()
        assert (out / "images" / "val" / f"{b.stem}.jpg").exists()

    def test_writes_a_relocatable_yaml(self, tmp_path):
        c = cand("v1", 100.0, [])
        pool = pool_dir(tmp_path, [("v1", c.stem)])
        out = tmp_path / "ds"
        B.build(doc_for([c], {"v1": "train"}), pool, out)
        text = (out / "curling.yaml").read_text()
        assert "path:" not in text and "train: images/train" in text

    def test_reports_a_frame_the_pool_has_lost(self, tmp_path):
        # A manifest naming a frame that is not there means the pool and the
        # manifest disagree, which must be loud, not a quietly smaller set.
        c = cand("v1", 100.0, [])
        pool = pool_dir(tmp_path, [])
        (pool / "v1").mkdir(parents=True, exist_ok=True)
        out = tmp_path / "ds"
        stats = B.build(doc_for([c], {"v1": "train"}), pool, out)
        assert stats["missing"] == 1 and stats["written"] == 0

    def test_is_repeatable(self, tmp_path):
        c = cand("v1", 100.0, [Label(0, 0.5, 0.5, 0.07, 0.04)])
        pool = pool_dir(tmp_path, [("v1", c.stem)])
        doc = doc_for([c], {"v1": "train"})
        first = B.build(doc, pool, tmp_path / "a")
        second = B.build(doc, pool, tmp_path / "b")
        assert first == second
        assert ((tmp_path / "a" / "labels" / "train" / f"{c.stem}.txt").read_text()
                == (tmp_path / "b" / "labels" / "train" / f"{c.stem}.txt").read_text())

    def test_starts_from_a_clean_split(self, tmp_path):
        # Rebuilding after dropping a video must not leave its frames behind.
        c = cand("v1", 100.0, [])
        pool = pool_dir(tmp_path, [("v1", c.stem)])
        out = tmp_path / "ds"
        B.build(doc_for([c], {"v1": "train"}), pool, out)
        stale = out / "images" / "train" / "gone_t_000001_00.jpg"
        stale.write_bytes(b"jpeg")
        B.build(doc_for([c], {"v1": "train"}), pool, out)
        assert not stale.exists()
