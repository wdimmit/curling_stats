"""Members' labelling over HTTP: who may call what, what a member is shown,
and that an answer we can't keep is refused with the reason.

The items are made the way the owner's script makes them: created through the
admin route, their images put where the route said, then opened with /ready.
"""

import pytest

from tests.test_service_api import ADMIN
from tests.test_service_auth import ALEX, MAKER, SARAH, w  # noqa: F401  (w is a fixture)

# One detected start, flagged, so the page has something a member must decide.
BOX_PAYLOAD = {"w": 640, "h": 360, "starts": [{"flagged": False}], "origin": "game-x"}
FULL_BOX = {"boxes": [{"x0": 10, "y0": 10, "x1": 60, "y1": 60, "color": "red", "start": 0}],
            "starts": ["kept"]}


def item(i, kind="box", **more):
    return {"id": f"b_{i}", "kind": kind, "batch": "t", "payload": BOX_PAYLOAD, **more}


def publish(w, n=2, kind="box", **more):
    """Creates, uploads through the memory store, readies."""
    items = [item(i, kind, **more) for i in range(n)]
    r = w["client"].post("/api/admin/label-items", json={"items": items}, headers=ADMIN)
    assert r.status_code == 200, r.text
    for it in r.json()["items"]:
        w["store"].put_bytes(f"labelling/{kind}/{it['id']}.jpg", b"jpg", "image/jpeg")
    w["client"].post("/api/admin/label-items/ready", json={"ids": [i["id"] for i in items]},
                     headers=ADMIN)


def answer(w, who, item_id, body=FULL_BOX, kind="box", seconds=4.0):
    return w["client"].post(f"/api/label/{kind}/{item_id}", headers=who,
                            json={"answer": body, "seconds": seconds})


def test_member_routes_need_sign_in(w):
    for m, p in [("get", "/api/label/box/next"), ("post", "/api/label/box/b_0"),
                 ("post", "/api/label/box/b_0/skip"), ("get", "/api/label/me")]:
        assert getattr(w["client"], m)(p).status_code == 401


def test_admin_routes_need_the_admin_token(w):
    c = w["client"]
    assert c.post("/api/admin/label-items", json={"items": []}, headers=SARAH).status_code == 401
    assert c.post("/api/admin/label-items/ready", json={"ids": []}, headers=SARAH).status_code == 401
    assert c.get("/api/admin/label-items?kind=box&state=done", headers=SARAH).status_code == 401
    assert c.get("/api/admin/label-items?kind=box&state=done").status_code == 401


def test_an_unknown_kind_is_404(w):
    assert w["client"].get("/api/label/nope/next", headers=SARAH).status_code == 404
    assert answer(w, SARAH, "b_0", kind="nope").status_code == 404


def test_create_hands_back_an_upload_and_stores_the_item_pending(w):
    r = w["client"].post("/api/admin/label-items", json={"items": [item(0)]}, headers=ADMIN)
    [got] = r.json()["items"]
    assert got["id"] == "b_0" and got["state"] == "pending"
    assert got["upload"]["url"].endswith("labelling/box/b_0.jpg")
    stored = w["repo"].get_label_item("b_0")
    assert stored.payload["image"] == "labelling/box/b_0.jpg"
    assert stored.state == "pending"


def test_create_refuses_a_bad_id_or_kind(w):
    for bad in [{**item(0), "id": "../x"}, {**item(0), "id": ""}, {**item(0), "kind": "nope"},
                {**item(0), "payload": "text"}, {**item(0), "id": 123},
                {**item(0), "id": ["b_0"]}, {**item(0), "id": None}]:
        r = w["client"].post("/api/admin/label-items", json={"items": [bad]}, headers=ADMIN)
        assert r.status_code == 422, bad


def test_ready_opens_only_items_whose_image_exists(w):
    items = [item(0), item(1)]
    w["client"].post("/api/admin/label-items", json={"items": items}, headers=ADMIN)
    w["store"].put_bytes("labelling/box/b_0.jpg", b"jpg", "image/jpeg")
    r = w["client"].post("/api/admin/label-items/ready", json={"ids": ["b_0", "b_1"]},
                         headers=ADMIN)
    assert r.json() == {"opened": ["b_0"], "missing_image": ["b_1"]}
    got = w["client"].get("/api/label/box/next?n=2", headers=SARAH).json()
    assert [i["id"] for i in got["items"]] == ["b_0"]


