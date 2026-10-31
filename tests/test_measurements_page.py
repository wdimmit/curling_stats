"""The Measurements page: there, linked from every page, and saying what the
code does. Each number the page states is read here from the constant it
describes, so refining a measurement without the page fails this test (the
user's rule, 2026-10-31: the page changes in lockstep with the measurements)."""
import re
from pathlib import Path

import pytest

from curling_score.game import aimlines, hogtime, linetime, spinfit, spintime, thinking
from curling_score.geometry import constants as C
from tests.test_service_api import world  # noqa: F401 - the app on in-memory fakes

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/curling_score/service/static/measurements.html"
FT, IN = 0.3048, 0.0254


def text():
    """The page's words, tags dropped and whitespace folded."""
    html = re.sub(r"<svg.*?</svg>", " ", PAGE.read_text(), flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def js_const(path, name):
    """A constant from a JS module, a number or a product of two (`1.5 * 0.0254`)."""
    m = re.search(rf"\b{name}\s*=\s*([0-9.]+)(?:\s*\*\s*([0-9.]+))?", (ROOT / path).read_text())
    return float(m.group(1)) * (float(m.group(2)) if m.group(2) else 1.0)


def stated():
    """(what, phrase the page must say), each phrase built from the code."""
    nine = round(linetime.NINE_FT_M / FT)
    assert linetime.FINAL_PAST_TEE_M[0] == C.TEE_TO_HOGLINE_M
    return [
        ("final window", f"first {nine} ft past the hog line"),
        ("release window", f"first {nine} ft past the release"),
        ("slide start", f"{round(-aimlines.SLIDE_FROM_M / FT)} ft behind the tee"),
        ("line error ceiling", f"more than {round(aimlines.SE_MAX_M / IN)} in"),
        ("set threshold", {2.0: "twice"}[aimlines.SET_SIGMAS]),
        ("on the broom, both pages", f"within {js_const('frontend/core/line.mjs', 'ON_M') / IN:g} in of the broom"),
        ("on the line at the hog line", f"means within {round(js_const('frontend/core/line.mjs', 'ON_M') / IN)} in"),
        ("hog crossing frame rate", f"{hogtime.READ_FPS:g} frames a second"),
        ("rotation start", f"{round((spintime.FROM_M - C.TEE_TO_HOGLINE_M) / FT)} ft past the hog line at the thrower's end"),
        ("rotation start, from the tee", f"{spintime.FROM_M:g} m from the tee"),
        ("rotation turns", {1.25: "first 1¼ turns"}[spinfit.EARLY_TURNS]),
        ("rotation range", {0.5: "under half a turn"}[spinfit.RANGE_BELOW_TURNS]),
        ("curl threshold", f"{round(linetime.CURL_MIN_M * 100)} cm"),
        ("thinking grace", f"{thinking.GRACE_S:g} seconds"),
    ]


@pytest.mark.parametrize("what, phrase", stated(), ids=[w for w, _p in stated()])
def test_the_page_states_what_the_code_does(what, phrase):
    assert phrase in text(), f"{what}: the page should say {phrase!r}"


def test_it_loads_without_a_script_and_links_the_other_pages(world):  # noqa: F811
    r = world["client"].get("/measurements")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<script" not in r.text
    assert 'href="/static/site.css"' in r.text
    assert 'href="/terms"' in r.text and 'href="/privacy"' in r.text


def test_every_page_links_it(world):  # noqa: F811
    c = world["client"]
    for path in ("/terms", "/privacy"):
        assert 'href="/measurements"' in c.get(path).text, path
    assert 'href="/measurements"' in (ROOT / "frontend/site/ui.jsx").read_text()
    assert '"/measurements"' in c.get("/static/site.js").text      # the built footer
