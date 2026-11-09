from pathlib import Path

import pytest

from curling_score import weights


class TestDefaultPath:
    def test_prefers_an_explicit_environment_override(self, tmp_path, monkeypatch):
        # A deployment pins its own model; it must not silently inherit ours.
        f = tmp_path / "other.pt"
        f.write_bytes(b"x")
        monkeypatch.setenv("CURLING_SCORE_WEIGHTS", str(f))
        assert weights.default_path() == f

    def test_falls_back_to_the_repository_copy(self, monkeypatch):
        monkeypatch.delenv("CURLING_SCORE_WEIGHTS", raising=False)
        got = weights.default_path()
        assert got is not None and got.name == weights.DEFAULT_NAME
        assert got.is_file()

    def test_says_what_is_missing_rather_than_falling_back_quietly(
            self, tmp_path, monkeypatch):
        # Dropping to the colour detector without saying so would make every
        # downstream number quietly incomparable.
        monkeypatch.setenv("CURLING_SCORE_WEIGHTS", str(tmp_path / "gone.pt"))
        with pytest.raises(FileNotFoundError, match="gone.pt"):
            weights.default_path()

    @pytest.mark.parametrize("asked", ["none", "classical", "None"])
    def test_cannot_be_asked_for_the_colour_detector(self, monkeypatch, asked):
        # User rule 2026-11-09: no model, no run -- never the colour thresholds.
        monkeypatch.setenv("CURLING_SCORE_WEIGHTS", asked)
        with pytest.raises(weights.NoModelError, match="classical colour detector"):
            weights.default_path()


class TestRequire:
    def test_a_weights_file_passes_through_unchanged(self, tmp_path):
        f = tmp_path / "m.pt"
        f.write_bytes(b"x")
        assert weights.require(str(f)) == str(f)
        assert weights.require(f) == f

    @pytest.mark.parametrize("asked", [None, "", "none", "classical", " None "])
    def test_no_model_is_refused(self, asked):
        with pytest.raises(weights.NoModelError):
            weights.require(asked)

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="gone.pt"):
            weights.require(tmp_path / "gone.pt")


class TestSidePath:
    def test_a_missing_side_model_is_an_error(self, monkeypatch):
        # There is no colour scan to fall back to any more.
        monkeypatch.delenv("CURLING_SCORE_SIDE_WEIGHTS", raising=False)
        monkeypatch.setattr(weights, "SIDE_NAME", "no-such-model.pt")
        with pytest.raises(FileNotFoundError, match="no-such-model.pt"):
            weights.side_path()

    def test_none_switches_it_off(self, monkeypatch):
        monkeypatch.setenv("CURLING_SCORE_SIDE_WEIGHTS", "none")
        assert weights.side_path() is None


class TestTheWorkerImageShipsBothDetectors:
    """A worker image built without the side model would fail every run; one
    with an unpinned model would reprocess an archive and quietly republish
    answers from a model nobody chose."""

    def _dockerfile(self):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / "Dockerfile.worker").read_text()

    def test_it_copies_the_side_model(self):
        assert "COPY weights/${SIDE_MODEL}" in self._dockerfile()

    def test_it_pins_the_side_model_by_environment(self):
        """Pinned, not left to resolution: an explicitly set path that is
        missing raises, where an unset one silently falls back."""
        df = self._dockerfile()
        assert "CURLING_SCORE_SIDE_WEIGHTS=/opt/curling/weights/${SIDE_MODEL}" in df

    def test_the_default_matches_what_the_repo_ships(self):
        from curling_score import weights
        assert f"ARG SIDE_MODEL={weights.SIDE_NAME}" in self._dockerfile()

    def test_it_ships_and_pins_the_broom_model(self):
        """Same failure, same cure: `broom_path` treats a missing file as "no
        brooms", so an image without it would reprocess the archive and publish
        every shot with a null broom. Pinned, a missing file is a failure."""
        from curling_score import weights
        df = self._dockerfile()
        assert f"ARG BROOM_MODEL={weights.BROOM_NAME}" in df
        assert "COPY weights/${BROOM_MODEL}" in df
        assert "CURLING_SCORE_BROOM_WEIGHTS=/opt/curling/weights/${BROOM_MODEL}" in df

    def test_it_ships_and_pins_the_spin_model(self):
        """`spin_path` treats a missing file as "no spin", so an image built
        without it would publish every rock with a null spin. Pinned, a missing
        file is a failure."""
        from curling_score import weights
        df = self._dockerfile()
        assert f"ARG SPIN_MODEL={weights.SPIN_NAME}" in df
        assert "COPY weights/${SPIN_MODEL}" in df
        assert "CURLING_SCORE_SPIN_WEIGHTS=/opt/curling/weights/${SPIN_MODEL}" in df

    def test_it_ships_and_pins_the_release_model(self):
        """`release_path` treats a missing file as "no release reads", and the
        image's weights live only under /opt/curling/weights, so an image built
        without the pin would publish every rock with no release. Pinned, a
        missing file is a failure."""
        from curling_score import weights
        df = self._dockerfile()
        assert f"ARG RELEASE_MODEL={weights.RELEASE_NAME}" in df
        assert "COPY weights/${RELEASE_MODEL}" in df
        assert "CURLING_SCORE_RELEASE_WEIGHTS=/opt/curling/weights/${RELEASE_MODEL}" in df


