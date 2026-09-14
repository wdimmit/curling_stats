"""The committed bundle, and the two invariants nothing else would notice.

`src/curling_score/viewer/app.js` is generated from `frontend/` and tracked
anyway, because `curling-score serve` is the first command in the README and
has to work after a plain `pip install`, with no node anywhere. The cost of
that decision is that the artifact can go stale without anything failing --
so this is what fails.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

from curling_score import viewer

ROOT = Path(__file__).resolve().parents[1]
STAMP = ROOT / "frontend/.buildstamp.json"

pytestmark = pytest.mark.skipif(not STAMP.is_file(),
                                reason="frontend has not been built here")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestTheBundleMatchesItsSources:
    """Needs neither node nor node_modules, so it runs wherever pytest does --
    which is the point. A check that rebuilds and compares would skip on
    exactly the machines where a stale artifact would ship."""

    def test_every_source_is_the_one_that_was_built_from(self):
        stamp = json.loads(STAMP.read_text())
        stale = [p for p, want in stamp["sources"].items()
                 if not (ROOT / p).is_file() or sha256(ROOT / p) != want]
        assert not stale, (
            f"these changed since the last build: {stale}. Run `npm run build` "
            f"in frontend/ -- always through npm, never esbuild directly, or "
            f"the stamp is not rewritten.")

    def test_the_bundle_is_the_one_that_was_produced(self):
        stamp = json.loads(STAMP.read_text())
        for path, want in stamp["outputs"].items():
            assert (ROOT / path).is_file(), f"{path} is missing"
            assert sha256(ROOT / path) == want, (
                f"{path} was edited by hand, or built some other way. It is "
                f"generated: change frontend/ and rebuild.")


class TestTheSourcesLint:
    """`npm run build` lints before it bundles, so a clean tree should stay
    clean. Skipped where node_modules is absent, like every other node test
    here -- this is a convenience for whoever has the toolchain, not a gate
    for whoever does not."""

    def test_eslint_is_happy(self):
        import subprocess
        eslint = ROOT / "frontend/node_modules/.bin/eslint"
        if not eslint.is_file():
            pytest.skip("frontend dependencies are not installed here")
        done = subprocess.run([str(eslint), "."], cwd=ROOT / "frontend",
                              capture_output=True, text=True, timeout=180)
        assert done.returncode == 0, done.stdout or done.stderr


class TestItStillWorksWithNoNetwork:
    """`curling-score serve` is the README's first command, and auth.js says
    plainly that the viewer stays free of Firebase so the page a person spends
    an hour in keeps working offline. Nothing else in the suite would notice a
    font link or a CDN import being added, and the failure would be a page
    that half-loads at a rink."""

    ALLOWED = {"www.youtube.com", "youtu.be", "www.w3.org", "react.dev"}

    def test_the_viewer_reaches_for_nothing_but_youtube(self):
        # The sources, not the bundle: React's production build carries
        # https://react.dev/errors/ strings that mean nothing at runtime.
        hosts = set()
        for path in [*(ROOT / "frontend/core").glob("*.mjs"),
                     *(ROOT / "frontend/runtime").glob("*.mjs"),
                     *(ROOT / "frontend/viewer").glob("*.jsx"),
                     ROOT / "src/curling_score/viewer/index.html",
                     ROOT / "src/curling_score/viewer/style.css"]:
            hosts |= set(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text()))
        assert hosts <= self.ALLOWED, (
            f"the viewer would fetch from {sorted(hosts - self.ALLOWED)}, which "
            f"breaks it offline under `curling-score serve`")


class TestTheBootAnchor:
    """The one substitution in this system whose failure is silent."""

    def test_a_page_is_injected_with_its_config(self):
        page = viewer.boot_page({"mode": "view", "slug": "abc"})
        assert '<script>window.CHART={"mode": "view", "slug": "abc"};</script>' in page
        assert viewer.BOOT_ANCHOR not in page

    def test_the_local_server_gets_no_config_at_all(self):
        assert "<script>window.CHART=" not in viewer.boot_page()

    def test_a_missing_anchor_raises_rather_than_shipping_an_editable_page(self, tmp_path, monkeypatch):
        shell = tmp_path / "index.html"
        shell.write_text("<!doctype html>\n<div id=root></div>\n")
        monkeypatch.setattr(viewer, "HERE", tmp_path)
        with pytest.raises(RuntimeError, match="window.CHART"):
            viewer.boot_page({"mode": "view"})
