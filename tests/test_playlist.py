import pytest

from curling_score.harvest import playlist


def flat(vid, title, duration=15596):
    """One entry as yt-dlp's --flat-playlist hands it over."""
    return {"id": vid, "title": title, "duration": duration}


SEASON = "Tuesday Super League 2025-2026"


class TestParseDate:
    def test_reads_a_spring_date_as_the_second_season_year(self):
        assert playlist.parse_date(f"3/24 - Sheet 2 - {SEASON}") == "2026-03-24"

    def test_reads_an_autumn_date_as_the_first_season_year(self):
        # The season spans a new year, so the month is the only thing that says
        # which half of "2025-2026" a date belongs to.
        assert playlist.parse_date(f"10/14 - Sheet 3 - {SEASON}") == "2025-10-14"

    def test_september_belongs_to_the_first_year(self):
        assert playlist.parse_date(f"9/30 - Sheet 1 - {SEASON}") == "2025-09-30"

    def test_a_single_year_league_needs_no_split(self):
        title = "4/30 - Sheet 2 - Spring Skip's Choice League 2026"
        assert playlist.parse_date(title) == "2026-04-30"

    def test_returns_none_when_there_is_no_date(self):
        assert playlist.parse_date("Sheet 2 - some other stream 2026") is None

    def test_returns_none_when_there_is_no_year(self):
        assert playlist.parse_date("3/24 - Sheet 2 - a league") is None


class TestEntriesFromFlat:
    def test_builds_an_entry_per_video(self):
        entries, problems = playlist.entries_from_flat(
            [flat("mBBGkVPcPBQ", f"4/7 - Sheet 5 - {SEASON}", 15591)])
        assert problems == []
        (e,) = entries
        assert (e.video_id, e.sheet, e.date, e.duration_s) == (
            "mBBGkVPcPBQ", 5, "2026-04-07", 15591)

    def test_reports_an_unparseable_title_instead_of_dropping_it_silently(self):
        entries, problems = playlist.entries_from_flat(
            [flat("mBBGkVPcPBQ", "a stream with no sheet or date")])
        assert entries == []
        assert "mBBGkVPcPBQ" in problems[0]

    def test_sorts_by_date_then_sheet_so_the_order_is_not_youtubes(self):
        # Playlist order is newest-first and could change under us; the split
        # assignment must not depend on it.
        entries, _ = playlist.entries_from_flat([
            flat("aaaaaaaaaaa", f"4/7 - Sheet 5 - {SEASON}"),
            flat("bbbbbbbbbbb", f"9/30 - Sheet 2 - {SEASON}"),
            flat("ccccccccccc", f"9/30 - Sheet 1 - {SEASON}"),
        ])
        assert [e.video_id for e in entries] == [
            "ccccccccccc", "bbbbbbbbbbb", "aaaaaaaaaaa"]


def season(dates=("2025-09-30", "2025-10-07", "2025-10-14", "2025-10-21"),
           sheets=(1, 2, 3, 4, 5)):
    out = []
    for i, d in enumerate(dates):
        for s in sheets:
            out.append(playlist.Entry(f"v{i}{s}".ljust(11, "x"),
                                      f"title {d} sheet {s}", 15596.0, d, s))
    return out


class TestCheck:
    def test_says_nothing_when_every_date_has_every_sheet(self):
        assert playlist.check(season()) == []

    def test_names_the_date_and_the_missing_sheet(self):
        entries = [e for e in season() if not (e.date == "2025-10-07" and e.sheet == 3)]
        (problem,) = playlist.check(entries)
        assert "2025-10-07" in problem and "3" in problem

    def test_reports_a_duplicated_sheet_on_one_date(self):
        entries = season()
        entries.append(playlist.Entry("dupxxxxxxxx", "t", 1.0, "2025-09-30", 1))
        assert any("2025-09-30" in p for p in playlist.check(entries))


class TestPickValDates:
    def test_spreads_the_hold_out_across_the_season(self):
        dates = [f"2025-{m:02d}-{d:02d}" for m, d in
                 [(9, 30)] + [(10, x) for x in (7, 14, 21, 28)] +
                 [(11, x) for x in (4, 11, 18, 25)] + [(12, x) for x in (2, 9, 16)]]
        assert len(dates) == 12
        picked = playlist.pick_val_dates(dates, n=3)
        assert picked == ["2025-10-14", "2025-11-11", "2025-12-09"]

    def test_is_deterministic(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 27)]
        assert playlist.pick_val_dates(dates, n=5) == playlist.pick_val_dates(dates, n=5)

    def test_never_picks_the_first_or_last_date(self):
        # The season's ends are its most unusual ice; holding one out entirely
        # would make val harder than train for a reason unrelated to the model.
        dates = [f"2026-01-{d:02d}" for d in range(1, 27)]
        picked = playlist.pick_val_dates(dates, n=5)
        assert dates[0] not in picked and dates[-1] not in picked

    def test_refuses_to_hold_out_more_dates_than_exist(self):
        with pytest.raises(ValueError):
            playlist.pick_val_dates(["2026-01-01", "2026-01-02"], n=5)

    def test_skips_excluded_dates_entirely(self):
        # Two October Tuesdays were streamed at 720p where the rest of the
        # season is 1080p. Holding one out would make val harder for a reason
        # that has nothing to do with the model.
        dates = [f"2026-01-{d:02d}" for d in range(1, 27)]
        bad = {"2026-01-03", "2026-01-09"}
        picked = playlist.pick_val_dates(dates, n=5, exclude=bad)
        assert not (set(picked) & bad)
        assert len(picked) == 5

    def test_still_spreads_after_excluding(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 27)]
        picked = playlist.pick_val_dates(dates, n=5, exclude={"2026-01-03", "2026-01-09"})
        gaps = [dates.index(b) - dates.index(a) for a, b in zip(picked, picked[1:])]
        assert max(gaps) - min(gaps) <= 2

    def test_counts_only_eligible_dates_when_refusing(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 13)]
        with pytest.raises(ValueError):
            playlist.pick_val_dates(dates, n=5, exclude=set(dates[:6]))


class TestAssignSplits:
    def test_every_video_on_a_held_out_date_goes_to_val(self):
        entries = season()
        splits = playlist.assign_splits(entries, ["2025-10-07"])
        assert {e.video_id: splits[e.video_id] for e in entries if e.date == "2025-10-07"} \
            == {e.video_id: "val" for e in entries if e.date == "2025-10-07"}
        assert all(splits[e.video_id] == "train"
                   for e in entries if e.date != "2025-10-07")

    def test_rejects_a_hold_out_date_that_is_not_in_the_season(self):
        with pytest.raises(ValueError):
            playlist.assign_splits(season(), ["2026-07-04"])
