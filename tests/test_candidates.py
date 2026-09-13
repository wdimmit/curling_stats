import pytest

from curling_score.harvest import candidates as C


def cand(t, n_red=2, n_yellow=2, kind="grid", panel="top", clip=0.0, vid="v"):
    return C.Candidate(video_id=vid, panel=panel, t_abs=t, clip_start_s=clip,
                       kind=kind, n_red=n_red, n_yellow=n_yellow,
                       flight_id=None, lighting="lit", labels=())


class TestBinOf:
    def test_an_empty_house_is_its_own_bin(self):
        assert C.bin_of(cand(1.0, 0, 0)) == "empty"

    def test_counts_both_colours(self):
        assert C.bin_of(cand(1.0, 2, 2)) == "medium"

    def test_a_packed_house_is_busy(self):
        assert C.bin_of(cand(1.0, 4, 4)) == "busy"

    def test_the_edges_follow_the_measured_distribution(self):
        # 44% of panels hold nothing and almost none hold ten; edges chosen
        # before measuring left "busy" at 2.7% of frames and unfillable.
        assert C.bin_of(cand(1.0, 1, 0)) == "sparse"
        assert C.bin_of(cand(1.0, 2, 1)) == "sparse"
        assert C.bin_of(cand(1.0, 2, 2)) == "medium"
        assert C.bin_of(cand(1.0, 3, 4)) == "busy"

    def test_a_moving_stone_outranks_its_count(self):
        # A flight frame is wanted *because* it is a flight; which count bin it
        # would otherwise land in is beside the point.
        assert C.bin_of(cand(1.0, 3, 3, kind="motion")) == "motion"


def pool(n_per_bin=6):
    out = []
    t = 600.0
    for counts in ((0, 0), (1, 1), (2, 3), (4, 4)):
        for i in range(n_per_bin):
            out.append(cand(t, *counts, panel="top" if i % 2 else "bottom",
                            clip=round(t / 1000) * 1000.0))
            t += 700.0
    for i in range(6):
        out.append(cand(t, 2, 2, kind="motion", clip=round(t / 1000) * 1000.0))
        t += 700.0
    return out


