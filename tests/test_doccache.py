"""The API's timeline cache keeps bytes, one version a key, within a budget."""

import json

import pytest

from curling_score.service.doccache import DocCache


class Store:
    def __init__(self, **docs):
        self.docs = {k: json.dumps(v).encode() for k, v in docs.items()}
        self.fetched = []

    def get_bytes(self, key):
        self.fetched.append(key)
        return self.docs.get(key)


def test_a_version_is_fetched_once():
    store = Store(a={"ends": 1})
    cache = DocCache(store.get_bytes)
    assert cache.load("a", "v1") == {"ends": 1}
    assert cache.load("a", "v1") == {"ends": 1}
    assert store.fetched == ["a"]


def test_a_newer_version_replaces_the_older_one():
    store = Store(a={"ends": 1})
    cache = DocCache(store.get_bytes)
    cache.load("a", "v1")
    store.docs["a"] = json.dumps({"ends": 2, "more": "x" * 50}).encode()
    assert cache.load("a", "v2") == {"ends": 2, "more": "x" * 50}
    assert store.fetched == ["a", "a"]
    assert len(cache) == 1 and cache.bytes_kept == len(store.docs["a"])


def test_every_load_is_the_caller_s_own_copy():
    cache = DocCache(Store(a={"games": [{"index": 0}]}).get_bytes)
    mine = cache.load("a", "v1")
    mine["games"].clear()
    assert cache.load("a", "v1") == {"games": [{"index": 0}]}


def test_a_missing_document_is_a_key_error_and_nothing_is_kept():
    cache = DocCache(Store().get_bytes)
    with pytest.raises(KeyError):
        cache.load("nope", "v1")
    assert len(cache) == 0


def test_the_budget_lets_the_least_recently_used_go():
    store = Store(a={"x": "a" * 40}, b={"x": "b" * 40}, c={"x": "c" * 40})
    one = len(store.docs["a"])
    cache = DocCache(store.get_bytes, max_bytes=2 * one)
    cache.load("a", "1")
    cache.load("b", "1")
    cache.load("a", "1")            # a is now the more recent
    cache.load("c", "1")            # over budget: b goes
    assert cache.bytes_kept == 2 * one
    store.fetched.clear()
    cache.load("a", "1")
    cache.load("c", "1")
    assert store.fetched == []
    cache.load("b", "1")
    assert store.fetched == ["b"]


def test_a_document_bigger_than_the_budget_is_served_but_not_kept():
    store = Store(big={"x": "y" * 100})
    cache = DocCache(store.get_bytes, max_bytes=10)
    assert cache.load("big", "1") == {"x": "y" * 100}
    assert len(cache) == 0 and cache.bytes_kept == 0
