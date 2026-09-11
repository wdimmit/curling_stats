import re

import pytest

from curling_score.train import labels


def box(cls=0, cx=0.5, cy=0.5, w=0.07, h=0.04):
    return labels.Box(cls, cx, cy, w, h)


def write(tmp_path, split, name, boxes):
    img = tmp_path / "images" / split
    lbl = tmp_path / "labels" / split
    img.mkdir(parents=True, exist_ok=True)
    lbl.mkdir(parents=True, exist_ok=True)
    (img / f"{name}.jpg").write_bytes(b"jpeg")
    (lbl / f"{name}.txt").write_text(
        "\n".join(f"{b.cls} {b.cx} {b.cy} {b.w} {b.h}" for b in boxes) + "\n")


class TestParseName:
    def test_it_recovers_the_sequence_and_time(self):
        assert labels.parse_name("g2e1_b_008117_00") == ("g2e1_b", 8117.0)

    def test_it_keeps_the_fractional_part(self):
        assert labels.parse_name("g1e3_t_000042_50") == ("g1e3_t", 42.5)

    def test_something_else_is_not_a_frame(self):
        assert labels.parse_name("classes") is None


class TestLoadSplit:
    def test_frames_come_back_in_time_order_per_sequence(self, tmp_path):
        for t in (300, 100, 200):
            write(tmp_path, "train", f"g1e1_b_{t:06d}_00", [box()])
        got = labels.load_split(tmp_path, "train")
        assert [f.t for f in got["g1e1_b"]] == [100.0, 200.0, 300.0]

    def test_sequences_are_kept_apart(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00", [box()])
        write(tmp_path, "train", "g2e4_t_000100_00", [box()])
        assert set(labels.load_split(tmp_path, "train")) == {"g1e1_b", "g2e4_t"}

    def test_the_boxes_are_read_back(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cls=1, cx=0.25, cy=0.75)])
        f = labels.load_split(tmp_path, "train")["g1e1_b"][0]
        assert f.boxes[0].color == "yellow"
        assert f.boxes[0].cx == pytest.approx(0.25)


class TestSuspectLabels:
    """A stone stays where it is. A label in one frame and gone from the next
    was never a stone, whatever wrote it -- which is how a player's red shoes
    got into the training set as a red stone."""

    def sequence(self, tmp_path, extra_at=None):
        for i in range(5):
            boxes = [box(cx=0.3), box(cls=1, cx=0.6)]
            if extra_at is not None and i == extra_at:
                boxes.append(box(cx=0.8, cy=0.2))
            write(tmp_path, "train", f"g1e1_b_{100 + i:06d}_00", boxes)
        return labels.load_split(tmp_path, "train")

    def test_a_label_present_in_one_frame_only_is_flagged(self, tmp_path):
        got = labels.find_suspect_labels(self.sequence(tmp_path, extra_at=2))
        assert len(got) == 1
        assert got[0].frame.t == 102.0
        assert got[0].marked == (2,)

    def test_labels_that_persist_are_not_flagged(self, tmp_path):
        assert labels.find_suspect_labels(self.sequence(tmp_path)) == []

    def test_a_stone_in_flight_is_not_flagged(self, tmp_path):
        """Saved frames are a second apart and a stone in flight crosses about
        a quarter of the panel, so judging by position alone flags every
        delivery. It flagged 507 frames in the cleaned set, nearly all of them
        real stones on their way down the sheet."""
        for i in range(6):
            write(tmp_path, "train", f"g1e1_b_{100 + i:06d}_00",
                  [box(cx=0.3), box(cls=1, cx=0.5, cy=0.85 - 0.15 * i)])
        assert labels.find_suspect_labels(labels.load_split(tmp_path, "train")) == []

    def test_more_labels_than_an_end_can_hold_is_flagged(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cx=0.01 * i) for i in range(20)])
        got = labels.find_suspect_labels(labels.load_split(tmp_path, "train"))
        assert len(got) == 1
        assert "only 16" in got[0].reason

    def test_the_worst_come_first(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cx=0.01 * i) for i in range(20)])
        for i in range(1, 4):
            write(tmp_path, "train", f"g1e1_b_{100 + i:06d}_00",
                  [box(cx=0.3), box(cls=1, cx=0.6)] + ([box(cx=0.9)] if i == 2 else []))
        got = labels.find_suspect_labels(labels.load_split(tmp_path, "train"))
        assert got == sorted(got, key=lambda x: -x.confidence)