class TestSelect:
    def test_fills_each_bin_to_its_quota(self):
        picked, short = C.select(pool(), quota={"empty": 2, "sparse": 3,
                                                "medium": 4, "busy": 3, "motion": 3})
        got = {}
        for c in picked:
            got[C.bin_of(c)] = got.get(C.bin_of(c), 0) + 1
        assert got == {"empty": 2, "sparse": 3, "medium": 4, "busy": 3, "motion": 3}
        assert short == {}

    def test_only_a_real_flight_can_fill_the_motion_quota(self):
        # Backfilling motion with a still frame would quietly turn the flight
        # coverage into a number nobody can trust.
        no_motion = [c for c in pool() if c.kind != "motion"]
        picked, short = C.select(no_motion, quota={"medium": 2, "motion": 3})
        assert all(c.kind != "motion" for c in picked)
        assert short["motion"] == 3

    def test_reports_a_shortfall_rather_than_hiding_it(self):
        thin = [c for c in pool() if C.bin_of(c) != "busy"][:8]
        _picked, short = C.select(thin, quota={"busy": 3, "medium": 2})
        assert short.get("busy") == 3

    def test_backfills_count_bins_so_the_frame_budget_is_met(self):
        thin = [c for c in pool() if C.bin_of(c) in ("medium", "empty")]
        picked, short = C.select(thin, quota={"empty": 2, "sparse": 3, "medium": 2},
                                 backfill=True)
        assert len(picked) == 7
        # Backfilling keeps the budget whole; it does not mean the video had
        # sparse frames to give, and the record has to keep saying so.
        assert short["sparse"] == 3
        assert short["backfilled"] == 3

    def test_is_deterministic(self):
        a, _ = C.select(pool(), quota={"empty": 2, "medium": 3, "motion": 2})
        b, _ = C.select(list(reversed(pool())), quota={"empty": 2, "medium": 3,
                                                       "motion": 2})
        assert [c.t_abs for c in a] == [c.t_abs for c in b]

    def test_never_picks_two_frames_from_the_same_moment(self):
        # Two frames a minute apart are nearly the same frame. ds10's whole
        # weakness is 6,995 frames that are really a few hundred.
        picked, _ = C.select(pool(), quota={"medium": 4})
        times = sorted(c.t_abs for c in picked)
        assert min(b - a for a, b in zip(times, times[1:])) >= 700.0

    def test_does_not_cluster_where_the_pool_happens_to_be_dense(self):
        # A real night gives bursts: several flights in one end and nothing for
        # ten minutes. Picking by availability would take the burst.
        dense = [cand(600.0 + i * 5.0, 3, 2, clip=600.0 + i * 5.0) for i in range(20)]
        sparse = [cand(t, 3, 2, clip=t) for t in (4000.0, 8000.0, 12000.0)]
        picked, _ = C.select(dense + sparse, quota={"medium": 4})
        assert sum(1 for c in picked if c.t_abs > 3000.0) >= 3

    def test_takes_the_ends_of_the_range_it_is_given(self):
        picked, _ = C.select(pool(), quota={"medium": 4})
        times = sorted(c.t_abs for c in picked)
        pool_times = sorted(c.t_abs for c in pool() if C.bin_of(c) == "medium")
        assert times[0] == pool_times[0] and times[-1] == pool_times[-1]

    def test_caps_how_many_come_from_one_clip(self):
        same_clip = [cand(600.0 + i, 2, 2, clip=600.0) for i in range(10)]
        picked, _ = C.select(same_clip, quota={"medium": 6}, max_per_clip=2)
        assert len(picked) == 2

    def test_returns_them_in_time_order(self):
        picked, _ = C.select(pool(), quota={"empty": 2, "medium": 3, "motion": 2})
        assert [c.t_abs for c in picked] == sorted(c.t_abs for c in picked)


class TestStem:
    def test_names_a_frame_by_its_video_panel_and_moment(self):
        assert C.stem_for("mBBGkVPcPBQ", "top", 3204.4) == "mBBGkVPcPBQ_t_003204_40"

    def test_the_existing_label_tools_can_parse_it_back(self):
        from curling_score.train.labels import parse_name

        seq, t = parse_name(C.stem_for("mBBGkVPcPBQ", "bottom", 3204.4))
        assert seq == "mBBGkVPcPBQ_b" and t == pytest.approx(3204.4)

    def test_two_panels_at_one_moment_do_not_collide(self):
        assert C.stem_for("v", "top", 10.0) != C.stem_for("v", "bottom", 10.0)


class TestWavesNest:
    """The pilot is reviewed first and the rest later. If the small quota
    picked different frames from the large one, the pilot's review would be
    thrown away."""

    def test_a_smaller_quota_is_a_subset_of_a_larger_one(self):
        p = pool()
        small, _ = C.select(p, quota={"empty": 1, "medium": 1, "motion": 1})
        large, _ = C.select(p, quota={"empty": 2, "medium": 4, "motion": 3})
        assert {c.stem for c in small} <= {c.stem for c in large}

    def test_holds_for_every_bin_independently(self):
        p = pool()
        for name in ("empty", "sparse", "medium", "busy", "motion"):
            one, _ = C.select(p, quota={name: 1})
            three, _ = C.select(p, quota={name: 3})
            assert {c.stem for c in one} <= {c.stem for c in three}, name

    def test_growing_by_one_keeps_everything_already_picked(self):
        p = [cand(600.0 + i * 100.0, 2, 2, clip=600.0 + i * 100.0) for i in range(12)]
        prev = set()
        for k in range(1, 7):
            got, _ = C.select(p, quota={"medium": k}, max_per_clip=1)
            stems = {c.stem for c in got}
            assert prev <= stems, f"k={k} dropped a frame k={k - 1} had picked"
            prev = stems


