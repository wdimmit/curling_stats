"""Reading a rock's rotation from the swing of its handle."""
import numpy as np
import pytest

from curling_score.game import spinfit


def swing(f=0.2, seconds=10.0, fps=10.0, a=0.2, noise=0.03, phase=0.7, decay=0.0, seed=1):
    """The grip's offset across from the neck: once round per turn, f turns a
    second at the start, slowing by ``decay`` turns/s each second."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, seconds, 1 / fps)
    turns = f * t + 0.5 * decay * t ** 2
    return t, a * np.sin(2 * np.pi * turns + phase) + rng.normal(0, noise, t.size)


def test_a_steady_rock_reads_its_rate_in_rpm():
    got = spinfit.read(*swing(f=0.2))
    assert got.rpm == pytest.approx(12.0, abs=0.4)
    assert got.rpm_avg == pytest.approx(12.0, abs=0.4)
    assert got.turns_in_view == pytest.approx(2.0, abs=0.15)
    assert got.samples == 100


def test_the_rate_reported_is_the_early_one():
    # 0.26 turns/s at the start, slowing to ~0.18 by the end of ten seconds.
    got = spinfit.read(*swing(f=0.26, decay=-0.008))
    assert got.rpm > got.rpm_avg
    assert got.rpm == pytest.approx(60 * 0.245, rel=0.06)


def test_a_slow_rock_needs_only_half_a_turn_in_view():
    got = spinfit.read(*swing(f=0.07, seconds=9.0, noise=0.02))
    assert got.rpm == pytest.approx(4.2, rel=0.15)


def test_a_fast_rock():
    assert spinfit.read(*swing(f=0.47)).rpm == pytest.approx(28.2, rel=0.03)


def test_a_quarter_wave_is_not_a_reading():
    """A fast hit gone from the frame after a fraction of a turn."""
    assert spinfit.read(*swing(f=0.05, seconds=4.0)) is None


def test_a_flat_or_unreadable_swing_is_not_a_slow_rock():
    t, _ = swing()
    rng = np.random.default_rng(2)
    assert spinfit.read(t, rng.normal(0, 0.03, t.size)) is None          # nothing there
    assert spinfit.read(*swing(a=0.08, noise=0.06)) is None              # too faint


def test_too_few_frames_is_not_a_reading():
    t, s = swing()
    assert spinfit.read(t[:spinfit.MIN_SAMPLES - 1], s[:spinfit.MIN_SAMPLES - 1]) is None


def test_a_few_wild_frames_do_not_move_it():
    t, s = swing(f=0.2)
    s = s.copy(); s[::17] = 0.6                                             # a box on a broom
    assert spinfit.read(t, s).rpm == pytest.approx(12.0, rel=0.05)


def test_gaps_in_the_frames_are_fine():
    t, s = swing(f=0.2)
    keep = np.ones(t.size, bool); keep[30:45] = False                       # a sweeper in the way
    assert spinfit.read(t[keep], s[keep]).rpm == pytest.approx(12.0, rel=0.04)


@pytest.mark.parametrize("n, phase", [(31, 0.0), (31, 1.0), (31, 2.0), (32, 0.5), (31, 3.0)])
def test_under_half_a_turn_is_not_read_fast(n, phase):
    # Three seconds of a 9 rpm rock -- a track lost early, or a rock coming to
    # rest -- is under half a turn. The whole-stretch fit reads it 40-50% fast,
    # enough to pass the turns gate, and the early fit then runs out to the
    # edge of its anchor: 9.0-12.2 rpm before this gate.
    t = 101.4 + np.arange(n) / 10
    got = spinfit.read(t, 0.2 * np.sin(2 * np.pi * 0.15 * t + phase))
    assert got is None or got.rpm == pytest.approx(9.0, rel=0.05)


# --- A rock under a turn in view: the handle's swing held -----------------

SWING = (0.22, -0.02)


def slow(f, seconds=7.0, phase=0.7, noise=0.03, a=0.22, c=-0.02, seed=3):
    """A rock turning ``f`` turns a second whose handle swings ``a`` about ``c``."""
    t, s = swing(f=f, seconds=seconds, a=a, noise=noise, phase=phase, seed=seed)
    return t, s + c


def held(t, s, swing_=SWING):
    return spinfit.read_with_swing(t, s, swing_, spinfit.read(t, s)).spin


@pytest.mark.parametrize("phase", [0.0, 0.7, 1.6, 2.5])
def test_under_half_a_turn_is_read_with_the_swing_held_and_given_as_a_range(phase):
    t, s = slow(f=0.06, phase=phase)               # 3.6 rpm: 0.42 turns in 7 s
    got = held(t, s)
    assert got.rpm == pytest.approx(3.6, rel=0.10)
    lo, hi = got.rpm_range
    assert lo <= 3.6 <= hi and (hi - lo) / got.rpm <= spinfit.MAX_RANGE


def test_between_half_a_turn_and_one_it_is_one_number():
    t, s = slow(f=0.11)                            # 6.6 rpm: 0.77 turns
    got = held(t, s)
    assert got.rpm == pytest.approx(6.6, rel=0.06) and got.rpm_range is None
    assert got.turns_in_view == pytest.approx(0.77, abs=0.08)


def test_a_turn_or_more_reads_as_it_always_has():
    t, s = swing(f=0.2)
    first = spinfit.read(t, s)
    got = spinfit.read_with_swing(t, s, SWING, first)
    assert got.spin is first and not got.under_a_turn


def test_a_fast_rock_reads_as_it_always_has():
    t, s = swing(f=0.7)                            # 42 rpm, past the held fit's grid
    first = spinfit.read(t, s)
    assert first.rpm == pytest.approx(42.0, rel=0.03)
    assert spinfit.read_with_swing(t, s, SWING, first).spin is first


def test_today_s_read_of_a_turn_or_more_stands_whatever_the_held_fit_says():
    t, s = slow(f=0.06)
    first = spinfit.Spin(rpm=6.9, rpm_avg=6.9, turns_in_view=1.04, samples=t.size)
    assert spinfit.read_with_swing(t, s, SWING, first).spin is first


def test_a_loose_held_fit_keeps_today_s_read():
    # 0.64 turns of a handle swinging 0.2 about 0, held at 0.22 about -0.02:
    # the held range runs 6-14 rpm, too loose, and today's 12.4 stands.
    t, s = swing(f=0.2, seconds=3.9, a=0.2, noise=0.03, seed=3)
    first = spinfit.read(t, s)
    got = spinfit.read_with_swing(t, s, SWING, first)
    assert first is not None and got.spin is first and got.under_a_turn


def test_a_faint_rock_of_many_turns_is_not_rescued():
    t, s = swing(f=0.3, a=0.08, noise=0.06)
    assert held(t, s) is None


def test_nothing_there_is_not_a_slow_rock():
    rng = np.random.default_rng(5)
    t = np.arange(0, 8, 0.1)
    assert held(t, rng.normal(0, 0.03, t.size) - 0.02) is None


def test_a_clean_read_carries_its_handle_s_swing():
    t, s = swing(f=0.2, a=0.23)
    a, c = spinfit.read(t, s - 0.02).swing
    assert a == pytest.approx(0.23, abs=0.01) and c == pytest.approx(-0.02, abs=0.01)


def test_a_read_under_a_turn_carries_no_swing():
    t, s = slow(f=0.11)
    first = spinfit.read(t, s)
    assert first is None or first.swing is None


class TestTheGridFittedAtOnce:
    """`fit` fits every (f, k) cell at once; it must pick the cell the
    cell-by-cell loop picks, with the same amplitude, noise and centre."""

    @pytest.mark.parametrize("seed", range(12))
    def test_the_same_fit_as_cell_by_cell(self, seed):
        rng = np.random.default_rng(seed)
        f, span = rng.uniform(0.06, 0.45), rng.choice([3.0, 6.0, 10.0])
        t = np.arange(0.5, 0.5 + span, 0.1)
        s = 0.21 * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.3)) - 0.02 \
            + rng.normal(0, 0.04, t.size)
        stray = rng.random(t.size) < 0.05
        s[stray] += rng.normal(0, 0.4, stray.sum())
        f0 = f * rng.uniform(0.85, 1.15)
        for drifts in (spinfit.DRIFTS, (0.0,)):
            want = spinfit._fit_cells(t, s, f0, drifts)
            got = spinfit.fit(t, s, f0, drifts)
            assert got[:2] == want[:2]
            assert got[2:] == pytest.approx(want[2:], rel=1e-9, abs=1e-12)
