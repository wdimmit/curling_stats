"""``scripts/mark_release.py`` is loaded by path, as ``test_mark_hog.py`` loads
its sibling. What it shows a person is what every mark in
``datasets/releasemarks`` was made through: the crop follows the rock, and past
the end of the delivery path it follows a guess, which these pin down.
"""

import http.client
import importlib.util
import json
import threading
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mark_release", Path(__file__).resolve().parents[1] / "scripts" / "mark_release.py")
mark_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mark_release)

TEE_Y = mark_release.TEE_Y


def path(speed=2.0, x_per_s=0.05, until=3.0, step=0.1):
    """[t, y, x] like ``line.delivery``: y house metres from the far tee."""
    n = int(round(until / step))
    return [[i * step, TEE_Y - speed * i * step, x_per_s * i * step] for i in range(n + 1)]


class TestTheCropFollowsTheRock:
    def test_on_the_path_it_interpolates(self):
        x, yp = mark_release.rock_at(path(), 1.05)
        assert yp == pytest.approx(2.1)
        assert x == pytest.approx(0.0525)

    def test_past_the_path_it_carries_on_at_the_last_speed(self):
        # A hand still on at hog +1.5 m has to stay in the crop for the
        # second after, or "still on at the end" could not be told from lost.
        x, yp = mark_release.rock_at(path(), 3.0 + 1.2)
        assert yp == pytest.approx(2.0 * 4.2)
        assert x == pytest.approx(0.05 * 4.2)


# The save: a real server on a free port, in a thread. Anyone who can reach
# the port could write marks.json, and so could a page on another site open
# in the marker's own browser.

IDS = ["e01r01", "e01r02"]
BEFORE = {"e01r01": 0.5}


@pytest.fixture
def served(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps([{"id": i, "frames": []} for i in IDS]))
    (tmp_path / "marks.json").write_text(json.dumps(BEFORE))
    srv = mark_release.make_server(tmp_path, "127.0.0.1", 0)
    # A short poll, or each shutdown waits out half a second.
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02},
                              daemon=True)
    thread.start()
    yield srv, tmp_path
    srv.shutdown()
    srv.server_close()
    thread.join(5)


def own_origin(srv):
    host, port = srv.server_address[:2]
    return f"http://{host}:{port}"


def post(srv, body, headers=None, path="/save"):
    """The status a POST of ``body`` gets."""
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("POST", path, body=body,
                     headers={"Content-Type": "application/json"} if headers is None else headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def marks(root):
    return json.loads((root / "marks.json").read_text())


class TestTheSave:
    def test_the_page_s_save_is_written_and_the_last_file_kept(self, served):
        srv, root = served
        new = {"e01r01": 0.5, "e01r02": "held"}
        status = post(srv, json.dumps(new), {"Content-Type": "application/json",
                                             "Origin": own_origin(srv)})
        assert status == 204
        assert marks(root) == new
        assert json.loads((root / "marks.json.bak").read_text()) == BEFORE
        # Written through a temporary file, which is gone.
        assert sorted(p.name for p in root.iterdir()) == [
            "index.json", "marks.json", "marks.json.bak"]

    def test_a_save_with_no_origin_is_written(self, served):
        # curl, or a script: no page sent it, so no other site's page did.
        srv, root = served
        assert post(srv, json.dumps({"e01r02": None})) == 204
        assert marks(root) == {"e01r02": None}

    def test_a_charset_on_the_type_is_fine(self, served):
        srv, root = served
        assert post(srv, "{}", {"Content-Type": "application/json; charset=utf-8"}) == 204
        assert marks(root) == {}

    def test_a_text_plain_post_is_refused(self, served):
        # What a page on another site can send without the browser asking first.
        srv, root = served
        assert post(srv, json.dumps({"e01r01": None}), {"Content-Type": "text/plain"}) == 415
        assert post(srv, json.dumps({"e01r01": None}), {}) == 415
        assert marks(root) == BEFORE

    def test_another_origin_is_refused(self, served):
        srv, root = served
        for origin in ("http://elsewhere.example", "null", own_origin(srv) + "0"):
            assert post(srv, "{}", {"Content-Type": "application/json", "Origin": origin}) == 403
        assert post(srv, "{}", {"Content-Type": "text/plain",
                                "Origin": "http://elsewhere.example"}) == 403
        assert marks(root) == BEFORE and not (root / "marks.json.bak").exists()

    def test_a_body_over_a_megabyte_is_refused_unread(self, served):
        srv, root = served
        host, port = srv.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.putrequest("POST", "/save")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(mark_release.MAX_SAVE_BYTES + 1))
            conn.endheaders()
            assert conn.getresponse().status == 413
        finally:
            conn.close()
        assert marks(root) == BEFORE

    @pytest.mark.parametrize("body", ["not json", "[]", '"held"', '{"e09r09": 0.5}',
                                      '{"e01r01": "off"}', '{"e01r01": true}',
                                      '{"e01r01": [0.5]}', '{"e01r01": NaN}',
                                      '{"e01r01": Infinity}'])
    def test_a_body_that_is_not_marks_is_refused(self, served, body):
        srv, root = served
        assert post(srv, body) == 400
        assert marks(root) == BEFORE

    def test_only_save_takes_a_post(self, served):
        srv, root = served
        assert post(srv, "{}", path="/marks.json") == 404
        assert marks(root) == BEFORE

    def test_the_page_sends_json_and_says_when_a_save_failed(self):
        assert "headers:{'Content-Type':'application/json'}" in mark_release.PAGE
        assert "if(!r.ok)" in mark_release.PAGE
