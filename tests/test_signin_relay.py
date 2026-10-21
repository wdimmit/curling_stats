"""The relay: an emailed link opened in the wrong browser, held for the right one.

Opening the link elsewhere gives a code. Typing that code where the sign-in
started gives the link back once. Nothing here signs anyone in: the link is
useless without the address it was sent to, and Firebase checks that.
"""
from urllib.parse import urlencode

import pytest

from curling_score.service import signin
from curling_score.service.auth import FakeVerifier, NoAuth
from tests.test_service_auth import _world

RID = "AbCdEfGhIjKlMnOpQrStUv"


def link(rid=RID, base="https://chart.example/signin", **q):
    params = {"r": rid, "apiKey": "AIzaFake", "oobCode": "oob-1", "mode": "signIn",
              "lang": "en", **q}
    return f"{base}?{urlencode(params)}"


@pytest.fixture
def w():
    return _world(FakeVerifier())


def relay(w, lnk=..., ip="1.2.3.4"):
    return w["client"].post("/api/signin/relay", json={"link": link() if lnk is ... else lnk},
                            headers={"X-Forwarded-For": ip})


def claim(w, code, rid=RID, ip="1.2.3.4"):
    return w["client"].post(f"/api/signin/relay/{rid}/claim", json={"code": code},
                            headers={"X-Forwarded-For": ip})


class TestRelay:
    def test_a_link_opened_elsewhere_gets_a_six_digit_code(self, w):
        r = relay(w)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert len(r.json()["code"]) == 6 and r.json()["code"].isdigit()
        assert r.json()["expires_at"] == "2026-09-11T12:10:00+00:00"

    def test_the_same_link_again_gets_the_same_code(self, w):
        assert relay(w).json() == relay(w).json()

    def test_a_newer_link_for_the_same_request_replaces_it(self, w):
        relay(w)
        second = relay(w, link(oobCode="oob-2")).json()["code"]
        assert claim(w, second).json() == {"link": link(oobCode="oob-2")}

    def test_the_code_gives_the_link_back_once(self, w):
        code = relay(w).json()["code"]
        r = claim(w, code)
        assert r.status_code == 200 and r.json() == {"link": link()}
        assert claim(w, code).status_code == 410

    def test_four_wrong_codes_are_404_and_the_fifth_is_410(self, w):
        code = relay(w).json()["code"]
        wrong = "000000" if code != "000000" else "111111"
        assert [claim(w, wrong).status_code for _ in range(5)] == [404] * 4 + [410]
        assert claim(w, code).status_code == 410

    def test_a_code_runs_out_after_ten_minutes(self, w):
        code = relay(w).json()["code"]
        w["clock"].advance(600)
        assert claim(w, code).status_code == 410

    @pytest.mark.parametrize("bad", [
        link(base="https://evil.example/signin"), link(base="http://chart.example/signin"),
        link(base="https://chart.example/mine"), link(mode="resetPassword"),
        link(oobCode=""), link(apiKey="AIzaOther"), link(rid="short"),
        link(lang="x" * 2100), "not a url", 5, None,
        link(link="https://evil.example/x?apiKey=AIzaOther&oobCode=ATTACKER&mode=signIn"),
        link(deep_link_id="https://evil.example/x?apiKey=AIzaOther&oobCode=ATTACKER&mode=signIn"),
    ])
    def test_only_our_own_sign_in_links_are_held(self, w, bad):
        assert relay(w, bad).status_code == 422

    def test_a_link_with_two_oob_codes_is_refused(self, w):
        assert relay(w, link() + "&oobCode=oob-x").status_code == 422

    def test_a_lone_surrogate_is_refused_not_a_500(self, w):
        r = w["client"].post("/api/signin/relay", content=b'{"link": "\\ud800"}',
                             headers={"Content-Type": "application/json",
                                      "X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 422

    def test_with_no_base_url_nothing_is_held(self):
        relative = f"/signin?r={RID}&apiKey=AIzaFake&oobCode=oob-1&mode=signIn"
        assert signin.relay_id_of(relative, "", "AIzaFake") is None
        assert signin.relay_id_of(link(), "", "AIzaFake") is None
        assert signin.relay_id_of(link(), "chart.example", "AIzaFake") is None

    def test_a_claim_for_a_malformed_request_id_is_refused(self, w):
        assert claim(w, "123456", rid="short").status_code == 422

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "", None])
    def test_a_code_is_six_digits(self, w, code):
        assert claim(w, code).status_code == 422

    def test_making_codes_is_limited_per_address(self, w):
        codes = [relay(w, link(rid=f"{i:022d}")).status_code for i in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429

    def test_trying_codes_is_limited_per_address(self, w):
        statuses = [claim(w, "000000", rid=f"{i:022d}").status_code for i in range(61)]
        assert statuses[60] == 429

    def test_with_accounts_off_there_is_no_relay(self):
        off = _world(NoAuth())
        assert relay(off).status_code == 503
        assert claim(off, "123456").status_code == 503
