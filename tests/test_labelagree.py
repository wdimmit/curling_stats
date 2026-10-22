"""When two members' answers count as the same answer."""

from curling_score.train import labelagree as la


def box(x0, y0, x1, y1, color="red", start=None):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color, "start": start}


def answer(boxes, starts=None, cant_tell=False):
    if cant_tell:
        return {"cant_tell": True, "boxes": [], "starts": []}
    return {"cant_tell": False, "boxes": boxes, "starts": starts or []}


def drawn(*boxes):
    """An answer where the member drew every box themselves."""
    return answer(list(boxes))


def test_identical_boxes_agree_and_average():
    a = drawn(box(10, 10, 50, 50))
    b = drawn(box(11, 11, 51, 51))
    v = la.box_verdict(a, b)
    assert v["agree"] and v["why"] is None
    assert v["boxes"] == [{"x0": 10.5, "y0": 10.5, "x1": 50.5, "y1": 50.5, "color": "red"}]


def test_a_missing_box_is_unmatched():
    a = drawn(box(10, 10, 50, 50), box(100, 100, 140, 140))
    b = drawn(box(10, 10, 50, 50))
    v = la.box_verdict(a, b)
    assert not v["agree"] and v["why"] == "unmatched" and v["boxes"] is None


def test_same_place_other_colour_is_colour():
    a = drawn(box(10, 10, 50, 50, "red"))
    b = drawn(box(10, 10, 50, 50, "yellow"))
    v = la.box_verdict(a, b)
    assert not v["agree"] and v["why"] == "colour"


def test_iou_just_under_the_bar_disagrees():
    a = box(0, 0, 10, 10)
    b = box(2, 0, 12, 10)
    assert round(la.iou(a, b), 3) == 0.667
    v = la.box_verdict(drawn(a), drawn(b))
    assert not v["agree"] and v["why"] == "unmatched"


def test_matching_is_greedy_by_iou_and_one_to_one():
    a = [box(0, 0, 10, 10), box(1, 0, 11, 10)]
    b = [box(1, 0, 11, 10)]
    pairs, ua, ub = la.match_boxes(a, b)
    assert pairs == [(1, 0)] and ua == [0] and ub == []


def test_both_rubber_stamped_is_disputed_even_when_identical():
    a = answer([box(10, 10, 50, 50, start=0)], ["untouched"])
    b = answer([box(10, 10, 50, 50, start=0)], ["kept"])
    v = la.box_verdict(a, b)
    assert not v["agree"] and v["why"] == "rubber_stamp"


def test_one_member_changed_something_is_not_a_rubber_stamp():
    a = answer([box(10, 10, 50, 50, start=0)], ["untouched"])
    b = answer([box(11, 11, 51, 51, start=0)], ["changed"])
    assert la.box_verdict(a, b)["agree"]
    # A box the member drew themselves counts as a change too.
    c = answer([box(10, 10, 50, 50, start=0), box(100, 100, 140, 140)], ["kept"])
    d = answer([box(10, 10, 50, 50, start=0), box(100, 100, 140, 140)], ["kept"])
    assert la.box_verdict(c, d)["agree"]


def test_cant_tell_boxes_are_never_agreement():
    a = answer([], cant_tell=True)
    assert la.box_verdict(a, a) == {"agree": False, "why": "cant_tell", "boxes": None}
    b = drawn(box(10, 10, 50, 50))
    assert la.box_verdict(a, b)["why"] == "cant_tell"
    assert la.box_verdict(b, a)["why"] == "cant_tell"


def test_releases_within_three_frames_take_the_later():
    off = lambda f: {"mark": "off", "frame": f}
    v = la.release_verdict(off(40), off(43))
    assert v == {"agree": True, "why": None, "mark": "off", "frame": 43}
    assert la.release_verdict(off(43), off(40))["frame"] == 43
    v = la.release_verdict(off(40), off(44))
    assert not v["agree"] and v["why"] == "apart"


def test_held_and_off_is_mixed():
    v = la.release_verdict({"mark": "held", "frame": None}, {"mark": "off", "frame": 5})
    assert not v["agree"] and v["why"] == "mixed"


def test_both_held_agree():
    held = {"mark": "held", "frame": None}
    v = la.release_verdict(held, held)
    assert v == {"agree": True, "why": None, "mark": "held", "frame": None}


def test_cant_tell_is_never_agreement():
    ct = {"mark": "cant_tell", "frame": None}
    held = {"mark": "held", "frame": None}
    assert la.release_verdict(ct, ct)["why"] == "cant_tell"
    assert la.release_verdict(ct, held)["why"] == "cant_tell"
    assert la.release_verdict(held, ct)["agree"] is False


def test_reference_check():
    ref = {"mark": "off", "frame": 40}
    assert la.agrees_with_reference("release", {"mark": "off", "frame": 42}, ref)
    assert not la.agrees_with_reference("release", {"mark": "off", "frame": 45}, ref)
    assert not la.agrees_with_reference("release", {"mark": "held", "frame": None}, ref)
    assert not la.agrees_with_reference(
        "release", {"mark": "cant_tell", "frame": None}, ref)


def test_reference_check_boxes():
    ref = {"boxes": [{"x0": 10, "y0": 10, "x1": 50, "y1": 50, "color": "red"}]}
    # A rubber stamp is not checked against a reference: this one is right.
    stamped = answer([box(10, 10, 50, 50, start=0)], ["untouched"])
    assert la.agrees_with_reference("box", stamped, ref)
    assert not la.agrees_with_reference("box", drawn(box(10, 10, 50, 50, "yellow")), ref)
    assert not la.agrees_with_reference("box", drawn(box(60, 60, 90, 90)), ref)
    assert not la.agrees_with_reference("box", answer([], cant_tell=True), ref)
    assert not la.agrees_with_reference("box", answer([]), ref)
