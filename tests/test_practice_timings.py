"""A person's own sensors in their practice sessions: an app pushes each
throw's short (back line to hog) and long (hog to hog) times with a key made on
the site, and the session pairs them with the cameras' throws when it is read
(service/practice.py)."""

from datetime import timedelta

import pytest

from curling_score.service import practice
from curling_score.service.records import PracticeSession, SensorTiming, sensor_timing_id
from curling_score.service.repo import MemoryRepo
from tests.test_practice_api import (  # noqa: F401 - w is a fixture
    ALEX, SARAH, claim, doc, publish, start, throw, view, w,
)
from tests.test_service_api import ADMIN, T0

WALL0 = T0.timestamp()


def ms(seconds):
    """Epoch milliseconds, ``seconds`` after T0."""
    return int((WALL0 + seconds) * 1000)


def cam_throw(t_release, hog_after=3.0, far_after=17.0, **kw):
    th = throw(t_release, **kw)
    th["t_hog_s"] = None if hog_after is None else t_release + hog_after
    th["t_far_s"] = None if far_after is None else t_release + far_after
    return th


def timing(seconds, short_s=3.1, long_s=14.0, cid=None):
    return SensorTiming(id=cid or f"st_{seconds}", user_id="u", session_id="ps",
                        client_id=cid or str(seconds), hog_at=T0 + timedelta(seconds=seconds),
                        at=T0 + timedelta(seconds=seconds), at_is="hog",
                        received_at=T0, short_s=short_s, long_s=long_s)


def session(**kw):
    return PracticeSession(id="ps", user_id="u", sheet=3, watch_id="pw", started_at=T0, **kw)


NOW = T0 + timedelta(seconds=600)


class TestReading:
    def test_the_hog_crossing_from_whichever_time_the_app_sends(self):
        hog = practice.read_timing({"client_id": "a", "at": ms(63), "at_is": "hog",
                                    "short_s": 3.0, "long_s": 14.0})
        back = practice.read_timing({"client_id": "a", "at": ms(60), "at_is": "back",
                                     "short_s": 3.0, "long_s": 14.0})
        far = practice.read_timing({"client_id": "a", "at": ms(77), "at_is": "far",
                                    "short_s": 3.0, "long_s": 14.0})
        assert hog["hog_at"] == back["hog_at"] == far["hog_at"] == T0 + timedelta(seconds=63)

    def test_one_duration_is_enough_when_it_is_not_the_one_needed(self):
        got = practice.read_timing({"client_id": "a", "at": ms(63), "at_is": "hog",
                                    "long_s": 14.0})
        assert (got["short_s"], got["long_s"]) == (None, 14.0)

    @pytest.mark.parametrize("item, why", [
        ({"at": 1, "at_is": "hog", "long_s": 14.0}, "client_id"),
        ({"client_id": "a b", "at": 1, "at_is": "hog", "long_s": 14.0}, "client_id"),
        ({"client_id": "a", "at": "now", "at_is": "hog", "long_s": 14.0}, "epoch"),
        ({"client_id": "a", "at": True, "at_is": "hog", "long_s": 14.0}, "epoch"),
        ({"client_id": "a", "at": 1, "at_is": "tee", "long_s": 14.0}, "at_is"),
        ({"client_id": "a", "at": 1, "at_is": "hog"}, "short_s, long_s or both"),
        ({"client_id": "a", "at": 1, "at_is": "back", "long_s": 14.0}, "needs short_s"),
        ({"client_id": "a", "at": 1, "at_is": "far", "short_s": 3.0}, "needs long_s"),
        ({"client_id": "a", "at": 1, "at_is": "hog", "long_s": 45.0}, "6-30"),
        ({"client_id": "a", "at": 1, "at_is": "hog", "short_s": 0.4}, "1.5-8"),
        ({"client_id": "a", "at": 1, "at_is": "hog", "long_s": float("nan")}, "number"),
        ("a timing", "object"),
    ])
    def test_what_cannot_be_read_says_why(self, item, why):
        with pytest.raises(practice.BadTiming, match=why):
            practice.read_timing(item)