class TestSampling:
    def test_a_spread_is_taken_across_each_sequence(self, tmp_path):
        for i in range(100):
            write(tmp_path, "train", f"g1e1_b_{i:06d}_00", [box()])
        got = labels.sample_frames(labels.load_split(tmp_path, "train"), every=25)
        assert [f.t for f in got] == [0.0, 25.0, 50.0, 75.0]


class TestFindingsCarryTheirEvidence:
    """The claim is that a label comes and goes, which cannot be judged from
    one frame. The neighbours are the evidence, so they travel with it."""

    def test_the_neighbouring_frames_are_attached(self, tmp_path):
        for i in range(5):
            boxes = [box(cx=0.3), box(cls=1, cx=0.6)]
            if i == 2:
                boxes.append(box(cx=0.8, cy=0.2))
            write(tmp_path, "train", f"g1e1_b_{100 + i:06d}_00", boxes)
        got = labels.find_suspect_labels(labels.load_split(tmp_path, "train"))
        assert len(got) == 1
        assert sorted(f.t for f in got[0].context) == [101.0, 103.0]

    def test_a_frame_at_the_start_has_only_one_neighbour(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cx=0.01 * i) for i in range(20)])
        write(tmp_path, "train", "g1e1_b_000101_00", [box(cx=0.3)])
        got = labels.find_suspect_labels(labels.load_split(tmp_path, "train"))
        assert got[0].context and len(got[0].context) == 1


class TestRejectingLabelsByHand:
    """Static rules kept producing partial fixes -- each removed some bad labels
    and left others, and two removed good labels as well. A person tells a
    stone from a knee at a glance, so let them say so directly."""

    def setup_set(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cls=0, cx=0.30, cy=0.40), box(cls=1, cx=0.60, cy=0.70)])
        write(tmp_path, "train", "g1e1_b_000101_00",
              [box(cls=0, cx=0.31, cy=0.41)])
        return labels.load_split(tmp_path, "train")

    def test_a_key_names_the_frame_and_the_box(self):
        b = box(cls=1, cx=0.034, cy=0.637)
        assert labels.reject_key("g1e1_b_000422_50", b) == \
            "g1e1_b_000422_50|0.034,0.637"

    def test_a_rejected_label_is_deleted(self, tmp_path):
        self.setup_set(tmp_path)
        got = labels.apply_rejections(
            tmp_path, "train", ["g1e1_b_000100_00|0.300,0.400"])
        assert got[0] == 1
        left = labels.load_split(tmp_path, "train")["g1e1_b"]
        first = [f for f in left if f.t == 100.0][0]
        assert [b.color for b in first.boxes] == ["yellow"]

    def test_the_others_are_untouched(self, tmp_path):
        self.setup_set(tmp_path)
        labels.apply_rejections(
            tmp_path, "train", ["g1e1_b_000100_00|0.300,0.400"])
        left = labels.load_split(tmp_path, "train")["g1e1_b"]
        assert len([f for f in left if f.t == 101.0][0].boxes) == 1

    def test_a_frame_left_with_nothing_is_dropped(self, tmp_path):
        self.setup_set(tmp_path)
        removed, _rw, emptied = labels.apply_rejections(
            tmp_path, "train", ["g1e1_b_000101_00|0.310,0.410"])
        assert (removed, emptied) == (1, 1)
        assert not (tmp_path / "images" / "train"
                    / "g1e1_b_000101_00.jpg").exists()
        assert "g1e1_b_000101_00" not in {
            f.image.stem for f in labels.load_split(tmp_path, "train")["g1e1_b"]}

    def test_rejecting_nothing_changes_nothing(self, tmp_path):
        self.setup_set(tmp_path)
        assert labels.apply_rejections(tmp_path, "train", []) == (0, 0, 0)

    def test_a_key_that_matches_no_box_is_ignored(self, tmp_path):
        self.setup_set(tmp_path)
        assert labels.apply_rejections(
            tmp_path, "train", ["g1e1_b_000100_00|0.999,0.999"])[0] == 0