class TestQuotaFor:
    def test_the_pilot_takes_one_of_each(self):
        assert set(C.quota_for(wave=1).values()) == {1}

    def test_val_takes_the_same_shape_as_train(self):
        # A benchmark composed differently from the training set measures a
        # different problem.
        assert C.quota_for("val") == C.quota_for("train")

    def test_the_weak_bins_get_the_room(self):
        # Measured on the 551-frame pilot: sparse loses 9% of its stones and
        # motion has three times the false-positive rate, while 114 empty
        # frames produced not one correction.
        q = C.quota_for("train")
        assert q["sparse"] >= 4 and q["motion"] >= 4
        assert q["empty"] <= 1
        assert q["busy"] <= q["sparse"]

    def test_the_pilot_nests_inside_the_full_quota(self):
        assert all(C.quota_for(wave=1)[k] <= C.quota_for("train", 2)[k]
                   for k in C.quota_for(wave=1))


class TestBackfillSource:
    def test_never_tops_up_from_empty_frames(self):
        # 114 empty frames in the pilot produced not one correction, and
        # empties are 44% of the pool -- so a naive backfill quietly refills
        # the very bin the quota just de-weighted.
        p = [cand(600.0 + i * 100.0, 0, 0, clip=600.0 + i * 100.0) for i in range(20)]
        p += [cand(9000.0 + i * 100.0, 2, 2, clip=9000.0 + i * 100.0) for i in range(3)]
        picked, short = C.select(p, quota={"empty": 1, "medium": 8}, backfill=True)
        assert sum(1 for c in picked if C.bin_of(c) == "empty") == 1
        assert short["medium"] == 5

    def test_still_tops_up_from_the_informative_bins(self):
        p = [cand(600.0 + i * 100.0, 4, 4, clip=600.0 + i * 100.0) for i in range(10)]
        picked, short = C.select(p, quota={"sparse": 3, "busy": 2}, backfill=True)
        assert len(picked) == 5
        assert short["sparse"] == 3 and short["backfilled"] == 3


class TestTheThrowBin:
    """A throw is wanted because it is a throw, like a flight."""

    def test_a_throw_outranks_the_count_bins(self):
        assert C.bin_of(cand(1.0, 3, 3, kind="throw")) == "throw"

    def test_a_throw_bin_exists_in_the_quota(self):
        assert "throw" in C.quota_for("train", wave=3)
        assert "throw" in C.quota_for("val", wave=3)

    def test_wave_three_asks_for_throws_and_nothing_else(self):
        # The point of the wave is the one thing every earlier set is missing.
        assert set(C.quota_for("train", wave=3)) == {"throw"}

    def test_only_a_real_throw_can_fill_the_throw_quota(self):
        # Same reasoning as motion: a throw quota met with still frames would
        # make the coverage a number nobody could trust.
        pool = [cand(float(i) * 100, 2, 2) for i in range(20)]
        picked, short = C.select(pool, quota={"throw": 3, "medium": 2},
                                 backfill=True)
        assert short.get("throw") == 3
        assert not any(c.kind == "throw" for c in picked)

    def test_throws_are_spread_across_the_night(self):
        pool = [cand(float(i) * 300, 2, 2, kind="throw", clip=float(i) * 300)
                for i in range(12)]
        picked, short = C.select(pool, quota={"throw": 3})
        assert len(picked) == 3
        assert not short
        ts = sorted(c.t_abs for c in picked)
        assert ts[0] == 0.0 and ts[-1] == 3300.0

    def test_a_short_supply_of_throws_is_reported_not_padded(self):
        pool = [cand(0.0, 2, 2, kind="throw")] + [cand(float(i) * 100, 2, 2)
                                                  for i in range(1, 10)]
        picked, short = C.select(pool, quota={"throw": 4})
        assert short["throw"] == 3
        assert len(picked) == 1
