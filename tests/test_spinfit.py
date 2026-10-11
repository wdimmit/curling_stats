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