def test_two_members_label_an_item_and_counts_go_up(w):
    publish(w)
    first = w["client"].get("/api/label/box/next", headers=SARAH).json()
    assert [i["id"] for i in first["items"]] == ["b_0"] and first["count"] == 0
    assert first["items"][0]["image_url"].endswith("labelling/box/b_0.jpg")
    r = answer(w, SARAH, "b_0")
    assert r.json() == {"result": "added", "count": 1}
    assert w["client"].get("/api/label/me", headers=SARAH).json() == {"box": 1, "release": 0}
    # The half-done item goes to the next member before the untouched one.
    second = w["client"].get("/api/label/box/next", headers=ALEX).json()
    assert [i["id"] for i in second["items"]] == ["b_0"]
    # And Sarah is not offered what she has done.
    again = w["client"].get("/api/label/box/next", headers=SARAH).json()
    assert [i["id"] for i in again["items"]] == ["b_1"]


def test_resubmitting_replaces_and_does_not_count_twice(w):
    publish(w, 1)
    assert answer(w, SARAH, "b_0").json()["result"] == "added"
    r = answer(w, SARAH, "b_0", {"cant_tell": True})
    assert r.json() == {"result": "replaced", "count": 1}
    [a] = w["repo"].label_answers("b_0")
    assert a.answer["cant_tell"] is True and a.uid == "uid-sarah"


def test_a_late_submit_on_a_finished_item_is_409(w):
    publish(w, 1)
    assert answer(w, SARAH, "b_0").status_code == 200
    assert answer(w, ALEX, "b_0").status_code == 200
    r = answer(w, MAKER, "b_0")
    assert r.status_code == 409 and r.json()["detail"] == "full"


def test_an_item_held_by_someone_else_is_409_held(w):
    publish(w, 1)
    w["client"].get("/api/label/box/next", headers=SARAH)         # Sarah holds it
    r = answer(w, ALEX, "b_0")
    assert r.status_code == 409 and r.json()["detail"] == "held"


def test_a_pending_item_is_closed_to_answers(w):
    w["client"].post("/api/admin/label-items", json={"items": [item(0)]}, headers=ADMIN)
    r = answer(w, SARAH, "b_0")
    assert r.status_code == 409 and r.json()["detail"] == "closed"


def test_an_answer_to_nothing_or_the_wrong_kind_is_404(w):
    publish(w, 1)
    assert answer(w, SARAH, "nope").status_code == 404
    assert answer(w, SARAH, "b_0", kind="release").status_code == 404


def test_a_bad_answer_is_422_with_the_reason(w):
    publish(w, 1, reference=None)
    flagged = {**BOX_PAYLOAD, "starts": [{"flagged": True}]}
    w["repo"].update_label_item("b_0", payload={**flagged, "image": "labelling/box/b_0.jpg"})
    r = answer(w, SARAH, "b_0", {"boxes": FULL_BOX["boxes"], "starts": ["untouched"]})
    assert r.status_code == 422 and "flagged" in r.json()["detail"]
    assert w["repo"].label_answers("b_0") == []


def test_a_coordinate_too_big_for_a_float_is_422_not_500(w):
    publish(w, 1)
    huge = {"boxes": [{"x0": 10**400, "y0": 0, "x1": 5, "y1": 5, "color": "red", "start": 0}],
            "starts": ["kept"]}
    r = answer(w, SARAH, "b_0", huge)
    assert r.status_code == 422 and "out of range" in r.json()["detail"]
    # A huge `seconds` is only clamped, and must not overflow doing it.
    r = w["client"].post("/api/label/box/b_0", headers=SARAH,
                         json={"answer": FULL_BOX, "seconds": 10**400})
    assert r.status_code == 200
    assert w["repo"].label_answers("b_0")[0].seconds == 3600


def test_a_non_json_or_non_object_body_is_400(w):
    publish(w, 1)
    c = w["client"]
    assert c.post("/api/label/box/b_0", headers=SARAH, content=b"{NaN").status_code == 400
    assert c.post("/api/label/box/b_0", headers=SARAH, json=[1]).status_code == 400
    assert c.post("/api/label/box/b_0", headers=SARAH,
                  content=b'{"answer": {}, "seconds": NaN}').status_code == 400


def test_seconds_is_clamped(w):
    publish(w, 2)
    answer(w, SARAH, "b_0", seconds=99999)
    answer(w, SARAH, "b_1", seconds=-5)
    assert w["repo"].label_answers("b_0")[0].seconds == 3600
    assert w["repo"].label_answers("b_1")[0].seconds == 0