class TestMatching:
    def test_each_timing_to_the_throw_that_crossed_the_hog_then(self):
        d = doc([cam_throw(60.0), cam_throw(120.0)], WALL0)
        matched, unpaired = practice.match_timings(
            d, session(), [timing(63.5), timing(122.0)], NOW)
        assert {k: (t.id, gap) for k, (t, gap) in matched.items()} == {
            "t_60.0": ("st_63.5", 0.5), "t_120.0": ("st_122.0", -1.0)}
        assert unpaired == []

    def test_four_seconds_out_is_the_limit(self):
        d = doc([cam_throw(60.0)], WALL0)
        assert practice.match_timings(d, session(), [timing(67.0)], NOW)[0]
        matched, unpaired = practice.match_timings(d, session(), [timing(67.1)], NOW)
        assert matched == {} and [t.id for t in unpaired] == ["st_67.1"]

    def test_one_throw_to_one_timing_the_closest_first(self):
        d = doc([cam_throw(60.0)], WALL0)
        matched, unpaired = practice.match_timings(
            d, session(), [timing(65.0), timing(63.2)], NOW)
        assert matched["t_60.0"][0].id == "st_63.2"
        assert [t.id for t in unpaired] == ["st_65.0"]

    def test_without_a_hog_crossing_the_far_one_less_the_long_time(self):
        d = doc([cam_throw(60.0, hog_after=None, far_after=17.0)], WALL0)
        matched, _ = practice.match_timings(d, session(), [timing(63.0, long_s=14.0)], NOW)
        assert matched["t_60.0"][1] == 0.0
        # ...which needs the timing's long time.
        _, unpaired = practice.match_timings(d, session(), [timing(63.0, long_s=None)], NOW)
        assert len(unpaired) == 1

    def test_a_timing_waits_for_its_throw_to_be_published(self):
        timings = [timing(63.0)]
        assert practice.match_timings(doc([], WALL0), session(), timings, NOW)[1] == timings
        assert practice.match_timings(doc([cam_throw(60.0)], WALL0), session(), timings,
                                      NOW)[0]

    def test_a_hidden_throw_still_pairs(self):
        d = doc([cam_throw(60.0)], WALL0)
        matched, _ = practice.match_timings(d, session(hidden=["t_60.0"]), [timing(63.0)], NOW)
        assert "t_60.0" in matched

    def test_a_placed_stone_never_does(self):
        placed = {**cam_throw(60.0), "release_source": None}
        assert practice.match_timings(doc([placed], WALL0), session(), [timing(63.0)],
                                      NOW)[0] == {}

    def test_no_document_yet(self):
        assert practice.match_timings(None, session(), [timing(63.0)], NOW)[0] == {}


class TestKeeping:
    def repo(self, **kw):
        repo = MemoryRepo()
        repo.put_session(session(**kw))
        return repo

    def item(self, seconds, cid="c1", **kw):
        return {"client_id": cid, "at": ms(seconds), "at_is": "hog", "short_s": 3.0,
                "long_s": 14.0, **kw}

    def test_kept_on_the_session_running_then(self):
        repo = self.repo()
        got = practice.add_timing(repo, "u", self.item(63), NOW, key_id="ak_1")
        assert (got.session_id, got.key_id) == ("ps", "ak_1")
        assert got.id == sensor_timing_id("u", "c1")
        assert repo.timings_for_session("ps") == [got]

    def test_sent_twice_is_kept_once(self):
        repo = self.repo()
        first = practice.add_timing(repo, "u", self.item(63), NOW)
        again = practice.add_timing(repo, "u", self.item(63, long_s=15.0), NOW)
        assert again == first and len(repo.timings_for_session("ps")) == 1

    def test_two_peoples_apps_never_collide(self):
        assert sensor_timing_id("u", "c1") != sensor_timing_id("v", "c1")

    def test_up_to_two_minutes_after_stop(self):
        repo = self.repo(ended_at=T0 + timedelta(seconds=300), end_reason="stop")
        assert practice.add_timing(repo, "u", self.item(410), NOW).session_id == "ps"
        with pytest.raises(practice.NoSession):
            practice.add_timing(repo, "u", self.item(430, cid="c2"), NOW)

    def test_a_few_seconds_before_start_for_a_slow_clock(self):
        repo = self.repo()
        assert practice.add_timing(repo, "u", self.item(-4), NOW)
        with pytest.raises(practice.NoSession):
            practice.add_timing(repo, "u", self.item(-6, cid="c2"), NOW)

    def test_never_on_someone_elses_session(self):
        with pytest.raises(practice.NoSession):
            practice.add_timing(self.repo(), "v", self.item(63), NOW)

    def test_a_clock_far_ahead_is_refused(self):
        with pytest.raises(practice.BadTiming, match="future"):
            practice.add_timing(self.repo(), "u", self.item(600 + 61), NOW)


