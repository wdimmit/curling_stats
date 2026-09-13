"""The harvest CLI surface, which the ds11 README documents verbatim."""
import pytest

from curling_score import cli


def parse(argv):
    """Parse without running: the stage functions all touch the network or GPU."""
    import argparse

    parser = argparse.ArgumentParser(prog="curling-score")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    cli._add_harvest(sub)
    return parser.parse_args(argv)


class TestStages:
    @pytest.mark.parametrize("stage", ["plan", "clips", "pool", "select", "build"])
    def test_every_stage_is_reachable(self, stage):
        # Five stages because the run is an hour of network and half an hour of
        # GPU; a job that starts over on a hiccup is a job nobody leaves running.
        assert stage in cli._stage_names()

    def test_plan_takes_a_playlist(self):
        args = parse(["harvest", "plan", "https://example/pl"])
        assert args.stage == "plan" and args.playlist == "https://example/pl"

    def test_clips_defaults_to_ten_parallel(self):
        # YouTube throttles per connection, not per client: 0.26 MB/s on one
        # stream against 1.53 MB/s on ten.
        args = parse(["harvest", "clips", "--root", "/tmp/c"])
        assert args.jobs == 10 and args.clips == 10

    def test_select_defaults_to_the_full_wave(self):
        args = parse(["harvest", "select", "--pool", "/tmp/p"])
        assert args.wave == 2

    def test_select_can_ask_for_the_pilot(self):
        assert parse(["harvest", "select", "--pool", "/tmp/p", "--wave", "1"]).wave == 1

    def test_select_can_ask_for_the_throw_wave(self):
        assert parse(["harvest", "select", "--pool", "/tmp/p", "--wave", "3"]).wave == 3

    def test_there_is_no_fourth_wave(self):
        with pytest.raises(SystemExit):
            parse(["harvest", "select", "--pool", "/tmp/p", "--wave", "4"])

    def test_pool_requires_the_weights_that_write_the_first_guess(self):
        with pytest.raises(SystemExit):
            parse(["harvest", "pool", "--root", "/tmp/c", "--out", "/tmp/o"])
