import json

import pytest

from curling_score.harvest import plan, playlist


def entry(vid, date, sheet, duration=15596.0):
    return playlist.Entry(vid, f"{date} sheet {sheet}", duration, date, sheet)


def season(n_dates=26, sheets=(1, 2, 3, 4, 5)):
    dates = [f"2026-01-{d:02d}" for d in range(1, n_dates + 1)]
    return [entry(f"v{i:02d}{s}".ljust(11, "x"), d, s)
            for i, d in enumerate(dates) for s in sheets]


def formats(entries, height=1080, fps=30.0):
    return {e.video_id: {"chosen_h": height, "chosen_fps": fps, "chosen_id": "137"}
            for e in entries}


class TestBuildPlan:
    def test_records_every_video_once(self):
        entries = season()
        doc = plan.build(entries, formats(entries), n_val=5)
        assert len(doc["videos"]) == len(entries)
        assert len({v["video_id"] for v in doc["videos"]}) == len(entries)

    def test_holds_out_whole_dates(self):
        entries = season()
        doc = plan.build(entries, formats(entries), n_val=5)
        by_date = {}
        for v in doc["videos"]:
            by_date.setdefault(v["date"], set()).add(v["split"])
        assert all(len(splits) == 1 for splits in by_date.values())
        assert len([d for d, s in by_date.items() if s == {"val"}]) == 5

    def test_drops_low_resolution_videos_entirely(self):
        # At 720p a stone is ~14 px against ~20 px at 1080p: a different
        # detection problem wearing the same clothes.
        entries = season()
        fmts = formats(entries)
        low_date = sorted({e.date for e in entries})[2]
        for e in entries:
            if e.date == low_date:
                fmts[e.video_id]["chosen_h"] = 720
        doc = plan.build(entries, fmts, n_val=5)
        assert not [v for v in doc["videos"] if v["date"] == low_date]
        assert doc["summary"]["dropped_videos"] == 5

    def test_records_what_it_dropped_and_why(self):
        # Visible and reversible: the decision is one parameter, and the plan
        # says which games it cost.
        entries = season()
        fmts = formats(entries)
        low_date = sorted({e.date for e in entries})[2]
        for e in entries:
            if e.date == low_date:
                fmts[e.video_id]["chosen_h"] = 720
        doc = plan.build(entries, fmts, n_val=5)
        assert {d["date"] for d in doc["dropped"]} == {low_date}
        assert all(d["height"] == 720 for d in doc["dropped"])

    def test_carries_the_resolution_of_what_it_kept(self):
        entries = season()
        doc = plan.build(entries, formats(entries, height=1080), n_val=5)
        assert all(v["height"] == 1080 for v in doc["videos"])

    def test_refuses_rather_than_building_an_empty_season(self):
        entries = season()
        with pytest.raises(ValueError):
            plan.build(entries, formats(entries, height=720), n_val=5)

    def test_is_json_round_trippable(self):
        entries = season()
        doc = plan.build(entries, formats(entries), n_val=5)
        assert json.loads(json.dumps(doc)) == doc

    def test_sorted_by_date_then_sheet_so_diffs_stay_readable(self):
        entries = season()
        doc = plan.build(entries, formats(entries), n_val=5)
        keys = [(v["date"], v["sheet"]) for v in doc["videos"]]
        assert keys == sorted(keys)

    def test_summary_counts_the_splits(self):
        entries = season()
        doc = plan.build(entries, formats(entries), n_val=5)
        assert doc["summary"]["train_videos"] + doc["summary"]["val_videos"] == 130
        assert doc["summary"]["val_videos"] == 25


class TestAllVal:
    def test_a_test_set_is_all_one_split(self):
        # A held-out test set is never trained on, so there is nothing to hold
        # out from.
        entries = season(n_dates=9)
        doc = plan.build(entries, formats(entries), all_val=True)
        assert {v["split"] for v in doc["videos"]} == {"val"}
        assert doc["summary"]["train_videos"] == 0

    def test_it_needs_no_room_to_spread_hold_outs(self):
        # pick_val_dates refuses fewer than 2n dates; a test set must not be
        # subject to that at all.
        entries = season(n_dates=3)
        doc = plan.build(entries, formats(entries), n_val=5, all_val=True)
        assert doc["summary"]["val_videos"] == len(entries)
