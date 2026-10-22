from datetime import datetime, timedelta, timezone

import pytest

from curling_score.service.labelling import (HOLD_S, check_answer, for_member,
                                             image_key, next_items)
from curling_score.service.records import LabelItem
from curling_score.service.repo import MemoryRepo

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def box_item(**kw):  # 100x50 image, start 0 flagged, start 1 not
    payload = {"image": "k", "w": 100, "h": 50, "starts": [
        {"x0": 10, "y0": 10, "x1": 20, "y1": 18, "color": "red", "conf": 0.4, "flagged": True},
        {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "conf": 0.9, "flagged": False}],
        "origin": {"video_id": "v"}}
    kw.setdefault("id", "b_1")
    kw.setdefault("created_at", T0)
    kw.setdefault("state", "open")
    return LabelItem(kind="box", batch="t", payload=payload,
                     reference={"boxes": []}, **kw)


def release_item():
    return LabelItem(id="r_1", kind="release", created_at=T0, batch="t", state="open",
                     payload={"image": "k", "cols": 4, "fw": 10, "fh": 10, "n": 8,
                              "fps": 30.0, "frames": [], "origin": {}})


def two_boxes(**over):
    a = {"x0": 10, "y0": 10, "x1": 20, "y1": 18, "color": "red", "start": 0}
    b = {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "start": 1}
    return {"cant_tell": False, "boxes": [{**a, **over.get("a", {})}, b],
            "starts": over.get("starts", ["kept", "untouched"])}


def test_the_image_key_names_the_kind():
    assert image_key("b_1", "box") == "labelling/box/b_1.jpg"
    assert image_key("r_1", "release") == "labelling/release/r_1.jpg"


def test_the_flagged_box_must_be_acted_on():
    with pytest.raises(ValueError, match="flagged"):
        check_answer(box_item(), {"cant_tell": False, "boxes": [
            {"x0": 10, "y0": 10, "x1": 20, "y1": 18, "color": "red", "start": 0},
            {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "start": 1}],
            "starts": ["untouched", "untouched"]})


def test_a_deleted_start_must_be_gone_and_a_kept_one_present():
    ok = {"cant_tell": False, "starts": ["deleted", "untouched"], "boxes": [
        {"x0": 50, "y0": 10, "x1": 60, "y1": 18, "color": "yellow", "start": 1}]}
    assert check_answer(box_item(), ok)["starts"] == ["deleted", "untouched"]
    bad = {**ok, "boxes": ok["boxes"] + [{"x0": 10, "y0": 10, "x1": 20, "y1": 18,
                                          "color": "red", "start": 0}]}
    with pytest.raises(ValueError):
        check_answer(box_item(), bad)
    missing = {**ok, "starts": ["kept", "untouched"]}
    with pytest.raises(ValueError):
        check_answer(box_item(), missing)


def test_a_start_cannot_be_boxed_twice_and_a_new_box_has_no_start():
    twice = two_boxes()
    twice["boxes"].append({"x0": 30, "y0": 20, "x1": 40, "y1": 30, "color": "red", "start": 1})
    with pytest.raises(ValueError):
        check_answer(box_item(), twice)
    extra = two_boxes()
    extra["boxes"].append({"x0": 30, "y0": 20, "x1": 40, "y1": 30, "color": "red", "start": None})
    assert len(check_answer(box_item(), extra)["boxes"]) == 3
    extra["boxes"][2]["start"] = 7
    with pytest.raises(ValueError):
        check_answer(box_item(), extra)


def test_the_statuses_must_match_the_starts():
    with pytest.raises(ValueError):
        check_answer(box_item(), two_boxes(starts=["kept"]))
    with pytest.raises(ValueError):
        check_answer(box_item(), two_boxes(starts=["kept", "fine"]))


def test_boxes_outside_the_image_or_tiny_are_refused():
    with pytest.raises(ValueError):
        check_answer(box_item(), two_boxes(a={"x1": 140}))
    with pytest.raises(ValueError):
        check_answer(box_item(), two_boxes(a={"x1": 12}))      # 2 px wide
    with pytest.raises(ValueError):
        check_answer(box_item(), two_boxes(a={"color": "blue"}))
    with pytest.raises(ValueError):
        check_answer(box_item(), two_boxes(a={"y0": 20, "y1": 15}))


def test_absurd_coordinates_are_a_value_error_not_an_overflow():
    for big in (10**400, -10**400, float("inf"), float("nan"), 1e300):
        with pytest.raises(ValueError):
            check_answer(box_item(), two_boxes(a={"x1": big}))


def test_a_pixel_over_the_edge_is_clamped():
    got = check_answer(box_item(), two_boxes(a={"x0": -1, "x1": 101}))
    assert got["boxes"][0]["x0"] == 0 and got["boxes"][0]["x1"] == 100


