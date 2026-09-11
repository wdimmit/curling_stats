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