class TestClickablePage:
    def test_every_label_becomes_a_clickable_box(self, tmp_path, monkeypatch):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cls=0, cx=0.30, cy=0.40), box(cls=1, cx=0.60, cy=0.70)])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        page, n = labels.render_clickable(frames, tmp_path / "fix")
        html = page.read_text()
        assert n == 1
        assert html.count('class="bx') == 2
        assert 'data-k="g1e1_b_000100_00|0.300,0.400"' in html
        assert 'data-k="g1e1_b_000100_00|0.600,0.700"' in html

    def test_the_box_is_positioned_where_the_label_is(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cls=0, cx=0.50, cy=0.50, w=0.10, h=0.10)])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        page, _n = labels.render_clickable(frames, tmp_path / "fix")
        assert "left:45.000%;top:45.000%;width:10.000%;height:10.000%" in \
            page.read_text()

    def test_the_image_is_copied_beside_the_page(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00", [box()])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        labels.render_clickable(frames, tmp_path / "fix")
        assert (tmp_path / "fix" / "frames" / "g1e1_b_000100_00.jpg").exists()


class TestAddingLabels:
    """A stone with no label teaches the model that it is not a stone, which is
    the failure behind every missing delivery traced by eye. Additions matter
    at least as much as removals."""

    def setup_set(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cls=0, cx=0.30, cy=0.40, w=0.07, h=0.04)])
        write(tmp_path, "train", "g1e1_b_000101_00",
              [box(cls=1, cx=0.60, cy=0.70, w=0.07, h=0.04)])

    def test_an_addition_becomes_a_label(self, tmp_path):
        self.setup_set(tmp_path)
        c = labels.apply_edits(tmp_path, "train", [],
                               ["g1e1_b_000100_00|0.800,0.250|1"])
        assert c["added"] == 1
        f = [x for x in labels.load_split(tmp_path, "train")["g1e1_b"]
             if x.t == 100.0][0]
        assert sorted(b.color for b in f.boxes) == ["red", "yellow"]

    def test_it_takes_the_size_the_frame_uses(self, tmp_path):
        self.setup_set(tmp_path)
        labels.apply_edits(tmp_path, "train", [],
                           ["g1e1_b_000100_00|0.800,0.250|1"])
        f = [x for x in labels.load_split(tmp_path, "train")["g1e1_b"]
             if x.t == 100.0][0]
        assert all(abs(b.w - 0.07) < 1e-6 for b in f.boxes)

    def test_rejections_and_additions_apply_together(self, tmp_path):
        self.setup_set(tmp_path)
        c = labels.apply_edits(
            tmp_path, "train",
            ["g1e1_b_000100_00|0.300,0.400"],
            ["g1e1_b_000100_00|0.800,0.250|1"])
        assert (c["removed"], c["added"]) == (1, 1)
        f = [x for x in labels.load_split(tmp_path, "train")["g1e1_b"]
             if x.t == 100.0][0]
        assert [b.color for b in f.boxes] == ["yellow"]

    def test_an_addition_to_a_vanished_frame_is_skipped(self, tmp_path):
        self.setup_set(tmp_path)
        c = labels.apply_edits(tmp_path, "train", [],
                               ["g1e1_b_009999_00|0.500,0.500|0"])
        assert c["added"] == 0 and c["missing_frames"] == 1

    def test_the_old_flat_format_still_loads(self):
        assert labels.parse_edits(["a|0.1,0.2"]) == (["a|0.1,0.2"], [])

    def test_the_new_format_carries_both(self):
        got = labels.parse_edits({"reject": ["a|0.1,0.2"],
                                  "add": ["b|0.3,0.4|1"]})
        assert got == (["a|0.1,0.2"], ["b|0.3,0.4|1"])