def test_too_many_boxes_are_refused():
    ans = two_boxes()
    ans["boxes"] += [{"x0": 1, "y0": 30, "x1": 9, "y1": 40, "color": "red", "start": None}
                     for _ in range(60)]
    with pytest.raises(ValueError, match="boxes"):
        check_answer(box_item(), ans)


def test_cant_tell_needs_nothing_else():
    got = check_answer(box_item(), {"cant_tell": True, "boxes": [{"junk": 1}], "starts": ["x"]})
    assert got == {"cant_tell": True, "boxes": [], "starts": []}


def test_release_answers():
    item = release_item()
    assert check_answer(item, {"mark": "off", "frame": 3}) == {"mark": "off", "frame": 3}
    assert check_answer(item, {"mark": "held", "frame": None}) == {"mark": "held", "frame": None}
    assert check_answer(item, {"mark": "cant_tell", "frame": None})["mark"] == "cant_tell"
    for bad in ({"mark": "off", "frame": None}, {"mark": "off", "frame": 8},
                {"mark": "off", "frame": -1}, {"mark": "off", "frame": True},
                {"mark": "held", "frame": 2}, {"mark": "maybe", "frame": None}):
        with pytest.raises(ValueError):
            check_answer(item, bad)


def test_for_member_hides_reference_origin_and_uids():
    got = for_member(box_item(labelled_by=["u1"]), "https://img")
    assert got["image_url"] == "https://img" and "reference" not in got
    assert "origin" not in got and "labelled_by" not in got and len(got["starts"]) == 2
    assert "image" not in got and got["id"] == "b_1" and got["kind"] == "box"
    assert set(got) == {"id", "kind", "image_url", "w", "h", "starts"}


@pytest.fixture
def repo():
    return MemoryRepo()


def put(repo, n, n_labels=0, by=(), **kw):
    item = box_item(id=f"b_{n}", created_at=T0 + timedelta(minutes=n),
                    n_labels=n_labels, labelled_by=list(by), **kw)
    repo.put_label_item(item)
    return item


def ids(items):
    return [i.id for i in items]


def test_next_items_finishes_half_done_first_and_never_repeats(repo):
    put(repo, 1)
    put(repo, 2)
    put(repo, 3, n_labels=1, by=["u1"])
    assert ids(next_items(repo, "box", "u2", T0)) == ["b_3"]
    assert ids(next_items(repo, "box", "u1", T0, n=2)) == ["b_1", "b_2"]
    # asking again within the hold gets the held ones back, the first first
    assert ids(next_items(repo, "box", "u1", T0 + timedelta(seconds=30), n=1)) == ["b_1"]
    # u2 holds b_3 and u1 holds b_1, b_2: nothing is left for a third member but their own
    assert ids(next_items(repo, "box", "u3", T0 + timedelta(seconds=30))) == []
    # once the holds lapse they are free again
    late = T0 + timedelta(seconds=HOLD_S + 60)
    assert ids(next_items(repo, "box", "u3", late, n=3)) == ["b_3", "b_1", "b_2"]


def test_next_items_puts_your_own_hold_ahead_of_half_done_items(repo):
    put(repo, 1)
    put(repo, 2, n_labels=1, by=["u2"])
    assert ids(next_items(repo, "box", "u1", T0)) == ["b_2"]
    repo.update_label_item("b_2", hold_uid=None, hold_until=None)
    assert ids(next_items(repo, "box", "u3", T0)) == ["b_2"]
    repo.update_label_item("b_2", hold_uid=None, hold_until=None)
    # u1 holds b_1 (n=0) by hand; a half-done b_2 is free but their hold comes first
    repo.hold_label_item("b_1", "u1", T0, HOLD_S)
    assert ids(next_items(repo, "box", "u1", T0)) == ["b_1"]


def test_next_items_skips_what_you_labelled_or_skipped_and_pages_on(repo):
    for n in range(1, 4):
        put(repo, n)
    repo.update_label_item("b_1", skipped_by=["u1"])
    repo.update_label_item("b_2", n_labels=1, labelled_by=["u1"])
    assert ids(next_items(repo, "box", "u1", T0, n=5)) == ["b_3"]


def test_next_items_pages_past_the_first_page(repo, monkeypatch):
    from curling_score.service import labelling
    monkeypatch.setattr(labelling, "PAGE", 2)
    for n in range(1, 6):
        put(repo, n)
    assert ids(next_items(repo, "box", "u1", T0, n=4)) == ["b_1", "b_2", "b_3", "b_4"]


def test_next_items_ignores_other_kinds_and_closed_items(repo):
    put(repo, 1, state="pending")
    put(repo, 2, state="done")
    assert next_items(repo, "box", "u1", T0) == []
    assert next_items(repo, "release", "u1", T0) == []
