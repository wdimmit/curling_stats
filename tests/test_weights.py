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

    def test_can_be_asked_for_nothing_at_all(self, monkeypatch):
        monkeypatch.setenv("CURLING_SCORE_WEIGHTS", "none")
        assert weights.default_path() is None


class TestTheWorkerImageShipsBothDetectors:
    """A worker image built without the side model would reprocess an archive,
    succeed, and quietly republish the old answers -- because `side_path`
    treats a missing file as "use the colour scan", which is right in general
    and wrong for a deployment that asked for the model."""

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
