"""The nightly review through the API: which games it reads, the flags it
raises, and that running it again -- or after a restore -- never doubles one."""

from datetime import timedelta

import pytest

from curling_score import autoreview
from curling_score.service.records import Review
from tests.test_service_api import ADMIN, T0, submit, work_through, world  # noqa: F401


def rock(n, t, broom=True):
    return {"number": n, "color": "red" if n % 2 else "yellow", "position": "lead",
            "rock_of_player": 1, "label": f"rock {n}", "t_rest_s": t + 20.0,
            "t_enter_s": t + 12.0, "t_video_s": t, "state_known": True, "missing": False,
            "shot_type": "draw", "stones": [], "track": [],
            "target_broom": {"x": 0.2, "y": 0.0, "confidence": 0.9} if broom else None,
            "long_split_s": 14.0, "line": {"at_broom": {"miss_m": 0.2}},
            "t_release_s": t - 4.0, "color_inferred": False}


def end(number, house, start, brooms=16):
    return {"number": number, "house": house, "start_s": start, "end_s": start + 600.0,
            "score": {"red": 1, "yellow": 0}, "detected_score": {"red": 1, "yellow": 0},
            "running": {"red": number, "yellow": 0}, "shots_expected": 16,
            "unplaced_shots": 0,
            "shots": [rock(n, start + 30.0 * n, broom=n > 16 - brooms) for n in range(1, 17)]}


def doc_of(*games):
    """A timeline whose games each have the ends ``houses`` names, 16 clean rocks
    an end; ``brooms`` maps an end number to how many of its rocks keep one."""
    out = []
    for i, (houses, brooms) in enumerate(games):
        start = 100.0 + i * 7400.0
        ends = [end(n, h, start + 700.0 * (n - 1), brooms.get(n, 16))
                for n, h in enumerate(houses, 1)]
        out.append({"index": i, "start_s": start, "end_s": start + 700.0 * len(houses),
                    "teams": {"red": {"name": None}, "yellow": {"name": None}},
                    "final": {"red": 0, "yellow": 0}, "hammer_consistent": True,
                    "ends": ends})
    return {"schema_version": 8, "processing_version": "2026.10.05+m-abc",
            "source": {"url": "u", "video_id": "VXU9xwmugRg", "sheet": 2,
                       "duration_s": 14392.0, "window": {"start_s": None, "end_s": None}},
            "calibration": {}, "games": out}


CLEAN = (["top", "bottom", "top", "bottom"], {})
SAME_HOUSE = (["top", "bottom", "bottom", "top"], {})
LOW_BROOMS = (["top", "bottom", "top", "bottom"], {2: 6})


def charted(w, *games):
    submit(w)
    work_through(w, doc=doc_of(*games), games=len(games))
    return sorted(w["repo"].list_sources(), key=lambda s: s.game_index)


