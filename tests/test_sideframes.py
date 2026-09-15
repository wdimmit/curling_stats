from curling_score.harvest import sideframes as S


def cand(vid="v1", view="left", t=0.0, clip=0.0, pos="crossing",
         key="ok", color="red"):
    return S.SideCandidate(video_id=vid, view=view, t_abs=t, clip_start_s=clip,
                           position=pos, outcome=key, color=color,
                           crowding=0, labels=())


class TestPositionBins:
    def test_a_stone_short_of_the_line_is_an_approach(self):
        assert S.position_of(edge_row=480.0, hog_row=520.0) == "approach"

    def test_a_stone_on_the_line_is_a_crossing(self):
        assert S.position_of(edge_row=512.0, hog_row=520.0) == "crossing"

    def test_a_stone_past_the_line_is_past(self):
        assert S.position_of(edge_row=560.0, hog_row=520.0) == "past"

    def test_no_stone_at_all_is_not_a_position(self):
        assert S.position_of(edge_row=None, hog_row=520.0) is None


class TestQuotas:
    def test_the_two_halves_come_to_six_hundred(self):
        scene = 2 * sum(S.SCENE_QUOTA.values())      # per view
        assert scene == 300
        assert sum(S.OUTCOME_QUOTA.values()) == 300

    def test_refusals_outweigh_successes_four_to_one(self):
        found = S.OUTCOME_QUOTA["ok"]
        refused = sum(v for k, v in S.OUTCOME_QUOTA.items() if k != "ok")
        assert refused >= 4 * found

    def test_the_scene_bins_are_the_positions(self):
        """Pinned here as well as by the assert in the module, because an
        assert vanishes under `python -O` and this invariant should not."""
        assert set(S.SCENE_QUOTA) == set(S.POSITIONS)

    def test_every_outcome_quota_names_a_real_refusal(self):
        from curling_score.detect import longview
        assert set(S.OUTCOME_QUOTA) <= set(longview.KEYS)


class TestSelect:
    def test_no_video_may_dominate(self):
        pool = [cand(vid="v1", t=float(i), clip=float(i)) for i in range(400)]
        chosen, _short = S.select(pool)
        assert len(chosen) <= S.MAX_PER_VIDEO

    def test_one_clip_cannot_fill_a_bin_with_near_duplicates(self):
        pool = [cand(vid=f"v{i}", t=float(i), clip=0.0) for i in range(5)] + \
               [cand(vid="v9", t=100.0 + j, clip=100.0) for j in range(50)]
        chosen, _short = S.select(pool)
        from collections import Counter
        per_clip = Counter((c.video_id, c.clip_start_s) for c in chosen)
        assert max(per_clip.values()) <= S.MAX_PER_CLIP

    def test_no_candidate_is_scarce_and_that_is_reported_not_hidden(self):
        """Measured supply is lopsided: over 13 ends the classical detector
        refused 106 windows as 55 ambiguous, 24 unsteady, 14 never_reached,
        11 bad_speed and only 2 no_candidate. The quota asks for 50 of the
        rarest anyway, deliberately -- it sweeps up whatever exists -- so this
        pins that the gap comes back as a number instead of being padded."""
        pool = [cand(vid=f"v{i}", t=float(i), clip=float(i), key="ambiguous")
                for i in range(400)]
        _chosen, shortfall = S.select(pool)
        assert shortfall.get("outcome:no_candidate") == \
            S.OUTCOME_QUOTA["no_candidate"]

    def test_a_bin_nobody_can_fill_is_reported_not_padded(self):
        pool = [cand(vid=f"v{i}", t=float(i), clip=float(i), pos="crossing")
                for i in range(200)]
        chosen, shortfall = S.select(pool)
        assert shortfall.get("scene:left:approach")
        assert all(c.position == "crossing" for c in chosen if c.half == "scene")

    def test_one_frame_cannot_take_two_of_the_six_hundred_slots(self):
        """`sidepool` emits a candidate per colour scan per moment, so the same
        image is in the pool twice -- differing in colour, position, edge_row
        and labels, so neither identity nor equality dedupes them. Keyed on the
        object rather than the frame, every selected frame was chosen twice:
        60 distinct stems came back as 120 rows, each landing in two bins at
        once and reaching a person twice with two different proposed boxes."""
        from collections import Counter
        pool = []
        for i in range(60):
            for color, pos in (("red", "approach"), ("yellow", "crossing")):
                pool.append(cand(vid=f"v{i % 30}", t=float(i), clip=float(i),
                                 pos=pos, color=color))
        chosen, _short = S.select(pool)
        stems = Counter(c.stem for c in chosen)
        assert not [k for k, n in stems.items() if n > 1]
        assert len(chosen) == len(stems)

    def test_the_scene_half_takes_refused_frames_too(self):
        """The whole point: selection must not consult the detector's verdict."""
        pool = [cand(vid=f"v{i}", t=float(i), clip=float(i), key="no_candidate")
                for i in range(200)]
        chosen, _short = S.select(pool)
        assert any(c.half == "scene" for c in chosen)
