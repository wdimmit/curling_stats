"""``curling-score analyze``'s own flags -- not exercised anywhere else.

``--no-scoreboard`` already had no test either; these cover both ``analyze()``
skip flags at the CLI boundary, added alongside plumbing ``--no-longview``
through (FIX I4: ``analyze.analyze``'s ``skip_longview`` parameter had no
caller at all -- not the CLI, not the worker).
"""

from curling_score import cli


def _run(monkeypatch, tmp_path, extra_args=()):
    seen = {}

    def fake_analyze(url, **kw):
        seen.update(kw)
        return {"games": []}

    monkeypatch.setattr(cli.analyze_mod, "analyze", fake_analyze)
    monkeypatch.setattr(cli.analyze_mod, "write",
                        lambda doc, out: tmp_path / "timeline.json")
    rc = cli.main(["analyze", "https://example.com/watch?v=x",
                  "--out", str(tmp_path), *extra_args])
    return rc, seen


class TestNoLongviewFlag:
    def test_the_side_views_run_by_default(self, monkeypatch, tmp_path):
        rc, seen = _run(monkeypatch, tmp_path)
        assert rc == 0
        assert seen["skip_longview"] is False

    def test_no_longview_skips_them(self, monkeypatch, tmp_path):
        rc, seen = _run(monkeypatch, tmp_path, ["--no-longview"])
        assert rc == 0
        assert seen["skip_longview"] is True

    def test_no_scoreboard_still_reaches_analyze_alongside_it(self, monkeypatch, tmp_path):
        """The two skip flags are independent -- one must not imply the other."""
        rc, seen = _run(monkeypatch, tmp_path, ["--no-longview"])
        assert rc == 0
        assert seen["skip_scoreboard"] is False
        assert seen["skip_longview"] is True