def review(w, **params):
    r = w["client"].post("/api/admin/review", params=params, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def flags(w):
    return w["repo"].list_flags()


class TestAccess:
    def test_it_needs_the_admin_token(self, world):
        assert world["client"].post("/api/admin/review").status_code == 401


class TestReviewing:
    def test_a_clean_game_is_read_and_not_flagged(self, world):
        (src,) = charted(world, CLEAN)
        got = review(world)
        assert (got["reviewed"], got["flagged"], got["pending"]) == (1, 0, 0)
        rec = world["repo"].get_review(f"{src.id}_{src.current_run_id}")
        assert rec.format == "fours" and [e["broom"] for e in rec.ends] == [16] * 4
        assert rec.flag_id is None and rec.error is None and flags(world) == []

    def test_a_game_that_looks_wrong_gets_one_auto_flag(self, world):
        (src,) = charted(world, SAME_HOUSE)
        got = review(world)
        assert got["flagged"] == 1
        (f,) = flags(world)
        run = world["repo"].get_run(src.current_run_id)
        assert f.id == autoreview.flag_id(src.id, run.id, run.ready_at)
        assert f.origin == "auto" and f.status == "open"
        assert f.where == {"link": "g", "chart_id": None, "share_slug": None,
                           "source_id": src.id, "run_id": run.id, "video_id": run.video_id,
                           "processing_version": run.processing_version, "title": run.title}
        assert (f.place["end"], f.place["rock"], f.place["game_index"]) == (3, None, 0)
        assert f.note.startswith("Auto-review: 1 finding — e3 e2 and e3 both to the bottom house")
        assert [x["check"] for x in f.findings] == ["same_house"]
        listed = world["client"].get("/api/admin/flags", headers=ADMIN).json()["flags"]
        assert listed[0]["origin"] == "auto" and listed[0]["findings"][0]["end"] == 3

    def test_a_second_call_reads_nothing_new(self, world):
        charted(world, SAME_HOUSE)
        review(world)
        got = review(world)
        assert (got["reviewed"], got["flagged"], got["pending"]) == (0, 0, 0)
        assert len(flags(world)) == 1

    def test_runs_that_finished_before_the_lookback_are_left(self, world):
        charted(world, SAME_HOUSE)
        world["clock"].advance(4 * 86400)
        assert review(world)["reviewed"] == 0

    def test_since_reaches_further_back(self, world):
        charted(world, SAME_HOUSE)
        world["clock"].advance(10 * 86400)
        assert review(world, since=(T0 - timedelta(days=1)).isoformat())["reviewed"] == 1

    def test_until_stops_short_of_recent_runs(self, world):
        charted(world, SAME_HOUSE)
        assert review(world, until=(T0 - timedelta(minutes=1)).isoformat())["reviewed"] == 0
        assert review(world, until=(T0 + timedelta(minutes=1)).isoformat())["reviewed"] == 1

    @pytest.mark.parametrize("param", ["since", "until"])
    def test_since_and_until_must_be_times(self, world, param):
        r = world["client"].post("/api/admin/review", params={param: "last tuesday"},
                                 headers=ADMIN)
        assert r.status_code == 422


class TestModes:
    def test_a_dry_run_writes_nothing_and_says_what_it_would_flag(self, world):
        (src,) = charted(world, SAME_HOUSE)
        got = review(world, dry_run=True)
        assert got["reviewed"] == 1 and got["flagged"] == 1
        (would,) = got["would_flag"]
        assert would["source_id"] == src.id and would["flag"]["origin"] == "auto"
        assert flags(world) == [] and world["repo"].reviews_for_source(src.id) == []

    def test_without_flags_it_only_records_what_it_read(self, world):
        (src,) = charted(world, SAME_HOUSE)
        got = review(world, flag=False)
        assert got["reviewed"] == 1 and got["flagged"] == 0 and flags(world) == []
        (rec,) = world["repo"].reviews_for_source(src.id)
        assert rec.flag_id is None and rec.findings[0]["check"] == "same_house"

    def test_one_game_on_request_even_if_read_already(self, world):
        (src,) = charted(world, SAME_HOUSE)
        review(world)
        got = review(world, source_id=src.id)
        assert got["reviewed"] == 1 and got["flagged"] == 0    # same flag: not written twice
        assert len(flags(world)) == 1

    def test_an_unknown_game_on_request(self, world):
        r = world["client"].post("/api/admin/review", params={"source_id": "s_nope"},
                                 headers=ADMIN)
        assert r.status_code == 404


class TestBatches:
    def test_limit_leaves_the_rest_for_the_next_call(self, world):
        charted(world, CLEAN, CLEAN, CLEAN)
        assert (review(world, limit=2)["reviewed"], review(world)["reviewed"]) == (2, 1)

    def test_the_time_budget_stops_starting_games_but_always_does_one(self, world, monkeypatch):
        charted(world, CLEAN, CLEAN, CLEAN)
        monkeypatch.setattr(autoreview, "TIME_BUDGET_S", -1.0)
        got = review(world)
        assert (got["reviewed"], got["pending"]) == (1, 2)


class TestTheBaseline:
    def prior(self, w, at, brooms, n=110):
        for i in range(n):
            w["repo"].put_review(Review(
                id=f"s_p{i}_r_p", source_id=f"s_p{i}", run_id="r_p", reviewed_at=at,
                format="fours", run_ready_at=at,
                ends=[{"number": 1, "rocks": 16, "broom": brooms, "split": 16, "line": 16,
                       "release": 16}]))

    def test_with_no_history_the_floor_flags_a_thin_end(self, world):
        charted(world, LOW_BROOMS)                     # end 2: 6 of 16 brooms
        assert review(world)["flagged"] == 1

    def test_normal_comes_from_the_fortnight_before(self, world):
        self.prior(world, T0 - timedelta(days=1), brooms=4)    # 25% is normal lately
        charted(world, LOW_BROOMS)
        assert review(world)["flagged"] == 0

    def test_reviews_from_the_batch_or_after_are_not_its_normal(self, world):
        self.prior(world, T0 + timedelta(hours=1), brooms=4)
        charted(world, LOW_BROOMS)
        assert review(world)["flagged"] == 1


class TestNeverTwice:
    def test_a_resolved_auto_flag_is_never_reopened(self, world):
        (src,) = charted(world, SAME_HOUSE)
        review(world)
        (f,) = flags(world)
        world["client"].post(f"/api/admin/flags/{f.id}/resolve", headers=ADMIN)
        world["repo"].reviews.clear()                  # a restore that lost the reviews
        got = review(world)
        assert got["reviewed"] == 1 and got["flagged"] == 0
        (again,) = flags(world)
        assert again.status == "resolved"

    def test_a_run_retried_in_place_is_read_again_and_names_the_earlier_flag(self, world):
        (src,) = charted(world, SAME_HOUSE)
        review(world)
        (first,) = flags(world)
        world["repo"].update_run(src.current_run_id, ready_at=T0 + timedelta(hours=1))
        got = review(world)
        assert got["reviewed"] == 1 and got["flagged"] == 1
        second = next(f for f in flags(world) if f.id != first.id)
        assert f"earlier auto-flag {first.id} (run {src.current_run_id})" in second.note
        assert first.status == "open"                  # never resolved for you


class TestWhatIsNotAGame:
    def test_a_page_merged_into_another_game_is_not_read(self, world):
        a, b = charted(world, CLEAN, SAME_HOUSE)
        world["repo"].update_source(b.id, merged_into=a.id)
        got = review(world)
        assert got["reviewed"] == 1 and flags(world) == []

    def test_a_practice_session_is_not_read(self, world):
        """Practice is not a game: no flags for it, and none of its ends in
        the baseline real games are measured against."""
        a, b = charted(world, CLEAN, SAME_HOUSE)
        world["repo"].update_source(b.id, practice=True)
        got = review(world)
        assert got["reviewed"] == 1 and flags(world) == []

    def test_a_live_run_waits_until_it_is_ready(self, world):
        (src,) = charted(world, SAME_HOUSE)
        world["repo"].update_run(src.current_run_id, status="live")
        assert review(world)["reviewed"] == 0
        world["repo"].update_run(src.current_run_id, status="ready")
        assert review(world)["reviewed"] == 1

    def test_a_game_missing_from_its_run_is_an_error_and_the_rest_go_on(self, world):
        a, b = charted(world, CLEAN, SAME_HOUSE)
        world["repo"].update_source(a.id, game_index=99)
        got = review(world)
        assert got["reviewed"] == 2 and got["flagged"] == 1
        assert got["errors"] == [{"source_id": a.id,
                                  "error": f"LookupError: game 99 is not in run {a.current_run_id}"}]
        rec = world["repo"].get_review(f"{a.id}_{a.current_run_id}")
        assert rec.error.startswith("LookupError") and rec.flag_id is None
        assert review(world)["reviewed"] == 0          # not tried again

    def test_a_game_that_breaks_the_review_is_recorded_and_the_rest_go_on(self, world,
                                                                          monkeypatch):
        a, b = charted(world, CLEAN, SAME_HOUSE)
        real = autoreview.review_game

        def fussy(game, baseline):
            if game["index"] == 0:
                raise ValueError("no idea")
            return real(game, baseline)

        monkeypatch.setattr(autoreview, "review_game", fussy)
        got = review(world)
        assert got["errors"] == [{"source_id": a.id, "error": "ValueError: no idea"}]
        assert got["flagged"] == 1

    def test_a_timeline_from_before_the_newer_fields(self, world):
        from tests.test_service_api import sample_doc
        submit(world)
        work_through(world, doc=sample_doc(1), games=1)
        got = review(world)
        assert got["reviewed"] == 1 and got["errors"] == []
        assert got["flagged"] == 1                     # one end: a short game
