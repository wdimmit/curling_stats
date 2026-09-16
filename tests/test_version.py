"""The string that says what produced a timeline."""

from curling_score import version

class TestTheSideModelIsPartOfTheIdentity:
    """The side detector times every throwing-end hog crossing, and so every
    split. A timeline made with the colour scan must not be reused for one made
    with a trained detector -- quiet mixing is exactly what this string exists
    to prevent.
    """

    def test_a_side_model_changes_the_version(self, tmp_path):
        a = tmp_path / "over.pt"; a.write_bytes(b"overhead")
        b = tmp_path / "side.pt"; b.write_bytes(b"sideview")
        assert version.processing_version(a) != version.processing_version(a, b)

    def test_two_different_side_models_differ(self, tmp_path):
        a = tmp_path / "over.pt"; a.write_bytes(b"overhead")
        b = tmp_path / "s1.pt"; b.write_bytes(b"one")
        c = tmp_path / "s2.pt"; c.write_bytes(b"two")
        assert version.processing_version(a, b) != version.processing_version(a, c)

    def test_no_side_model_keeps_the_old_identity(self, tmp_path):
        """Timelines published before there was a side model must keep the
        identity they were published with, or every one of them looks stale."""
        a = tmp_path / "over.pt"; a.write_bytes(b"overhead")
        assert version.processing_version(a, None) == version.processing_version(a)