def test_a_member_never_sees_reference_or_others(w):
    publish(w, 1, reference={"boxes": [], "starts": ["kept"]})
    answer(w, SARAH, "b_0")
    text = w["client"].get("/api/label/box/next", headers=ALEX).text
    for secret in ("reference", "labelled_by", "uid-sarah", "origin", "game-x", "n_labels"):
        assert secret not in text


def test_skipping_ends_the_offer_and_a_missing_item_is_404(w):
    publish(w, 2)
    got = w["client"].get("/api/label/box/next", headers=SARAH).json()["items"][0]
    r = w["client"].post(f"/api/label/box/{got['id']}/skip", headers=SARAH)
    assert r.status_code == 200 and r.json() == {"ok": True}
    nxt = w["client"].get("/api/label/box/next", headers=SARAH).json()["items"][0]
    assert nxt["id"] != got["id"]
    assert w["client"].post("/api/label/box/nope/skip", headers=SARAH).status_code == 404


def test_next_takes_one_or_two_and_nothing_else(w):
    publish(w, 3)
    two = w["client"].get("/api/label/box/next?n=2", headers=SARAH).json()["items"]
    assert len(two) == 2
    assert w["client"].get("/api/label/box/next?n=3", headers=SARAH).status_code == 422
    assert w["client"].get("/api/label/box/next?n=0", headers=SARAH).status_code == 422


def test_create_is_idempotent(w):
    publish(w, 1)
    r = w["client"].post("/api/admin/label-items", json={"items": [item(0)]}, headers=ADMIN)
    [got] = r.json()["items"]
    assert got["upload"] is None and got["state"] == "open"
    assert w["repo"].get_label_item("b_0").state == "open"


def test_creating_a_pending_item_again_gives_a_fresh_upload_and_keeps_it(w):
    w["client"].post("/api/admin/label-items", json={"items": [item(0)]}, headers=ADMIN)
    other = {**item(0), "payload": {**BOX_PAYLOAD, "w": 1}}
    r = w["client"].post("/api/admin/label-items", json={"items": [other]}, headers=ADMIN)
    [got] = r.json()["items"]
    assert got["state"] == "pending" and got["upload"] is not None
    assert w["repo"].get_label_item("b_0").payload["w"] == 640


def test_admin_listing_returns_answers_and_pages(w):
    for i in range(3):
        publish_one = [item(i)]
        w["client"].post("/api/admin/label-items", json={"items": publish_one}, headers=ADMIN)
        w["store"].put_bytes(f"labelling/box/b_{i}.jpg", b"jpg", "image/jpeg")
        w["client"].post("/api/admin/label-items/ready", json={"ids": [f"b_{i}"]}, headers=ADMIN)
        w["clock"].advance(1)
    for who in (SARAH, ALEX):
        answer(w, who, "b_0")
    done = w["client"].get("/api/admin/label-items?kind=box&state=done", headers=ADMIN).json()
    [only] = done["items"]
    assert only["id"] == "b_0" and only["image_url"].endswith("labelling/box/b_0.jpg")
    assert sorted(a["uid"] for a in only["answers"]) == ["uid-alex", "uid-sarah"]
    assert only["answers"][0]["answer"]["starts"] == ["kept"]
    first = w["client"].get("/api/admin/label-items?kind=box&state=open&limit=1",
                            headers=ADMIN).json()
    assert [i["id"] for i in first["items"]] == ["b_1"] and first["after"]
    rest = w["client"].get(f"/api/admin/label-items?kind=box&state=open&limit=5"
                           f"&after={first['after']}".replace("+", "%2B"), headers=ADMIN).json()
    assert [i["id"] for i in rest["items"]] == ["b_2"]
    end = w["client"].get("/api/admin/label-items?kind=box&state=retired", headers=ADMIN).json()
    assert end == {"items": [], "after": None}


def test_admin_listing_refuses_bad_parameters(w):
    c = w["client"]
    assert c.get("/api/admin/label-items?kind=box&state=nope", headers=ADMIN).status_code == 422
    assert c.get("/api/admin/label-items?kind=nope&state=done", headers=ADMIN).status_code == 422
    assert c.get("/api/admin/label-items?kind=box&state=done&limit=501",
                 headers=ADMIN).status_code == 422
    assert c.get("/api/admin/label-items?kind=box&state=done&after=yesterday",
                 headers=ADMIN).status_code == 422


def test_the_label_page_is_served(w):
    assert 'data-page="label"' in w["client"].get("/label").text


def test_the_privacy_page_says_members_may_be_asked_to_label(w):
    text = w["client"].get("/privacy").text
    assert "volunteer to help" in text and "train the software" in text