class TestClickablePageSupportsAdding:
    def test_the_frame_carries_its_box_size(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00",
              [box(cls=0, cx=0.30, cy=0.40, w=0.081, h=0.047)])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        page, _n = labels.render_clickable(frames, tmp_path / "fix")
        html = page.read_text()
        assert 'data-stem="g1e1_b_000100_00"' in html
        assert 'data-w="0.081000"' in html and 'data-h="0.047000"' in html

    def test_the_page_offers_both_colours(self, tmp_path):
        write(tmp_path, "train", "g1e1_b_000100_00", [box()])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        page, _n = labels.render_clickable(frames, tmp_path / "fix")
        assert 'data-mode="red"' in page.read_text()
        assert 'data-mode="yellow"' in page.read_text()

    def test_the_export_file_names_its_scope(self, tmp_path):
        # "label-edits.json" in ~/Downloads quietly overwriting itself is how
        # one split's session gets attributed to another.
        write(tmp_path, "train", "g1e1_b_000100_00", [box()])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        labels.render_clickable(frames, tmp_path / "fix", scope="ds11:train")
        js = (tmp_path / "fix" / "review.js").read_text()
        assert 'a.download = "label-edits-"' in js
        assert "SCOPE.replace" in js


class TestEditsAreScopedPerSplit:
    """One storage key shared across pages let a session on the validation
    split leak 110 edits into an export meant for the training split."""

    def page(self, tmp_path, scope):
        write(tmp_path, "train", "g1e1_b_000100_00", [box()])
        frames = labels.load_split(tmp_path, "train")["g1e1_b"]
        out = tmp_path / f"fix_{scope.replace(':', '_')}"
        page, _n = labels.render_clickable(frames, out, scope=scope)
        return page.read_text()

    def test_the_scope_reaches_the_page(self, tmp_path):
        assert 'data-scope="ds8:val"' in self.page(tmp_path, "ds8:val")

    def test_two_splits_do_not_share_a_key(self, tmp_path):
        a = self.page(tmp_path, "ds8:train")
        b = self.page(tmp_path, "ds8:val")
        assert 'data-scope="ds8:train"' in a
        assert 'data-scope="ds8:val"' in b
        assert a != b


class TestParseEditsFull:
    def test_reads_the_reviewed_set(self):
        got = labels.parse_edits_full(
            {"reject": ["a|0.1,0.2"], "add": [], "reviewed": ["a", "b"]})
        assert got.reviewed == ("a", "b")

    def test_an_export_with_no_reviewed_set_is_still_valid(self):
        # Exports written before the reviewed state existed must keep working.
        got = labels.parse_edits_full({"reject": ["a|0.1,0.2"], "add": []})
        assert got.reject == ("a|0.1,0.2",) and got.reviewed == ()

    def test_a_bare_list_is_still_rejections(self):
        got = labels.parse_edits_full(["a|0.1,0.2"])
        assert got.reject == ("a|0.1,0.2",) and got.add == ()

    def test_carries_the_scope_so_a_leak_can_be_spotted(self):
        # A session on the val split once leaked 110 edits into an export meant
        # for train. The file could not say so; now it can.
        assert labels.parse_edits_full({"scope": "ds11:val"}).scope == "ds11:val"


class TestMergeEdits:
    def test_unions_across_sessions(self):
        merged = labels.merge_edits({"reject": ["a"], "reviewed": ["f1"]},
                                    {"reject": ["b"], "reviewed": ["f2"]})
        assert set(merged.reject) == {"a", "b"}
        assert set(merged.reviewed) == {"f1", "f2"}

    def test_does_not_double_count_a_repeated_edit(self):
        merged = labels.merge_edits({"reject": ["a"]}, {"reject": ["a"]})
        assert merged.reject == ("a",)

    def test_is_order_independent(self):
        a = labels.merge_edits({"reject": ["b"]}, {"reject": ["a"]})
        b = labels.merge_edits({"reject": ["a"]}, {"reject": ["b"]})
        assert a == b


