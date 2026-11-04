"""The string that says what produced a timeline."""

from curling_score import version

class TestTheSideModelIsPartOfTheIdentity:
    """The side detector times every throwing-end hog crossing, and so every
    split. A timeline made with one side model, or none, must not be reused
    for one made with another -- quiet mixing is exactly what this string
    exists to prevent.
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


class TestTheBroomModelIsPartOfTheIdentity:
    """The broom model adds a field to every shot. A timeline made without it
    must not pass for one made with it -- and every timeline made before it
    existed keeps the identity it was published with."""

    def test_a_broom_model_changes_the_version(self, tmp_path):
        a = tmp_path / "over.pt"; a.write_bytes(b"overhead")
        s = tmp_path / "side.pt"; s.write_bytes(b"sideview")
        b = tmp_path / "broom1.pt"; b.write_bytes(b"broom")
        with_b = version.processing_version(a, s, b)
        assert with_b.startswith(version.processing_version(a, s))
        assert "+broom-broom1-" in with_b

    def test_no_broom_model_keeps_the_old_identity(self, tmp_path):
        a = tmp_path / "over.pt"; a.write_bytes(b"overhead")
        s = tmp_path / "side.pt"; s.write_bytes(b"sideview")
        assert version.processing_version(a, s, None) == version.processing_version(a, s)


def test_the_line_pass_is_named_when_it_ran(tmp_path):
    from curling_score import version
    w, side, broom = (tmp_path / n for n in ("w.pt", "side.pt", "broom.pt"))
    for i, f in enumerate((w, side, broom)):
        f.write_bytes(bytes([i]))           # model_id hashes the file
    assert version.processing_version(w, side, broom, line=True).endswith("+line")
    assert not version.processing_version(w, side, broom).endswith("+line")
    assert not version.processing_version(w, None, None, line=True).endswith("+line")


class TestTheSpinModelIsPartOfTheIdentity:
    """The handle-point model gives every shot its spin: a new one changes
    every rock's rpm, so it is named -- with the line, whose side model the
    spin pass reads the rock's boxes with, and only then."""

    def files(self, tmp_path):
        out = [tmp_path / n for n in ("w.pt", "side.pt", "broom.pt", "spin1.pt")]
        for i, f in enumerate(out):
            f.write_bytes(bytes([i]))
        return out

    def test_a_spin_model_is_named_after_the_line(self, tmp_path):
        w, side, broom, spin = self.files(tmp_path)
        got = version.processing_version(w, side, broom, line=True, spin_weights=spin)
        assert got.startswith(version.processing_version(w, side, broom, line=True) + "+spin-spin1-")

    def test_without_the_line_it_is_not_named(self, tmp_path):
        w, side, broom, spin = self.files(tmp_path)
        assert version.processing_version(w, side, broom, spin_weights=spin) == \
            version.processing_version(w, side, broom)

    def test_no_spin_model_keeps_the_identity(self, tmp_path):
        w, side, broom, _spin = self.files(tmp_path)
        assert version.processing_version(w, side, broom, line=True, spin_weights=None) == \
            version.processing_version(w, side, broom, line=True)


class TestTheReleaseModelIsPartOfTheIdentity:
    """The release model puts the hand's release on every rock's delivery
    path: a new one moves those points, so it is named -- after the spin, and
    only with the line, whose delivery path it reads along."""

    def files(self, tmp_path):
        out = [tmp_path / n for n in ("w.pt", "side.pt", "broom.pt", "spin1.pt", "release1.pt")]
        for i, f in enumerate(out):
            f.write_bytes(bytes([i]))
        return out

    def test_a_release_model_is_named_after_the_spin(self, tmp_path):
        w, side, broom, spin, rel = self.files(tmp_path)
        got = version.processing_version(w, side, broom, line=True, spin_weights=spin,
                                         release_weights=rel)
        assert got.endswith(f"+spin-{version.model_id(spin)}+release-{version.model_id(rel)}")
        assert got.startswith(version.processing_version(w, side, broom, line=True,
                                                         spin_weights=spin))

    def test_without_the_line_it_is_not_named(self, tmp_path):
        w, side, broom, spin, rel = self.files(tmp_path)
        assert version.processing_version(w, side, broom, spin_weights=spin,
                                          release_weights=rel) == \
            version.processing_version(w, side, broom)

    def test_no_release_model_keeps_the_identity(self, tmp_path):
        w, side, broom, spin, _rel = self.files(tmp_path)
        assert version.processing_version(w, side, broom, line=True, spin_weights=spin,
                                          release_weights=None) == \
            version.processing_version(w, side, broom, line=True, spin_weights=spin)