class TestBroomPath:
    """No broom model means no brooms, never a broken run."""

    def test_absent_is_no_brooms_not_an_error(self, monkeypatch):
        monkeypatch.delenv(weights.BROOM_ENV_VAR, raising=False)
        monkeypatch.setattr(weights, "_candidates", lambda name=None: iter(()))
        assert weights.broom_path() is None

    def test_none_switches_it_off(self, monkeypatch):
        monkeypatch.setenv(weights.BROOM_ENV_VAR, "none")
        assert weights.broom_path() is None

    def test_a_set_but_missing_path_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.BROOM_ENV_VAR, str(tmp_path / "nope.pt"))
        with pytest.raises(FileNotFoundError):
            weights.broom_path()

    def test_a_set_path_is_used(self, monkeypatch, tmp_path):
        p = tmp_path / "b.pt"; p.write_bytes(b"x")
        monkeypatch.setenv(weights.BROOM_ENV_VAR, str(p))
        assert weights.broom_path() == p


class TestSpinPath:
    """No spin model means no spin, never a broken run."""

    def test_absent_is_no_spin_not_an_error(self, monkeypatch):
        monkeypatch.delenv(weights.SPIN_ENV_VAR, raising=False)
        monkeypatch.setattr(weights, "_candidates", lambda name=None: iter(()))
        assert weights.spin_path() is None

    def test_none_switches_it_off(self, monkeypatch):
        monkeypatch.setenv(weights.SPIN_ENV_VAR, "none")
        assert weights.spin_path() is None

    def test_a_set_but_missing_path_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.SPIN_ENV_VAR, str(tmp_path / "nope.pt"))
        with pytest.raises(FileNotFoundError):
            weights.spin_path()

    def test_a_set_path_is_used(self, monkeypatch, tmp_path):
        p = tmp_path / "s.pt"; p.write_bytes(b"x")
        monkeypatch.setenv(weights.SPIN_ENV_VAR, str(p))
        assert weights.spin_path() == p

    def test_the_repository_ships_the_default(self):
        assert (Path(__file__).resolve().parents[1] / "weights" / weights.SPIN_NAME).is_file()


class TestReleasePath:
    """No release model means no release reads, never a broken run."""

    def test_absent_is_no_release_model_not_an_error(self, monkeypatch):
        monkeypatch.delenv(weights.RELEASE_ENV_VAR, raising=False)
        monkeypatch.setattr(weights, "_candidates", lambda name=None: iter(()))
        assert weights.release_path() is None

    def test_none_switches_it_off(self, monkeypatch):
        monkeypatch.setenv(weights.RELEASE_ENV_VAR, "none")
        assert weights.release_path() is None

    def test_a_set_but_missing_path_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.RELEASE_ENV_VAR, str(tmp_path / "nope.pt"))
        with pytest.raises(FileNotFoundError):
            weights.release_path()

    def test_a_set_path_is_used(self, monkeypatch, tmp_path):
        p = tmp_path / "r.pt"; p.write_bytes(b"x")
        monkeypatch.setenv(weights.RELEASE_ENV_VAR, str(p))
        assert weights.release_path() == p

    def test_it_looks_for_the_release_weights_by_name(self, monkeypatch):
        monkeypatch.delenv(weights.RELEASE_ENV_VAR, raising=False)
        names = []
        monkeypatch.setattr(weights, "_candidates", lambda name=None: names.append(name) or iter(()))
        weights.release_path()
        assert names == [weights.RELEASE_NAME] == ["release1.pt"]