class TestApplyEditsNegatives:
    def test_keeps_a_confirmed_empty_frame_when_asked(self, tmp_path):
        # The frames a reviewer strips to zero are the model's false positives
        # on people -- the most valuable negatives in the set. Deleting them
        # throws away the correction that was just made.
        write(tmp_path, "train", "v_t_000010_00", [box(cx=0.5, cy=0.5)])
        counts = labels.apply_edits(
            tmp_path, "train", ["v_t_000010_00|0.500,0.500"], keep_empty=True)
        assert counts["kept_empty"] == 1
        assert (tmp_path / "images" / "train" / "v_t_000010_00.jpg").exists()
        assert (tmp_path / "labels" / "train" / "v_t_000010_00.txt").read_text() == ""

    def test_still_deletes_by_default(self, tmp_path):
        write(tmp_path, "train", "v_t_000010_00", [box(cx=0.5, cy=0.5)])
        counts = labels.apply_edits(
            tmp_path, "train", ["v_t_000010_00|0.500,0.500"])
        assert counts["emptied"] == 1
        assert not (tmp_path / "images" / "train" / "v_t_000010_00.jpg").exists()

    def test_can_add_a_box_to_a_frame_that_had_none(self, tmp_path):
        write(tmp_path, "train", "v_t_000010_00", [])
        counts = labels.apply_edits(
            tmp_path, "train", [], ["v_t_000010_00|0.300,0.400|1"],
            keep_empty=True, box_for=lambda stem: (0.09, 0.05))
        assert counts["added"] == 1
        text = (tmp_path / "labels" / "train" / "v_t_000010_00.txt").read_text()
        assert text.split() == ["1", "0.300000", "0.400000", "0.090000", "0.050000"]


class TestApplyEditsBoxSizing:
    def test_takes_an_added_boxs_size_from_the_caller(self, tmp_path):
        # Across 120 videos there are 120 values of px_per_m, so one median
        # box size borrowed from the set is wrong nearly everywhere.
        write(tmp_path, "train", "v_t_000010_00", [box(w=0.07, h=0.04)])
        labels.apply_edits(tmp_path, "train", [], ["v_t_000010_00|0.300,0.400|0"],
                           box_for=lambda stem: (0.12, 0.08))
        rows = (tmp_path / "labels" / "train" / "v_t_000010_00.txt").read_text()
        added = [r for r in rows.strip().split("\n") if r.startswith("0 0.300")]
        assert added[0].split()[3:] == ["0.120000", "0.080000"]

    def test_falls_back_to_the_frames_own_boxes(self, tmp_path):
        write(tmp_path, "train", "v_t_000010_00", [box(w=0.07, h=0.04)])
        labels.apply_edits(tmp_path, "train", [], ["v_t_000010_00|0.300,0.400|0"])
        rows = (tmp_path / "labels" / "train" / "v_t_000010_00.txt").read_text()
        added = [r for r in rows.strip().split("\n") if r.startswith("0 0.300")]
        assert added[0].split()[3:] == ["0.070000", "0.040000"]


class TestDropUnreviewed:
    def test_removes_frames_nobody_looked_at(self, tmp_path):
        # An unreviewed frame carries detector labels. Letting it into the set
        # silently is what makes the whole exercise circular again.
        write(tmp_path, "train", "v_t_000010_00", [box()])
        write(tmp_path, "train", "v_t_000020_00", [box()])
        counts = labels.apply_edits(tmp_path, "train", [], (),
                                    reviewed=["v_t_000010_00"],
                                    drop_unreviewed=True)
        assert counts["unreviewed"] == 1
        assert (tmp_path / "images" / "train" / "v_t_000010_00.jpg").exists()
        assert not (tmp_path / "images" / "train" / "v_t_000020_00.jpg").exists()

    def test_leaves_everything_alone_when_not_asked(self, tmp_path):
        write(tmp_path, "train", "v_t_000010_00", [box()])
        write(tmp_path, "train", "v_t_000020_00", [box()])
        labels.apply_edits(tmp_path, "train", [], (), reviewed=["v_t_000010_00"])
        assert (tmp_path / "images" / "train" / "v_t_000020_00.jpg").exists()

    def test_counts_coverage_without_changing_anything(self, tmp_path):
        write(tmp_path, "train", "v_t_000010_00", [box()])
        write(tmp_path, "train", "v_t_000020_00", [box()])
        seen, total, missing = labels.coverage(tmp_path, "train", ["v_t_000010_00"])
        assert (seen, total) == (1, 2)
        assert missing == ["v_t_000020_00"]