# ------------------------------------------------------------------ the API
KEYS = "/api/me/keys"
PUSH = "/api/practice/sensors"


def make_key(w, who=SARAH, name="Rock timer"):
    r = w["client"].post(KEYS, headers=who, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(key):
    return {"Authorization": f"Bearer {key['key']}"}


def push(w, key, *items):
    return w["client"].post(PUSH, headers=bearer(key), json={"timings": list(items)})


def at_hog(seconds, cid, short_s=3.2, long_s=13.9):
    return {"client_id": cid, "at": ms(seconds), "at_is": "hog", "short_s": short_s,
            "long_s": long_s}


class TestKeys:
    def test_shown_once_and_kept_only_as_a_hash(self, w):
        key = make_key(w)
        assert key["key"].startswith("csk_") and key["name"] == "Rock timer"
        (stored,) = w["repo"].api_keys_for_user("uid-sarah")
        assert key["key"] not in str(stored.to_dict())
        listed = w["client"].get(KEYS, headers=SARAH).json()["keys"]
        assert [k["id"] for k in listed] == [key["id"]] and "key" not in listed[0]

    def test_a_key_is_never_a_sign_in(self, w):
        key = make_key(w)
        assert w["client"].get(KEYS, headers=bearer(key)).status_code == 401
        assert w["client"].get("/api/me/practice", headers=bearer(key)).status_code == 401

    def test_revoked_is_refused(self, w):
        key = make_key(w)
        start(w)
        assert w["client"].delete(f"{KEYS}/{key['id']}", headers=SARAH).json() == {"ok": True}
        assert push(w, key, at_hog(63, "a")).status_code == 401
        assert w["client"].get(KEYS, headers=SARAH).json()["keys"] == []

    def test_only_the_owner_revokes(self, w):
        key = make_key(w)
        assert w["client"].delete(f"{KEYS}/{key['id']}", headers=ALEX).status_code == 404

    def test_ten_at_most(self, w):
        for i in range(10):
            make_key(w, name=f"k{i}")
        assert w["client"].post(KEYS, headers=SARAH, json={}).status_code == 409

    def test_an_unknown_or_missing_key(self, w):
        bogus = {"Authorization": "Bearer csk_nothing"}
        assert w["client"].post(PUSH, headers=bogus, json={"timings": []}).status_code == 401
        assert w["client"].post(PUSH, json={"timings": []}).status_code == 401
        assert w["client"].post(PUSH, headers=SARAH, json={"timings": []}).status_code == 401


class TestPushing:
    def test_the_whole_way_round(self, w):
        key = make_key(w)
        sid = start(w).json()["id"]
        job = claim(w)
        w["clock"].advance(200)
        got = push(w, key, at_hog(63.4, "a"), at_hog(150.0, "b"))
        assert got.status_code == 200, got.text
        assert [r["session_id"] for r in got.json()["timings"]] == [sid, sid]
        # The cameras saw the first; the second they have not published yet.
        publish(w, job, doc([cam_throw(60.0)], WALL0))
        v = view(w, sid).json()
        assert v["has_sensors"] is True
        (th,) = v["throws"]
        assert th["sensor"]["short_s"] == 3.2 and th["sensor"]["long_s"] == 13.9
        assert th["sensor"]["gap_s"] == 0.4
        (left,) = v["timings"]
        assert left["pending"] is True and left["hog_at"].startswith("2026-09-11T12:02:30")
        # A minute on, and still nothing seen: the cameras missed it.
        w["clock"].advance(60)
        assert view(w, sid).json()["timings"][0]["pending"] is False

    def test_a_session_with_no_timings_says_so(self, w):
        sid = start(w).json()["id"]
        v = view(w, sid).json()
        assert v["has_sensors"] is False and v["timings"] == []

    def test_each_timing_answered_on_its_own(self, w):
        key = make_key(w)
        start(w)
        w["clock"].advance(100)
        got = push(w, key, at_hog(63, "a"), {"client_id": "b", "at": ms(70), "at_is": "far",
                                             "short_s": 3.0}).json()["timings"]
        assert "id" in got[0] and got[1] == {"client_id": "b", "error": "at_is far needs long_s"}

    def test_no_session_running_is_a_409(self, w):
        key = make_key(w)
        w["clock"].advance(100)
        got = push(w, key, at_hog(63, "a"))
        assert got.status_code == 409
        assert "no practice session" in got.json()["timings"][0]["error"]

    def test_a_retry_lands_once(self, w):
        key = make_key(w)
        sid = start(w).json()["id"]
        w["clock"].advance(100)
        first = push(w, key, at_hog(63, "a")).json()["timings"][0]["id"]
        assert push(w, key, at_hog(63, "a")).json()["timings"][0]["id"] == first
        assert len(w["repo"].timings_for_session(sid)) == 1

    def test_never_into_anyone_elses_session(self, w):
        key = make_key(w, who=ALEX)
        start(w)                                   # Sarah's
        w["clock"].advance(100)
        assert push(w, key, at_hog(63, "a")).status_code == 409

    @pytest.mark.parametrize("body", [{}, {"timings": []}, {"timings": "x"}, [1]])
    def test_a_push_says_what_it_is(self, w, body):
        key = make_key(w)
        assert w["client"].post(PUSH, headers=bearer(key), json=body).status_code == 422

    def test_fifty_at_most(self, w):
        key = make_key(w)
        items = [at_hog(63, f"c{i}") for i in range(51)]
        assert push(w, key, *items).status_code == 422

    def test_the_admin_sees_the_pairs(self, w):
        key = make_key(w)
        sid = start(w).json()["id"]
        job = claim(w)
        w["clock"].advance(200)
        push(w, key, at_hog(63.4, "a"), at_hog(150.0, "b"))
        publish(w, job, doc([cam_throw(60.0)], WALL0))
        c = w["client"]
        assert c.get(f"/api/admin/practice/timings?session={sid}", headers=SARAH).status_code == 401
        (s,) = c.get(f"/api/admin/practice/timings?session={sid}", headers=ADMIN).json()["sessions"]
        (pair,) = s["pairs"]
        assert pair["throw"]["split_s"] == 13.8 and pair["timing"]["long_s"] == 13.9
        assert pair["gap_s"] == 0.4 and len(s["unpaired"]) == 1
        by_user = c.get("/api/admin/practice/timings?user=uid-sarah", headers=ADMIN).json()
        assert [x["session"]["id"] for x in by_user["sessions"]] == [sid]


def test_timings_and_keys_are_backed_up():
    from curling_score.service.records import ApiKey

    repo = MemoryRepo()
    repo.put_timing(timing(63.0))
    repo.put_api_key(ApiKey(id="ak_1", user_id="u", name="k", sha256="h", created_at=T0))
    other = MemoryRepo()
    other.import_all(repo.export_all())
    assert other.timings_for_session("ps")[0].long_s == 14.0
    assert other.api_key_by_hash("h").id == "ak_1"


def test_the_privacy_page_says_what_an_app_sends(w):
    text = " ".join(w["client"].get("/privacy").text.split())
    assert "Sensor timings and app keys:" in text
    assert "kept with the session they were sent into" in text


def test_the_validation_script_reads_the_pairs(w):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/practice/sensor_pairs.py"
    spec = importlib.util.spec_from_file_location("sensor_pairs", path)
    sp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sp)
    key = make_key(w)
    start(w)
    job = claim(w)
    w["clock"].advance(200)
    push(w, key, at_hog(63.4, "a"), at_hog(122.8, "b", long_s=14.3))
    publish(w, job, doc([cam_throw(60.0), cam_throw(120.0)], WALL0))
    got = w["client"].get("/api/admin/practice/timings?user=uid-sarah", headers=ADMIN).json()
    rs = sp.rows(got["sessions"])
    assert [(r["throw"], round(r["split_diff"], 2), r["release_to_hog"]) for r in rs] == [
        ("t_60.0", -0.1, 3.0), ("t_120.0", -0.5, 3.0)]
    s = sp.summary(rs)
    assert (s["pairs"], round(s["median_diff"], 2), round(s["p90_abs_diff"], 2)) == (2, -0.3, 0.5)
    assert round(s["median_gap"], 2) == 0.1             # +0.4 and -0.2
