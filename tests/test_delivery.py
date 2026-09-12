import pytest

from curling_score.detect import delivery
from curling_score.detect.rocks import Detection


def det(color, x, y, conf=0.8):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=conf)


def thrown(color, y0=3.8, y1=-0.9, t0=0.0, speed=0.5, fps=5.0, x=0.2):
    """A stone travelling down-sheet from y0 to y1, then resting."""
    out, t, y = [], t0, y0
    step = 1.0 / fps
    while y > y1:
        out.append((t, det(color, x, y)))
        y -= speed * step
        t += step
    # A stone that has come to rest stays there and keeps being seen; the
    # detector only stops reporting it if something removes it.
    for _ in range(int(fps * 30)):
        out.append((t, det(color, x, y1)))
        t += step
    return out


def merge(*traces, fps=5.0, static=()):
    """Combine per-stone traces into (t, [detections]) frames."""
    frames = {}
    for tr in traces:
        for t, d in tr:
            frames.setdefault(round(t, 3), []).append(d)
    for t in frames:
        frames[t].extend(static)
    return [(t, frames[t]) for t in sorted(frames)]


class TestFindDeliveries:
    def test_a_stone_travelling_down_sheet_is_one_delivery(self):
        got = delivery.find_deliveries(merge(thrown("red")))
        assert len(got) == 1
        assert got[0].color == "red"

    def test_reports_where_the_stone_came_to_rest(self):
        got = delivery.find_deliveries(merge(thrown("red", y1=-0.9, x=0.2)))
        assert got[0].rest_y_m == pytest.approx(-0.9, abs=0.15)
        assert got[0].rest_x_m == pytest.approx(0.2, abs=0.1)

    def test_reports_when_the_stone_entered_and_settled(self):
        got = delivery.find_deliveries(merge(thrown("red", t0=10.0)))
        assert got[0].t_enter == pytest.approx(10.0, abs=0.5)
        assert got[0].t_rest > got[0].t_enter

    def test_a_stone_already_at_rest_is_not_a_delivery(self):
        frames = [(i / 5.0, [det("yellow", 0.5, 0.3)]) for i in range(100)]
        assert delivery.find_deliveries(frames) == []

    def test_finds_both_colours_in_order(self):
        got = delivery.find_deliveries(
            merge(thrown("yellow", t0=0.0, x=-0.4), thrown("red", t0=20.0, x=0.6))
        )
        assert [d.color for d in got] == ["yellow", "red"]

    def test_ignores_a_blob_that_loiters_near_the_hog_line(self):
        # A broom head or a yellow-shirted sweeper: it wanders but never
        # traverses. Observed on the reference VOD around y = 4.1-4.5 m.
        wander = []
        for i in range(120):
            y = 4.3 + 0.2 * ((i // 5) % 3 - 1)
            wander.append((i / 5.0, det("yellow", 0.45, y)))
        assert delivery.find_deliveries(merge(wander)) == []

    def test_a_static_stone_does_not_hide_a_delivery_beside_it(self):
        got = delivery.find_deliveries(
            merge(thrown("red", x=0.2), static=(det("yellow", 0.55, 2.83),))
        )
        assert len(got) == 1
        assert got[0].color == "red"

    def test_no_frames_yields_nothing(self):
        assert delivery.find_deliveries([]) == []


class TestTravelThreshold:
    def test_a_short_nudge_is_not_a_delivery(self):
        # A stone bumped half a metre by a broom is not a thrown rock.
        got = delivery.find_deliveries(merge(thrown("red", y0=1.0, y1=0.5)))
        assert got == []

    def test_the_full_traverse_counts(self):
        got = delivery.find_deliveries(merge(thrown("red", y0=3.8, y1=-0.9)))
        assert got[0].travel_m > 4.0


def drop(trace, windows):
    """Remove frames whose time falls in any (start, end) window."""
    return [(t, d) for t, d in trace
            if not any(a <= t <= b for a, b in windows)]


class TestOcclusionDuringTravel:
    """Sweepers walk over the stone all the way down the sheet.

    Measured on the reference VOD: a delivery at t=2752 was hidden for 0.8 s and
    then 1.2 s while sweeping. Closing a track after one second split it into
    two short pieces, neither of which travelled far enough to be recognised --
    which is how half of end 4's deliveries went missing.
    """

    def test_a_delivery_survives_a_one_second_gap(self):
        tr = thrown("yellow", y0=4.2, y1=1.0, t0=0.0, speed=0.55)
        got = delivery.find_deliveries(merge(drop(tr, [(2.0, 3.0)])))
        assert len(got) == 1
        assert got[0].travel_m > 2.5

    def test_a_delivery_survives_several_gaps(self):
        tr = thrown("yellow", y0=4.2, y1=1.0, t0=0.0, speed=0.55)
        got = delivery.find_deliveries(
            merge(drop(tr, [(1.4, 2.2), (3.0, 4.2), (4.8, 5.2)]))
        )
        assert len(got) == 1, [(d.t_enter, d.travel_m) for d in got]

    def test_gaps_do_not_manufacture_extra_deliveries(self):
        tr = thrown("red", y0=3.8, y1=-0.9, t0=0.0)
        got = delivery.find_deliveries(merge(drop(tr, [(2.0, 3.2), (5.0, 6.0)])))
        assert len(got) == 1

    def test_a_static_neighbour_is_not_stolen_by_the_moving_track(self):
        # A guard sits where the thrown stone passes close by.
        tr = thrown("yellow", y0=4.2, y1=1.0, speed=0.55, x=0.15)
        got = delivery.find_deliveries(
            merge(drop(tr, [(2.0, 3.0)]), static=(det("yellow", 0.55, 2.83),))
        )
        assert len(got) == 1
        assert got[0].rest_y_m == pytest.approx(1.0, abs=0.2)


class TestFastTakeouts:
    """A takeout crosses the panel far faster than a draw.

    Measured on the reference VOD: a red takeout ran 3.57 -> 2.33 m in 0.8 s,
    about 1.55 m/s, which is 0.3 m between frames at 5 fps. A track that starts
    with no velocity estimate cannot open its gate wide enough to pick up the
    second sighting, so fast stones fragmented and were never recognised --
    which is why reds, who threw most of the takeouts, went missing.
    """

    def test_a_takeout_at_one_and_a_half_metres_per_second(self):
        got = delivery.find_deliveries(merge(thrown("red", y0=4.2, y1=-0.5, speed=1.55)))
        assert len(got) == 1
        assert got[0].travel_m > 4.0

    def test_a_very_fast_peel(self):
        got = delivery.find_deliveries(merge(thrown("red", y0=4.2, y1=-0.5, speed=2.6)))
        assert len(got) == 1

    def test_a_fast_stone_is_not_split_into_two_deliveries(self):
        got = delivery.find_deliveries(merge(thrown("yellow", y0=4.4, y1=-0.8, speed=2.0)))
        assert len(got) == 1

    def test_the_wide_bootstrap_gate_does_not_merge_two_stones(self):
        # Two stones of the same colour resting a metre apart must stay distinct.
        frames = []
        for i in range(80):
            frames.append((i / 5.0, [det("red", 0.0, 0.0), det("red", 0.0, 1.0)]))
        assert delivery.find_deliveries(frames) == []


class TestStonesThatLeavePlay:
    """A takeout often sends the shooter out the back; it never comes to rest.

    Measured on the reference VOD at t=3299: a red ran +3.68 -> -2.01 m and left
    the playing area. Requiring a resting place discarded it -- and takeouts are
    exactly the shots that leave, which skewed the detected colours.
    """

    def test_a_stone_through_the_back_still_counts_as_a_delivery(self):
        tr = thrown("red", y0=3.7, y1=-2.1, speed=1.6)
        # No settling: it is gone once past the back line.
        tr = [(t, d) for t, d in tr if d.y_m > -2.1]
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1
        assert got[0].came_to_rest is False

    def test_a_stone_off_the_side_counts(self):
        from curling_score.geometry import constants as C

        tr = []
        t, y, x = 0.0, 3.6, 0.0
        while x < C.SIDELINE_ABS_X_M + 0.3:  # runs off the side of the sheet
            tr.append((t, det("yellow", x, y)))
            y -= 0.28; x += 0.22; t += 0.2
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1
        assert got[0].came_to_rest is False

    def test_a_resting_delivery_is_still_marked_as_resting(self):
        got = delivery.find_deliveries(merge(thrown("red")))
        assert got[0].came_to_rest is True

    def test_a_track_that_simply_vanishes_mid_sheet_is_not_a_delivery(self):
        # Lost to occlusion inside the playing area: we cannot claim it was a shot.
        tr = thrown("yellow", y0=4.0, y1=1.5, speed=0.5)
        tr = [(t, d) for t, d in tr if d.y_m > 1.5]
        assert delivery.find_deliveries(merge(tr)) == []

    def test_a_stone_knocked_out_by_a_takeout_is_not_a_delivery(self):
        # It was sitting still, then got hit: it never entered from up-sheet.
        tr = [(i / 5.0, det("yellow", 0.3, 0.6)) for i in range(30)]
        t, y = 6.0, 0.6
        while y > -2.1:
            tr.append((t, det("yellow", 0.3, y)))
            y -= 0.4; t += 0.2
        assert delivery.find_deliveries(merge(tr)) == []


class TestEntersFromUpSheet:
    """A thrown stone arrives from the delivery end; a struck one does not.

    Every genuine delivery in end 4 entered the panel at y >= 3.4 m. The one
    false positive was a stone knocked out of the house by a takeout: the
    tracker picked it up at the moment of impact, at y = 0.02 m, and its flight
    out the back looked just like a delivery.
    """

    def test_a_stone_starting_mid_house_is_not_a_delivery(self):
        tr = []
        t, y = 0.0, 0.02
        while y > -2.1:  # knocked out the back
            tr.append((t, det("red", 1.7, y)))
            y -= 0.35; t += 0.1
        assert delivery.find_deliveries(merge(tr)) == []

    def test_a_stone_entering_from_up_sheet_is_a_delivery(self):
        got = delivery.find_deliveries(merge(thrown("red", y0=3.5, y1=-0.5)))
        assert len(got) == 1
        assert got[0].entry_y_m >= delivery.MIN_ENTRY_Y_M


def resting(color, x, y, t0, until, fps=10.0):
    """A stone sitting still from t0 until `until`."""
    out, t = [], t0
    step = 1.0 / fps
    while t <= until:
        out.append((t, det(color, x, y)))
        t += step
    return out


class TestGuards:
    """A guard is thrown short and stops in front of the house.

    Its visible travel inside the panel is small -- one measured at 0.79 m,
    entering at 3.50 m and stopping at 2.71 m -- so a minimum-travel filter
    throws it away. Leads throw mostly guards, so that filter was quietly
    discarding the opening deliveries of every end.

    What separates a guard from a broom nudge is not distance but persistence:
    a delivered stone is still sitting there once the players have cleared off.
    """

    def _guard(self, color="red", y0=3.50, y1=2.71, t0=0.0):
        tr, t, y = [], t0, y0
        while y > y1:
            tr.append((t, det(color, 0.15, y)))
            y -= 0.033
            t += 0.1
        tr += resting(color, 0.15, y1, t, t + 20.0)
        return tr

    def test_a_guard_is_a_delivery(self):
        got = delivery.find_deliveries(merge(self._guard()))
        assert len(got) == 1
        assert got[0].travel_m < 1.0, "this is deliberately a short-travel shot"
        assert got[0].rest_y_m == pytest.approx(2.71, abs=0.1)

    def test_a_stone_that_vanishes_straight_after_stopping_is_not_a_delivery(self):
        # A broom head or a mis-detection: nothing is there once play moves on.
        tr, t, y = [], 0.0, 3.5
        while y > 2.7:
            tr.append((t, det("red", 0.15, y)))
            y -= 0.033
            t += 0.1
        tr += resting("red", 0.15, 2.7, t, t + 2.0)
        assert delivery.find_deliveries(merge(tr)) == []

    def test_a_stone_that_moves_on_again_is_not_counted_where_it_paused(self):
        # Swept along, pausing mid-sheet, then carrying on out of play.
        tr, t, y = [], 0.0, 3.5
        while y > 2.7:
            tr.append((t, det("yellow", 0.15, y)))
            y -= 0.033; t += 0.1
        tr += resting("yellow", 0.15, 2.7, t, t + 3.0)
        t += 3.1
        while y > -2.2:
            tr.append((t, det("yellow", 0.15, y)))
            y -= 0.2; t += 0.1
        got = delivery.find_deliveries(merge(tr))
        assert all(d.rest_y_m < 2.0 for d in got), [d.rest_y_m for d in got]

    def test_persistence_is_judged_against_the_frames_available(self):
        # The sequence ends soon after the stone stops; it was still there.
        got = delivery.find_deliveries(merge(self._guard(t0=0.0)[:120]))
        assert len(got) <= 1  # accepted or not, must not crash


class TestPersistenceIsRequiredRegardlessOfDistance:
    """Distance does not excuse a track from having to persist.

    A sweeper in a team-coloured jacket runs alongside the delivery, so their
    phantom track enters up-sheet, crosses several metres and decelerates --
    every hallmark of a throw. Observed at t=3153: a yellow-jacketed sweeper
    following a red delivery produced "yellow stone" detections tracking from
    +4.52 m down to +2.10 m. What the jacket does not do is stay there
    afterwards, so persistence is the test that separates them, and exempting
    long tracks from it let every such phantom through.

    The one thing that genuinely excuses persistence is leaving the playing
    area, which is what a takeout does once it has done its job.
    """

    def _short(self, color, y0, y1, t0=0.0, persist_until=None):
        tr, t, y = [], t0, y0
        while y > y1:
            tr.append((t, det(color, 0.15, y)))
            y -= 0.033
            t += 0.1
        tr += resting(color, 0.15, y1, t, persist_until or (t + 2.0))
        return tr

    def test_a_long_track_that_leaves_nothing_behind_is_not_a_delivery(self):
        tr = self._short("yellow", 3.9, 0.5)          # ~3.4 m, then gone
        tr += resting("red", -1.0, 1.0, 0.0, 40.0)
        assert delivery.find_deliveries(merge(tr)) == []

    def test_a_takeout_that_leaves_the_sheet_still_counts(self):
        tr = thrown("yellow", y0=3.9, y1=-2.6, speed=1.6)
        # Lost once it is clear of the back line, as happens in the panel.
        tr = [(t, d) for t, d in tr if d.y_m > -2.25]
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1
        assert got[0].came_to_rest is False

    def test_a_short_shot_that_persists_counts(self):
        tr = self._short("red", 3.5, 2.9, persist_until=40.0)
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1
        assert got[0].rest_y_m == pytest.approx(2.9, abs=0.1)

    def test_a_long_shot_that_persists_counts(self):
        tr = self._short("red", 3.9, 0.5, persist_until=40.0)
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1


class TestStruckStones:
    """A stone driven out of the house is not a delivery.

    Observed in end 4: two "deliveries" detected in the same instant, a yellow
    entering at 4.52 m and a red at 3.96 m that ran out the back. The red was a
    *guard* the yellow struck. Guards sit up-sheet, inside the zone a delivery
    enters from, so a struck one mimics a throw.

    The tell is that it was already being tracked before it moved. It only looks
    new because the track snaps at impact: the matching gate is scaled by the
    track's own speed, which is zero for a stone at rest, so a stone accelerating
    instantly to several metres a second lands outside it.
    """

    def test_a_struck_guard_is_not_counted_as_a_delivery(self):
        # A red guard sits at 3.9 m, then is driven out the back by a hard
        # takeout. At 6 m/s it covers 0.6 m between frames, well outside the
        # gate of a track whose own speed is zero, so the track snaps.
        tr = resting("red", 0.4, 3.9, 0.0, 30.0)
        t, y = 30.1, 3.9
        while y > -2.2:
            tr.append((t, det("red", 0.4, y)))
            y -= 0.60
            t += 0.1
        assert delivery.find_deliveries(merge(tr)) == []

    def test_the_delivery_that_struck_it_still_counts(self):
        struck = resting("red", 0.4, 3.9, 0.0, 30.0)
        t, y = 30.1, 3.9
        while y > -2.2:
            struck.append((t, det("red", 0.4, y)))
            y -= 0.60; t += 0.1
        shooter = thrown("yellow", y0=4.3, y1=1.2, t0=25.0, speed=0.9, x=0.42)
        got = delivery.find_deliveries(merge(struck, shooter))
        assert [d.color for d in got] == ["yellow"]

    def test_a_stone_nudged_slowly_is_still_not_a_delivery(self):
        tr = resting("yellow", -0.3, 3.6, 0.0, 30.0)
        t, y = 30.1, 3.6
        while y > 1.0:
            tr.append((t, det("yellow", -0.3, y)))
            y -= 0.08; t += 0.1
        tr += resting("yellow", -0.3, 1.0, t, t + 25.0)
        assert delivery.find_deliveries(merge(tr)) == []

    def test_a_genuine_delivery_is_unaffected(self):
        got = delivery.find_deliveries(merge(thrown("red", y0=4.2, y1=-0.5)))
        assert len(got) == 1


class TestTrackLinkingPerformance:
    """Linking must stay linear in the number of fragments.

    Rescanning all tracks after each merge made this cubic on real footage: an
    analysis run sat at 25% CPU with the GPU idle for half an hour on a single
    end. Flickering detections are the normal case, not a corner case, so this
    shape has to stay cheap.
    """

    def _flickering(self, seconds=600, fps=10.0, drop=0.25, seed=0):
        import random

        rng = random.Random(seed)
        stones = [("red", 0.3, 1.1), ("yellow", -1.2, 1.6),
                  ("red", 0.5, 2.8), ("yellow", 0.1, -0.9)]
        out = []
        for i in range(int(seconds * fps)):
            frame = [
                det(c, x + rng.gauss(0, 0.01), y + rng.gauss(0, 0.01))
                for c, x, y in stones
                if rng.random() >= drop
            ]
            out.append((i / fps, frame))
        return out

    def test_a_long_flickering_end_is_processed_quickly(self):
        import time

        frames = self._flickering()
        start = time.time()
        delivery.find_deliveries(frames)
        assert time.time() - start < 5.0

    def test_flicker_collapses_to_one_track_per_stone(self):
        tracks = delivery._build_tracks(self._flickering(seconds=120))
        assert len(tracks) <= 8, f"{len(tracks)} tracks for 4 stones"


class TestTheLastStoneOfAnEnd:
    """The final delivery is cleared away almost immediately.

    Persistence was checked around ten seconds after the stone settled, on the
    reasoning that the players need that long to clear off. But at the *end* of
    an end they clear the rocks too: in end 4 the last stone rested at t=3546.5
    and the house was empty by t=3557. So the one delivery that decides the
    score was the one systematically rejected.
    """

    def test_a_stone_cleared_soon_after_settling_still_counts(self):
        tr, t, y = [], 0.0, 3.9
        while y > 0.4:
            tr.append((t, det("red", 0.2, y)))
            y -= 0.06
            t += 0.1
        # Sits for 11 s, then the players clear the house.
        tr += resting("red", 0.2, 0.4, t, t + 11.0)
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1
        assert got[0].rest_y_m == pytest.approx(0.4, abs=0.1)

    def test_a_broom_gone_within_two_seconds_is_still_rejected(self):
        tr, t, y = [], 0.0, 3.6
        while y > 2.9:
            tr.append((t, det("yellow", 0.2, y)))
            y -= 0.03
            t += 0.1
        tr += resting("yellow", 0.2, 2.9, t, t + 1.5)
        tr += resting("red", -1.0, 1.0, 0.0, 40.0)
        got = [d for d in delivery.find_deliveries(merge(tr)) if d.color == "yellow"]
        assert got == []


class TestSettlingThroughOcclusion:
    """A stone is most occluded exactly as it stops.

    Players converge on the house as the stone comes to rest, so the moment
    that decides where it ended is the moment most likely to be hidden. In end 1
    a yellow ran to y=-0.44, disappeared for three seconds, then sat at
    (+0.53,-0.94) at high confidence for the rest of the end. Because the gap
    exceeded the track timeout, the resting stone became a separate track and
    the delivery was thrown away as having "vanished in play" -- with its
    resting place plainly visible.

    Linking has to reach across that gap, but only forward: a stone carries on
    the way it was going and never comes back up the sheet.
    """

    def _delivery_then_gap(self, gap_s, color="yellow", stop=(0.53, -0.94)):
        tr, t, y = [], 0.0, 3.9
        while y > -0.44:
            tr.append((t, det(color, 0.40, y)))
            y -= 0.044
            t += 0.1
        t += gap_s                                    # occluded as it settles
        tr += resting(color, stop[0], stop[1], t, t + 40.0)
        return tr

    def test_a_three_second_occlusion_while_settling_is_bridged(self):
        got = delivery.find_deliveries(merge(self._delivery_then_gap(3.0)))
        assert len(got) == 1
        assert got[0].came_to_rest is True
        assert got[0].rest_y_m == pytest.approx(-0.94, abs=0.15)

    def test_the_resting_place_is_the_settled_one_not_the_last_seen(self):
        got = delivery.find_deliveries(merge(self._delivery_then_gap(2.9)))
        assert got[0].rest_y_m < -0.7, "should report where it stopped"

    def test_linking_does_not_reach_backwards_up_the_sheet(self):
        # A stone cannot reverse; a separate stone behind it must stay separate.
        tr = self._delivery_then_gap(3.0)
        tr += resting("yellow", 0.40, 2.60, 0.0, 60.0)
        got = delivery.find_deliveries(merge(tr))
        assert len(got) == 1
        assert got[0].rest_y_m < 0.0

    def test_an_implausibly_long_gap_is_not_bridged(self):
        assert delivery.find_deliveries(merge(self._delivery_then_gap(20.0))) == []


def interp(points, fps=5.0, color="red"):
    """Detections along a piecewise-linear path of (t, x, y) waypoints."""
    out = []
    step = 1.0 / fps
    for (t0, x0, y0), (t1, x1, y1) in zip(points, points[1:]):
        t = t0
        while t < t1 - 1e-9:
            f = (t - t0) / (t1 - t0)
            out.append((round(t, 3), det(color, x0 + f * (x1 - x0), y0 + f * (y1 - y0))))
            t += step
    t1, x1, y1 = points[-1]
    out.append((round(t1, 3), det(color, x1, y1)))
    return out


def static(color, x, y, t0, t1, fps=5.0):
    """A stone sitting still between two times."""
    out, t, step = [], t0, 1.0 / fps
    while t <= t1 + 1e-9:
        out.append((round(t, 3), det(color, x, y)))
        t += step
    return out


class TestCollisionHandoff:
    """A shot that strikes a guard, taken from game 1 end 1 at t=183.

    The delivered red hit the red guard sitting at (+0.18, +2.61). The shooter
    stopped dead at (-0.48, +2.01) and was still there a minute later; the
    guard was driven down-sheet and out the back.

    A stone that stops abruptly looks exactly like a track dying, so the
    tracker's velocity prediction follows the *struck* stone instead -- the one
    still moving the way the shot was going. The delivery therefore appeared to
    run to y = -0.33 and vanish in play, and was discarded, even though its
    resting place was sitting in plain view.
    """

    def frames(self):
        shooter = interp([(181.5, 0.9, 3.90), (183.0, 0.66, 2.31),
                          (184.0, -0.17, 2.21), (185.0, -0.35, 2.08),
                          (186.0, -0.45, 2.01), (187.0, -0.48, 2.01)])
        struck = interp([(183.0, 0.18, 2.61), (184.0, 0.99, 1.66),
                         (185.0, 1.29, 1.02), (186.0, 1.51, 0.41),
                         (187.0, 1.67, -0.09), (188.0, 1.80, -0.60),
                         (189.0, 1.90, -2.10)])
        return merge(
            static("red", 0.18, 2.61, 150.0, 182.8),
            shooter,
            struck,
            static("red", -0.48, 2.01, 187.0, 260.0),
            static("red", 0.35, 1.15, 150.0, 260.0),
            static("yellow", -1.24, 1.62, 150.0, 260.0),
            static("yellow", 0.53, -0.95, 150.0, 260.0),
        )

    def test_the_shot_is_found(self):
        got = delivery.find_deliveries(self.frames())
        assert [d.color for d in got] == ["red"]
        assert got[0].t_enter == pytest.approx(181.5, abs=1.5)

    def test_it_rests_where_the_shooter_stopped_not_where_the_guard_went(self):
        got = delivery.find_deliveries(self.frames())
        assert got[0].rest_x_m == pytest.approx(-0.48, abs=0.2)
        assert got[0].rest_y_m == pytest.approx(2.01, abs=0.2)

    def test_the_struck_guard_is_not_a_second_delivery(self):
        got = delivery.find_deliveries(self.frames())
        assert len(got) == 1


class TestShooterLeavesPlay:
    """A takeout whose shooter rolls out, from game 1 end 1 at t=235.

    The yellow struck the red sitting at (+0.35, +1.16) and removed it, then
    rolled off toward the near side line and out of view. Nothing came to rest,
    so persistence had nothing to confirm -- but the house is a stone lighter
    afterwards, which no sweeper can do.
    """

    def frames(self):
        shooter = interp([(235.0, 0.42, 3.46), (236.0, 0.46, 2.28),
                          (237.0, 0.69, 1.27), (238.0, 1.12, 0.98),
                          (239.0, 1.46, 0.73), (240.0, 1.68, 0.53)],
                         color="yellow")
        struck = interp([(236.6, 0.35, 1.16), (237.0, 0.11, 0.53),
                         (238.0, -0.42, -0.81), (238.6, -0.60, -1.60)])
        return merge(
            static("yellow", 0.42, 3.46, 234.6, 234.9),
            shooter,
            static("red", 0.35, 1.16, 200.0, 236.4),
            struck,
            static("red", -0.48, 2.01, 200.0, 300.0),
            static("yellow", -1.24, 1.62, 200.0, 300.0),
            static("yellow", 0.53, -0.95, 200.0, 300.0),
        )

    def test_the_takeout_is_found(self):
        got = delivery.find_deliveries(self.frames())
        assert [d.color for d in got] == ["yellow"]
        assert got[0].t_enter == pytest.approx(235.0, abs=1.5)

    def test_it_is_not_claimed_to_have_come_to_rest(self):
        got = delivery.find_deliveries(self.frames())
        assert got[0].came_to_rest is False

    def test_the_removed_red_is_not_a_delivery(self):
        got = delivery.find_deliveries(self.frames())
        assert len(got) == 1


class TestHouseUnchanged:
    """A phantom that crosses the sheet but leaves the house exactly as it was.

    A sweeper in a team-coloured jacket runs alongside the delivery, so their
    blob enters up-sheet and traverses several metres -- every hallmark of a
    throw. Accepting a shot on the strength of a changed house must not accept
    these, and the house is what separates them: a sweeper moves no stones.
    """

    def test_a_traverse_that_changes_nothing_is_not_a_delivery(self):
        phantom = interp([(50.0, -1.5, 3.90), (54.0, -1.4, -0.50)],
                         color="yellow")
        frames = merge(
            phantom,
            static("red", 0.35, 1.15, 20.0, 120.0),
            static("yellow", 0.53, -0.95, 20.0, 120.0),
        )
        assert delivery.find_deliveries(frames) == []


class TestLeavesTheViewSideways:
    """The side lines are outside the panel, so a stone that rolls out is lost
    from view before it ever reaches them.

    The overhead panels span about x = -1.9 to +1.9 m, but the side line is at
    2.233 m. Testing the last seen position against the side line is therefore
    a condition that can never be true, and every shooter that rolled out was
    discarded as having vanished. What can be judged is whether the stone was
    still travelling outward when the view ran out.
    """

    def test_a_stone_still_running_off_the_side_has_left_play(self):
        shooter = interp([(10.0, 0.2, 3.80), (13.0, 1.30, 1.20),
                          (15.0, 1.85, 0.60)])
        frames = merge(shooter, static("yellow", -1.0, 0.5, 0.0, 80.0))
        got = delivery.find_deliveries(frames, view_x_limit_m=1.92)
        assert [d.color for d in got] == ["red"]
        assert got[0].came_to_rest is False

    def test_a_late_sideways_jitter_is_not_leaving(self):
        # Ran straight down the sheet and stopped being seen, with a noisy
        # last sample. Extrapolating that sample's velocity reaches the edge;
        # the stone itself had barely drifted and had 1.4 m still to cover.
        shooter = interp([(10.0, -0.20, 3.80), (13.0, -0.30, 1.60),
                          (14.0, -0.35, 1.30), (14.2, -0.47, 1.28)],
                         color="yellow")
        frames = merge(shooter, static("red", 1.0, 0.5, 0.0, 80.0))
        assert delivery.find_deliveries(frames, view_x_limit_m=1.92) == []

    def test_a_stone_lost_mid_sheet_has_not(self):
        shooter = interp([(10.0, 0.2, 3.80), (13.0, 0.25, 1.20),
                          (15.0, 0.30, 0.60)])
        frames = merge(shooter, static("yellow", -1.0, 0.5, 0.0, 80.0))
        assert delivery.find_deliveries(frames, view_x_limit_m=1.92) == []


class TestReason:
    """Each shot records what made it believable, since the four routes are not
    equally strong and a reader should not have to re-derive which applied."""

    def test_a_stone_seen_settling_says_so(self):
        got = delivery.find_deliveries(merge(thrown("red")))
        assert got[0].reason == "rest"

    def test_a_collision_handoff_is_named_by_the_stone_that_appeared(self):
        got = delivery.find_deliveries(TestCollisionHandoff().frames())
        assert got[0].reason == "house-add"

    def test_a_shooter_that_rolled_out_is_named_by_what_it_removed(self):
        got = delivery.find_deliveries(TestShooterLeavesPlay().frames())
        assert got[0].reason == "house-remove"

    def test_a_stone_still_running_as_it_left_says_so(self):
        shooter = interp([(10.0, 0.2, 3.80), (13.0, 1.30, 1.20),
                          (15.0, 1.85, 0.60)])
        frames = merge(shooter, static("yellow", -1.0, 0.5, 0.0, 80.0))
        got = delivery.find_deliveries(frames, view_x_limit_m=1.92)
        assert got[0].reason == "left-view"


class TestOneDeliveryPerHouseChange:
    """A shot moves one stone, so a stone that appeared is evidence for exactly
    one delivery.

    Sweepers wear their team's colours and run alongside the stone they are
    sweeping, so a delivery is routinely accompanied by same-coloured tracks
    that enter up-sheet and cross several metres. Letting the new stone vouch
    for all of them took end 1 from 9 deliveries to 16, and gave end 2 eleven
    yellow stones against a team allowance of eight.
    """

    def frames(self):
        # A red that hands off in a collision, plus two sweepers in red
        # jackets running down alongside it.
        base = TestCollisionHandoff().frames()
        extra = []
        for x in (-1.6, 1.55):
            extra += interp([(181.0, x, 4.30), (187.0, x + 0.1, 1.90)])
        merged = {t: list(d) for t, d in base}
        for t, d in extra:
            merged.setdefault(round(t, 3), []).append(d)
        return [(t, merged[t]) for t in sorted(merged)]

    def test_only_one_delivery_is_credited(self):
        got = delivery.find_deliveries(self.frames())
        assert len(got) == 1, [(d.t_enter, d.reason, d.rest_x_m) for d in got]

    def test_the_one_credited_is_the_stone_not_a_sweeper(self):
        got = delivery.find_deliveries(self.frames())
        assert got[0].rest_x_m == pytest.approx(-0.48, abs=0.2)

    def test_two_shots_far_apart_may_both_account_for_the_same_spot(self):
        # A guard is placed, cleared later, and a second stone draws to where
        # it had been. That is two deliveries, not one claim contested twice.
        first = interp([(20.0, 0.1, 3.80), (24.0, 0.15, 2.70)], color="yellow")
        second = interp([(300.0, 0.1, 3.80), (304.0, 0.15, 2.70)], color="yellow")
        frames = merge(
            first, static("yellow", 0.15, 2.70, 24.0, 120.0),
            second, static("yellow", 0.15, 2.70, 304.0, 400.0),
            static("red", -1.0, 0.5, 0.0, 400.0),
        )
        got = delivery.find_deliveries(frames)
        assert len(got) == 2, [(d.t_enter, d.reason) for d in got]


class TestArrivedAtRest:
    """A guard thrown short, from game 2 end 3 at t=10067.

    It crossed the hog line at 10061 and came to rest at y = +4.08, against a
    panel edge at +4.60 -- so its whole flight happened outside the view and
    its first sighting is already stationary. There is no traverse to follow,
    and the only trace it leaves is that the sheet holds one more yellow.
    """

    def base(self, t_arrive=10067.0, until=10160.0):
        return [
            static("yellow", -0.45, 3.37, 10040.0, until),
            static("red", -0.38, 3.17, 10040.0, until),
            static("red", 0.14, 2.59, 10040.0, until),
            static("yellow", -0.61, 2.21, 10040.0, until),
            static("yellow", -0.18, 1.43, 10040.0, until),
        ]

    def test_a_stone_that_appears_up_sheet_and_stays_is_a_delivery(self):
        frames = merge(*self.base(),
                       static("yellow", 0.0, 4.08, 10067.0, 10160.0))
        got = delivery.find_deliveries(frames)
        assert [(d.color, d.reason) for d in got] == [("yellow", "house-appear")]
        assert got[0].rest_y_m == pytest.approx(4.08, abs=0.05)
        assert got[0].t_enter == pytest.approx(10067.0, abs=0.5)

    def test_a_stone_merely_shifted_is_not_a_delivery(self):
        # Staging: a stone pushed back toward the throwing end arrives at the
        # new spot, but it also leaves the old one, so the count is unchanged.
        frames = merge(
            static("red", -0.38, 3.17, 10040.0, 10160.0),
            static("red", 0.14, 2.59, 10040.0, 10160.0),
            static("yellow", -0.61, 2.21, 10040.0, 10160.0),
            static("yellow", -0.18, 1.43, 10040.0, 10160.0),
            static("yellow", -0.45, 3.37, 10040.0, 10064.0),
            static("yellow", 0.0, 4.08, 10067.0, 10160.0),
        )
        assert delivery.find_deliveries(frames) == []

    def test_a_stone_that_appears_and_then_goes_is_not_a_delivery(self):
        frames = merge(*self.base(),
                       static("yellow", 0.0, 4.08, 10067.0, 10070.0))
        assert delivery.find_deliveries(frames) == []

    def test_a_stone_appearing_down_sheet_is_not_a_delivery(self):
        # Nothing arrives from below: a stone appearing near the house without
        # having crossed the panel was uncovered, not thrown.
        frames = merge(*self.base(),
                       static("yellow", 0.4, -0.9, 10067.0, 10160.0))
        assert delivery.find_deliveries(frames) == []

    def test_stones_there_from_the_start_are_not_deliveries(self):
        assert delivery.find_deliveries(merge(*self.base())) == []


class TestGuardThatBarelyMoves:
    """A red guard from game 1 end 5, observed crossing the hog line at 4282.

    It entered the panel at y = +3.95 already almost stopped, drifted 0.13 m to
    +3.82, and sat there for the next three minutes. That is too much movement
    to look like a stone already in play, and far too little to pass the travel
    gate, so it fell between the two routes and was discarded -- and the rules
    then had to drop a real yellow for want of a red between it and the next.
    """

    def frames(self):
        settled = [
            static("yellow", 0.41, 2.38, 4240.0, 4400.0),
            static("yellow", 0.78, 1.45, 4240.0, 4400.0),
            static("red", 0.24, 0.51, 4240.0, 4400.0),
            static("red", 0.59, -0.47, 4240.0, 4400.0),
            static("red", 0.13, -1.25, 4240.0, 4400.0),
        ]
        arriving = interp([(4285.2, 0.24, 3.95), (4286.0, 0.29, 3.83),
                           (4287.0, 0.33, 3.73), (4288.0, 0.37, 3.68)])
        return merge(*settled, arriving,
                     static("red", 0.37, 3.68, 4288.0, 4400.0))

    def test_the_guard_is_found(self):
        got = delivery.find_deliveries(self.frames())
        assert [(d.color, d.reason) for d in got] == [("red", "house-appear")]

    def test_it_rests_where_it_stopped_not_where_it_entered(self):
        got = delivery.find_deliveries(self.frames())
        assert got[0].rest_y_m == pytest.approx(3.68, abs=0.05)
        assert got[0].rest_x_m == pytest.approx(0.37, abs=0.05)

    def test_a_stone_nudged_without_the_count_rising_is_not_a_delivery(self):
        # A broom clipping a stone in play moves it about as far. The sheet
        # holds no more stones afterwards, which is what separates the two.
        nudged = interp([(4285.2, 0.24, 3.95), (4288.0, 0.37, 3.68)])
        frames = merge(
            static("red", 0.24, 3.95, 4240.0, 4285.0),
            nudged,
            static("red", 0.37, 3.68, 4288.0, 4400.0),
            static("yellow", 0.78, 1.45, 4240.0, 4400.0),
            static("red", 0.24, 0.51, 4240.0, 4400.0),
        )
        assert delivery.find_deliveries(frames) == []


class TestParkedStoneIsNotAnArrival:
    """The stones waiting to be thrown sit at the delivery end, right where the
    players stand.

    One sat at (+0.32, +4.43) for 94% of game 1 end 6 -- 8480 of 9000 frames,
    with a single absence longer than five seconds in the whole end. Because
    players walk over it, it drops out of a six-second window and comes back,
    which raises the colour's count and reads exactly like a stone arriving.
    It was accepted as a delivery three times in that one end.
    """

    def parked(self, t0, t1, holes=()):
        """A stone at the delivery end, hidden now and then by passers-by."""
        out = []
        t = t0
        while t <= t1:
            if not any(a <= t <= b for a, b in holes):
                out.append((round(t, 3), det("yellow", 0.32, 4.43)))
            t += 0.2
        return out

    def others(self, t0, t1):
        return [static("red", 0.20, -0.42, t0, t1),
                static("red", 0.82, -1.37, t0, t1)]

    def test_a_stone_hidden_and_seen_again_is_not_a_delivery(self):
        frames = merge(*self.others(4990.0, 5120.0),
                       self.parked(4990.0, 5120.0, holes=((5052.0, 5060.0),)))
        assert delivery.find_deliveries(frames) == []

    def test_a_genuine_arrival_at_the_same_height_still_counts(self):
        # Same part of the sheet, but nothing was sitting there beforehand.
        frames = merge(*self.others(4990.0, 5120.0),
                       static("yellow", -1.20, 4.20, 5060.0, 5120.0))
        got = delivery.find_deliveries(frames)
        assert [(d.color, d.reason) for d in got] == [("yellow", "house-appear")]

    def test_the_look_back_reaches_past_a_long_occlusion(self):
        # Twelve seconds out of sight -- twice the change window -- and it is
        # still the same parked stone.
        frames = merge(*self.others(4990.0, 5120.0),
                       self.parked(4990.0, 5120.0, holes=((5048.0, 5060.0),)))
        assert delivery.find_deliveries(frames) == []

    def test_too_little_history_does_not_claim_a_delivery(self):
        # The stone is there from the first frame available, so there is no
        # look-back to judge it by and no grounds to call it thrown.
        frames = merge(*self.others(5060.0, 5120.0),
                       static("yellow", 0.32, 4.43, 5060.0, 5120.0))
        assert delivery.find_deliveries(frames) == []


class TestBorrowedStart:
    """A delivery passes within a stone's width of the stones parked at the
    delivery end, so frame to frame the tracker cannot tell which is which.

    Over the end it can: at 5084 in game 1 end 6 there were yellows at both
    (+0.33, +4.43) and (+1.53, -1.04), so the parked stone plainly never moved.
    Without that check the hit that entered at 5075 was recorded as entering at
    4995.6 -- an eighty-second error that put it before the red at 4996.8, an
    impossible 1.2 s pair, and broke the turn order badly enough that the rules
    dropped a real red 150 s later.
    """

    def frames(self):
        # The parked stone, then the hit passing close by it on the way in.
        hit = interp([(5075.0, 0.09, 3.89), (5076.0, 0.17, 2.89),
                      (5078.0, 0.32, -0.09), (5080.0, 1.12, -0.66),
                      (5084.0, 1.53, -1.04)], color="yellow")
        return merge(
            static("yellow", 0.32, 4.43, 4990.0, 5160.0),
            hit,
            static("yellow", 1.53, -1.04, 5084.0, 5160.0),
            static("red", 0.20, -0.42, 4990.0, 5079.0),
            static("red", 0.82, -1.37, 4990.0, 5160.0),
        )

    def test_the_entry_time_is_when_the_stone_actually_came_in(self):
        got = [d for d in delivery.find_deliveries(self.frames())
               if d.color == "yellow" and d.rest_y_m < 0]
        assert len(got) == 1
        assert got[0].t_enter == pytest.approx(5075.0, abs=2.0)

    def test_the_parked_stone_is_not_credited_with_the_journey(self):
        got = delivery.find_deliveries(self.frames())
        assert all(d.entry_y_m < 4.3 for d in got), [d.entry_y_m for d in got]

    def test_a_track_whose_start_really_did_leave_is_untouched(self):
        # Nothing remains where it began, so the whole track is one stone.
        moved = interp([(100.0, 0.30, 3.90), (104.0, 0.35, 0.10)],
                       color="yellow")
        frames = merge(moved, static("yellow", 0.35, 0.10, 104.0, 200.0),
                       static("red", -1.0, 0.5, 90.0, 200.0))
        got = delivery.find_deliveries(frames)
        assert len(got) == 1
        assert got[0].t_enter == pytest.approx(100.0, abs=0.5)
        assert got[0].entry_y_m == pytest.approx(3.90, abs=0.1)


class TestLateEntry:
    """A takeout first seen well down the sheet, from game 1 end 3 at 2412.

    The model picks red up much further down than yellow -- this one was not
    seen above y = +1.69, while the sweepers running alongside it were found at
    +4.15 -- so the entry gate that rejects struck stones cuts real shots of
    one colour only. A struck stone moves; it does not add one, so a colour
    whose count went up in a place nothing of that colour was sitting had a
    stone delivered.
    """

    def frames(self):
        arriving = interp([(2414.9, 0.99, 1.69), (2416.5, 0.65, 0.68),
                           (2418.0, 0.36, 0.51), (2419.5, 0.33, 0.49)])
        return merge(
            static("red", 0.57, 1.25, 2360.0, 2500.0),
            static("yellow", 1.10, 0.56, 2360.0, 2416.0),
            arriving,
            static("red", 0.33, 0.49, 2419.5, 2500.0),
        )

    def test_the_takeout_is_found(self):
        got = delivery.find_deliveries(self.frames())
        assert [(d.color, d.reason) for d in got] == [("red", "late-entry")]

    def test_it_rests_where_it_stopped(self):
        got = delivery.find_deliveries(self.frames())
        assert got[0].rest_x_m == pytest.approx(0.33, abs=0.1)
        assert got[0].rest_y_m == pytest.approx(0.49, abs=0.1)

    def test_a_struck_stone_is_still_not_a_delivery(self):
        # The same shape of track, but the colour's count is unchanged: this
        # stone was knocked from one place to another, not thrown.
        struck = interp([(2414.9, 0.57, 1.25), (2418.0, 0.36, 0.51),
                         (2419.5, 0.33, 0.49)])
        frames = merge(
            static("red", 0.57, 1.25, 2360.0, 2414.5),
            struck,
            static("red", 0.33, 0.49, 2419.5, 2500.0),
            static("yellow", 1.10, 0.56, 2360.0, 2500.0),
        )
        assert delivery.find_deliveries(frames) == []

    def test_a_stone_uncovered_where_one_already_sat_is_not_a_delivery(self):
        # The place was occupied beforehand, so its reappearance is not an
        # arrival however much the count seems to rise.
        frames = merge(
            static("red", 0.33, 0.49, 2360.0, 2410.0),
            static("red", 0.57, 1.25, 2360.0, 2500.0),
            interp([(2414.9, 0.40, 0.90), (2419.5, 0.33, 0.49)]),
            static("red", 0.33, 0.49, 2419.5, 2500.0),
            static("yellow", 1.10, 0.56, 2360.0, 2500.0),
        )
        assert delivery.find_deliveries(frames) == []


class TestLateEntryStaysWeak:
    """A shot whose arrival was never seen is weaker evidence than one watched
    the whole way down, whatever later confirms it.

    In game 2 end 1 a track first seen at y = -0.80 -- behind the tee, never
    seen in the 4.6 m above it -- was confirmed by a stone appearing, which
    overwrote its reason and gave it the same weight as the real delivery 29 s
    later that entered at +3.62. With equal weight the fit's choice between
    them was arbitrary, and it took the wrong one.
    """

    def test_a_track_first_seen_behind_the_tee_is_refused(self):
        arriving = interp([(7700.0, -0.60, -0.80), (7703.0, -0.82, -0.99)])
        frames = merge(
            static("red", 0.03, 2.26, 7600.0, 7800.0),
            arriving,
            static("red", -0.82, -0.99, 7703.0, 7800.0),
            static("yellow", -0.14, -0.40, 7600.0, 7800.0),
        )
        assert delivery.find_deliveries(frames) == []

    def test_a_late_entry_keeps_its_own_reason(self):
        got = delivery.find_deliveries(TestLateEntry().frames())
        assert [d.reason for d in got] == ["late-entry"]

    def test_it_is_weighted_below_a_shot_seen_from_the_top(self):
        from curling_score.game import fit

        assert fit.weight(_r("late-entry")) < fit.weight(_r("house-add"))
        assert fit.weight(_r("late-entry")) < fit.weight(_r("rest"))


def _r(reason):
    from curling_score.detect.delivery import Delivery

    return Delivery(color="red", t_enter=0.0, t_rest=1.0, entry_y_m=1.0,
                    rest_x_m=0.0, rest_y_m=0.0, travel_m=1.0, reason=reason)


class TestAStoneCreepingInAtTheEdge:
    """Game 3 end 4 of the 5U championship, the yellow lead's first guard.

    First seen at y = +4.60, right at the top of the panel, it moved 0.05 m in
    its first 0.7 s -- a bounding box clipped by the frame edge barely moves
    -- then vanished under the sweepers for half a second and reappeared 0.2 m
    further on, running at 0.2 m/s to stop a metre down the sheet. The rest
    test asked whether it stayed put for a second, saw 0.7 s of near-stillness
    with nothing after it inside the second, and called it at rest from its
    very first sighting. Judged as a stone that had always been there, it was
    then refused for not being new.
    """

    def _guard(self):
        tr = interp([(0.0, -0.07, 4.60), (0.7, -0.02, 4.55), (1.3, 0.01, 4.41),
                     (6.5, 0.34, 3.60), (7.6, 0.36, 3.57)], fps=10.0, color="yellow")
        tr = drop(tr, [(0.75, 1.25)])
        return tr + resting("yellow", 0.36, 3.57, 7.7, 60.0)

    def test_it_is_a_delivery(self):
        got = delivery.find_deliveries(merge(self._guard()))
        assert len(got) == 1
        assert got[0].came_to_rest is True
        assert got[0].t_enter == pytest.approx(0.0, abs=0.2)

    def test_it_rests_where_it_stopped_not_where_it_hesitated(self):
        got = delivery.find_deliveries(merge(self._guard()))
        assert got[0].rest_y_m == pytest.approx(3.57, abs=0.1)
        assert got[0].travel_m > 0.9


class TestAParkedStoneAnnexedByAPassingOne:
    """Game 3 end 4 again, the red lead's first guard -- the one the chart
    called rock 1's predecessor and never listed.

    It arrived at 2824.6 and parked at (+0.36, +4.22) for four minutes. At 3078
    a red draw passed 0.3 m from it while the sweepers hid it, and the tracker
    handed the guard's track to the passing stone. The borrowed-start trim
    rightly gave the flight back to the shooter -- and threw the guard's
    arrival away with the history it trimmed. The guard is a stone in its own
    right; if it arrived within the sequence, that arrival is a delivery.
    """

    def _frames(self):
        quiet = [(round(t * 0.1, 3), []) for t in range(0, 400)]     # 40 s of empty ice
        guard = interp([(40.0, 0.30, 4.30), (41.5, 0.36, 4.22)], fps=10.0, color="red")
        guard += resting("red", 0.36, 4.22, 41.6, 200.0)
        guard = drop(guard, [(99.3, 100.2)])          # hidden as the shooter passes
        shooter = thrown("red", y0=4.60, y1=-1.0, t0=99.0, speed=0.8, fps=10.0, x=0.10)
        return quiet + merge(guard, shooter)

    def test_the_tracker_really_does_hand_the_track_over(self):
        # The premise: one red track runs from the guard's arrival to the
        # shooter's resting place. Without that the rest of the class proves
        # nothing.
        tracks = [t for t in delivery._build_tracks(self._frames())
                  if t.ts[0] <= 41.0 and t.ys[-1] < 0.0]
        assert len(tracks) == 1

    def test_both_stones_are_deliveries(self):
        got = sorted(delivery.find_deliveries(self._frames()), key=lambda d: d.t_enter)
        assert len(got) == 2
        assert got[0].t_enter == pytest.approx(40.0, abs=0.2)
        # The shooter's first sightings went to the parked stone's track, so
        # its own account starts a little late -- but there is one, and only one.
        assert 99.0 <= got[1].t_enter <= 100.5

    def test_the_guard_is_where_it_parked_and_the_shooter_where_it_stopped(self):
        got = sorted(delivery.find_deliveries(self._frames()), key=lambda d: d.t_enter)
        assert got[0].rest_y_m == pytest.approx(4.22, abs=0.1)
        assert got[1].rest_y_m == pytest.approx(-1.0, abs=0.2)
        assert got[1].travel_m > 4.5   # its account starts where the handover did

    def test_a_stone_parked_before_the_footage_began_is_still_not_one(self):
        # The same handover, but the guard was there from the first frame:
        # nothing says it arrived, so nothing may call it a delivery.
        frames = [(t, d) for t, d in self._frames() if t >= 41.6]
        got = delivery.find_deliveries(frames)
        assert len(got) == 1 and 99.0 <= got[0].t_enter <= 100.5
        assert got[0].rest_y_m == pytest.approx(-1.0, abs=0.2)

    def test_the_shooters_orphaned_first_sightings_do_not_become_a_delivery(self):
        # Cut off when the parked stone's track took the shooter over, the
        # fragment ends 0.14 m from where that stone reappears a second later.
        # Linking the two would make a red that "came to rest" exactly where a
        # red had been sitting for a minute. The spot was occupied; no link.
        got = delivery.find_deliveries(self._frames())
        assert not any(d.travel_m < 1.0 and d.t_enter > 60 for d in got), \
            [(d.t_enter, d.travel_m, d.reason) for d in got]


class TestAStoneLostWhileStillRunning:
    """Game 3 end 6 of the 5U championship, the red lead's first rock.

    Tracked from the top of the panel down to +0.63 m at 0.6 m/s, then the
    players closed over it for 5.7 s; when they moved off it sat 2.3 m further
    on. Too long a gap for the linker, and the house-change check opened its
    after-window while the stone was still rolling, so it was seen in 30% of
    it against the 40% that counts as settled. A stone that was still moving
    when it was lost needs time to stop before the house can show it.
    """

    def _frames(self):
        quiet = [(round(t * 0.1, 3), []) for t in range(0, 300)]     # 30 s of empty ice
        flight = interp([(30.0, -0.46, 4.60), (37.3, -0.23, 0.63)], fps=10.0, color="red")
        rest = resting("red", 0.31, -1.61, 43.0, 90.0)
        return quiet + merge(flight, rest)

    def test_it_is_a_delivery_that_came_to_rest_where_it_was_next_seen(self):
        got = delivery.find_deliveries(self._frames())
        assert len(got) == 1
        assert got[0].t_enter == pytest.approx(30.0, abs=0.2)
        assert got[0].came_to_rest is True
        assert got[0].rest_y_m == pytest.approx(-1.61, abs=0.15)

    def test_a_stone_that_stopped_in_view_gets_no_extra_time(self):
        # Ended at rest: the house is read in the usual window, and a stone
        # appearing fifteen seconds later is somebody else's.
        tr = thrown("red", y0=4.2, y1=1.0, t0=0.0, speed=0.5, fps=10.0)
        late = resting("red", 0.9, -1.5, tr[-1][0] + 12.0, tr[-1][0] + 40.0)
        got = delivery.find_deliveries(merge(tr, late))
        assert len(got) == 1
        assert got[0].rest_y_m == pytest.approx(1.0, abs=0.2)

    def test_a_stone_that_left_play_is_not_read_as_resting_where_the_next_one_lands(self):
        # A fast stone vanishing through the back; the next delivery's stone
        # settles 20 s later. The run-out is capped well short of that.
        peel = interp([(0.0, 0.1, 4.60), (3.0, 0.1, -1.9)], fps=10.0, color="red")
        nxt = resting("red", -0.5, 0.5, 25.0, 70.0)
        got = delivery.find_deliveries(merge(peel, nxt))
        assert not any(d.came_to_rest and d.t_enter < 1.0 for d in got), \
            [(d.t_enter, d.reason, d.rest_y_m) for d in got]


class TestAGuardFrozenOnItsOwnColour:
    """Game 3 end 7, the yellow lead's... ninth rock of the end.

    A yellow guard sat at (-0.08, +3.95). The next yellow came in at the top
    of the panel, slid a quarter of a metre and froze against it, a diameter
    away -- which is exactly the tolerance the was-the-spot-empty test uses,
    so a freeze on one's own colour could never pass it. The detector also
    boxed the two touching stones as one for a while, so the track's settled
    position was the parked stone's. The spot gained a stone: two yellows
    within reach where there had been one.
    """

    def _frames(self):
        parked = resting("yellow", -0.08, 3.95, 0.0, 120.0)
        # Frozen: one diameter away, 0.29 m -- as close as two stones can be.
        arrive = interp([(60.0, -0.36, 4.12), (62.0, -0.36, 3.80)], fps=10.0, color="yellow")
        frozen = resting("yellow", -0.36, 3.80, 62.1, 120.0)
        return merge(parked, arrive, frozen)

    def test_the_freeze_is_a_delivery(self):
        got = delivery.find_deliveries(self._frames())
        assert [round(d.t_enter) for d in got] == [60]

    def test_it_rests_where_the_new_stone_sits_not_on_the_parked_one(self):
        got = delivery.find_deliveries(self._frames())
        assert got[0].rest_x_m == pytest.approx(-0.36, abs=0.1)
        assert got[0].rest_y_m == pytest.approx(3.80, abs=0.1)

    def test_a_parked_stone_showing_again_is_still_not_a_delivery(self):
        # Hidden for five seconds and back in the same place: one yellow
        # before, one after. The spot gained nothing.
        parked = drop(resting("yellow", -0.08, 3.95, 0.0, 120.0), [(60.0, 65.0)])
        assert delivery.find_deliveries(merge(parked)) == []