def many(tmp_path, n, split="train"):
    for i in range(n):
        write(tmp_path, split, f"v_t_{i:06d}_00", [box()])
    return [f for seq in labels.load_split(tmp_path, split).values() for f in seq]


class TestPagination:
    def test_a_small_set_is_still_one_page(self, tmp_path):
        page, n = labels.render_clickable(many(tmp_path, 5), tmp_path / "fix",
                                          per_page=200)
        assert page.name == "index.html" and n == 5
        assert not list((tmp_path / "fix").glob("page-*.html"))
        assert "<figure" in page.read_text()

    def test_a_large_set_is_split(self, tmp_path):
        # 442 frames already made a 567 KB document; 2,000 would be ~2.5 MB
        # with eight thousand absolutely positioned children.
        index, n = labels.render_clickable(many(tmp_path, 25), tmp_path / "fix",
                                           per_page=10)
        assert n == 25
        pages = sorted(p.name for p in (tmp_path / "fix").glob("page-*.html"))
        assert pages == ["page-0001.html", "page-0002.html", "page-0003.html"]
        assert "<figure" not in index.read_text()

    def test_every_frame_lands_on_exactly_one_page(self, tmp_path):
        labels.render_clickable(many(tmp_path, 25), tmp_path / "fix", per_page=10)
        seen = []
        for pg in sorted((tmp_path / "fix").glob("page-*.html")):
            seen += re.findall(r'<figure[^>]*data-stem="([^"]+)"', pg.read_text())
        assert len(seen) == len(set(seen)) == 25

    def test_the_pages_link_to_each_other(self, tmp_path):
        labels.render_clickable(many(tmp_path, 25), tmp_path / "fix", per_page=10)
        mid = (tmp_path / "fix" / "page-0002.html").read_text()
        assert "page-0001.html" in mid and "page-0003.html" in mid
        assert "index.html" in mid

    def test_the_contents_page_lists_them_all(self, tmp_path):
        index, _ = labels.render_clickable(many(tmp_path, 25), tmp_path / "fix",
                                           per_page=10)
        text = index.read_text()
        assert all(f"page-{i:04d}.html" in text for i in (1, 2, 3))

    def test_all_pages_share_one_set_of_keys(self, tmp_path):
        # Otherwise an export from page 3 would hold only page 3's work.
        labels.render_clickable(many(tmp_path, 25), tmp_path / "fix",
                                per_page=10, scope="ds11:train")
        for pg in (tmp_path / "fix").glob("page-*.html"):
            assert 'data-scope="ds11:train"' in pg.read_text()

    def test_every_page_knows_the_whole_total(self, tmp_path):
        labels.render_clickable(many(tmp_path, 25), tmp_path / "fix", per_page=10)
        for pg in (tmp_path / "fix").glob("page-*.html"):
            assert 'data-total="25"' in pg.read_text()

    def test_the_script_is_written_once_not_inlined_per_page(self, tmp_path):
        labels.render_clickable(many(tmp_path, 25), tmp_path / "fix", per_page=10)
        assert (tmp_path / "fix" / "review.js").exists()
        assert (tmp_path / "fix" / "review.css").exists()
        page = (tmp_path / "fix" / "page-0001.html").read_text()
        assert 'src="review.js"' in page and "localStorage" not in page