class TestReceivingPath:
    """The receiving panel's model: pinned by its own variable, else the
    bundled RECEIVING_NAME, else the stone model's file."""

    def test_unset_it_is_the_bundled_receiving_model(self, monkeypatch):
        monkeypatch.delenv(weights.RECEIVING_ENV_VAR, raising=False)
        got = weights.receiving_path()
        assert got.name == weights.RECEIVING_NAME == "overhead-5.pt"
        assert got.is_file()
        assert not weights.same_file(got, weights.default_path())

    def test_without_the_bundled_file_it_is_the_stone_model(self, monkeypatch):
        monkeypatch.delenv(weights.RECEIVING_ENV_VAR, raising=False)
        monkeypatch.setattr(weights, "RECEIVING_NAME", "no-such-model.pt")
        assert weights.receiving_path() == weights.default_path()

    def test_a_set_path_is_used(self, monkeypatch, tmp_path):
        p = tmp_path / "o4.pt"; p.write_bytes(b"x")
        monkeypatch.setenv(weights.RECEIVING_ENV_VAR, str(p))
        assert weights.receiving_path() == p

    def test_a_set_but_missing_path_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.RECEIVING_ENV_VAR, str(tmp_path / "nope.pt"))
        with pytest.raises(FileNotFoundError, match="nope.pt"):
            weights.receiving_path()

    def test_none_is_no_model_of_its_own(self, monkeypatch):
        monkeypatch.setenv(weights.RECEIVING_ENV_VAR, "none")
        assert weights.receiving_path() is None


class TestReceivingFor:
    """What the receiving panel reads with beside a run's stone weights."""

    def test_with_no_model_of_its_own_it_follows_the_stone_weights(
            self, monkeypatch, tmp_path):
        """A run pinned to other stone weights (an A/B) reads both panels
        with them, as it did before there were two models."""
        monkeypatch.delenv(weights.RECEIVING_ENV_VAR, raising=False)
        other = tmp_path / "other.pt"; other.write_bytes(b"x")
        assert weights.receiving_for(other) == other
        assert weights.receiving_for(None) is None

    def test_a_model_of_its_own_is_used_whatever_the_stones(self, monkeypatch, tmp_path):
        p = tmp_path / "o4.pt"; p.write_bytes(b"x")
        monkeypatch.setenv(weights.RECEIVING_ENV_VAR, str(p))
        assert weights.receiving_for(weights.default_path()) == p

    def test_none_follows_the_stone_weights(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.RECEIVING_ENV_VAR, "none")
        other = tmp_path / "other.pt"; other.write_bytes(b"x")
        assert weights.receiving_for(other) == other

    def test_the_default_stones_get_the_bundled_receiving_model(self, monkeypatch):
        # The production pair: overhead-3 throwing, overhead-5 receiving.
        monkeypatch.delenv(weights.RECEIVING_ENV_VAR, raising=False)
        monkeypatch.delenv(weights.ENV_VAR, raising=False)
        got = weights.receiving_for(weights.default_path())
        assert got.name == "overhead-5.pt"

    def test_an_ab_on_other_stones_reads_both_panels_with_them(self, monkeypatch, tmp_path):
        # The bundled receiving model exists, but a run pinned to other stone
        # weights is an A/B of those weights: one model for both panels.
        monkeypatch.delenv(weights.RECEIVING_ENV_VAR, raising=False)
        monkeypatch.delenv(weights.ENV_VAR, raising=False)
        other = tmp_path / "ds17b.pt"; other.write_bytes(b"x")
        assert weights.receiving_for(other) == other

    def test_a_pinned_model_that_is_missing_warns_and_reads_with_the_stones(
            self, monkeypatch, tmp_path, caplog):
        # Resolved where a worker starts: a typo must not take a lane down,
        # and the stone weights are still a trained model.
        monkeypatch.setenv(weights.RECEIVING_ENV_VAR, str(tmp_path / "typo.pt"))
        stones = weights.default_path()
        with caplog.at_level("WARNING"):
            assert weights.receiving_for(stones) == stones
        assert "typo.pt" in caplog.text


class TestSameFile:
    def test_one_file_by_two_spellings(self, tmp_path):
        p = tmp_path / "w.pt"; p.write_bytes(b"x")
        assert weights.same_file(p, str(tmp_path / "." / "w.pt"))

    def test_two_files(self, tmp_path):
        a, b = tmp_path / "a.pt", tmp_path / "b.pt"
        a.write_bytes(b"x"); b.write_bytes(b"x")
        assert not weights.same_file(a, b)

    def test_none_is_only_the_same_as_none(self, tmp_path):
        p = tmp_path / "w.pt"; p.write_bytes(b"x")
        assert weights.same_file(None, None)
        assert not weights.same_file(p, None) and not weights.same_file(None, p)