class TestReviewedState:
    def test_each_frame_has_a_done_tick(self, tmp_path):
        page, _ = labels.render_clickable(many(tmp_path, 3), tmp_path / "fix")
        assert page.read_text().count('type="checkbox" class="seen"') == 3

    def test_the_page_can_be_ticked_in_one_click(self, tmp_path):
        # The throughput lever: most frames are right, so the common action
        # must be one click per page, not one per frame.
        page, _ = labels.render_clickable(many(tmp_path, 3), tmp_path / "fix")
        assert 'id="allseen"' in page.read_text()

    def test_reviewed_frames_can_be_hidden_to_resume_a_session(self, tmp_path):
        page, _ = labels.render_clickable(many(tmp_path, 3), tmp_path / "fix")
        assert 'id="hide"' in page.read_text()

    def test_the_export_carries_the_reviewed_set(self, tmp_path):
        labels.render_clickable(many(tmp_path, 3), tmp_path / "fix")
        assert "reviewed: [...seen]" in (tmp_path / "fix" / "review.js").read_text()

    def test_state_is_read_modify_written_so_two_tabs_do_not_clobber(self, tmp_path):
        labels.render_clickable(many(tmp_path, 3), tmp_path / "fix")
        js = (tmp_path / "fix" / "review.js").read_text()
        assert "function mutate(" in js
        assert 'window.addEventListener("storage"' in js


class TestFrameMeta:
    def test_a_deep_link_reaches_the_card(self, tmp_path):
        # Judging an ambiguous box from one still is guesswork; the moving
        # video settles it.
        write(tmp_path, "train", "v_t_003204_40", [box()])
        frames = labels.load_split(tmp_path, "train")["v_t"]
        m = {"v_t_003204_40": labels.FrameMeta(url="https://youtu.be/abc?t=3204")}
        page, _ = labels.render_clickable(frames, tmp_path / "fix", meta=m)
        assert 'href="https://youtu.be/abc?t=3204"' in page.read_text()

    def test_the_box_size_comes_from_the_manifest(self, tmp_path):
        write(tmp_path, "train", "v_t_003204_40", [])
        frames = labels.load_split(tmp_path, "train")["v_t"]
        m = {"v_t_003204_40": labels.FrameMeta(box=(0.11, 0.06))}
        page, _ = labels.render_clickable(frames, tmp_path / "fix", meta=m)
        assert 'data-w="0.110000"' in page.read_text()

    def test_an_empty_frame_says_so(self, tmp_path):
        write(tmp_path, "train", "v_t_003204_40", [])
        frames = labels.load_split(tmp_path, "train")["v_t"]
        page, _ = labels.render_clickable(frames, tmp_path / "fix")
        assert "no labels" in page.read_text()

    def test_a_motion_frame_shows_its_neighbours(self, tmp_path):
        # A moving stone and a red shoe look alike in one still. That is the
        # whole reason the flight test exists, so the reviewer gets three.
        write(tmp_path, "train", "v_t_003204_40", [box()])
        near = tmp_path / "images" / "train" / "v_t_003204_00.jpg"
        near.write_bytes(b"jpeg")
        frames = labels.load_split(tmp_path, "train")["v_t"]
        m = {"v_t_003204_40": labels.FrameMeta(kind="motion", neighbours=(near,))}
        page, _ = labels.render_clickable(
            [f for f in frames if f.image.stem == "v_t_003204_40"],
            tmp_path / "fix", meta=m)
        html = page.read_text()
        assert 'class="nb"' in html and "v_t_003204_00.jpg" in html
        assert 'class="motion"' in html


class TestReviewLegibility:
    def test_panels_render_at_about_native_size(self, tmp_path):
        # The crops are ~297 px wide and a stone is ~18 px across. Shrinking
        # them is how a stone that should have been labelled gets missed, and
        # spotting those is the whole job.
        labels.render_clickable(many(tmp_path, 2), tmp_path / "fix")
        css = (tmp_path / "fix" / "review.css").read_text()
        assert "minmax(300px,1fr)" in css
