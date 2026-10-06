"""FirestoreRepo.put_flag_if_absent without the emulator: only "it exists
already" means no; any other refusal is an error, never a silent skip."""

from datetime import datetime, timezone

import pytest
from google.api_core.exceptions import Aborted, AlreadyExists

from curling_score.service.firestore_repo import FirestoreRepo
from curling_score.service.records import Flag


class Client:
    """Just enough of firestore.Client for one create()."""

    def __init__(self, exc=None):
        self.exc, self.created = exc, []

    def collection(self, name):
        client = self

        class Doc:
            def __init__(self, doc_id):
                self.doc_id = doc_id

            def create(self, data):
                if client.exc is not None:
                    raise client.exc
                client.created.append((name, self.doc_id))

        class Col:
            def document(self, doc_id):
                return Doc(doc_id)

        return Col()


FLAG = Flag(id="fa_1", created_at=datetime(2026, 10, 5, tzinfo=timezone.utc), note="n",
            origin="auto")


def test_a_new_flag_is_created():
    client = Client()
    assert FirestoreRepo(client=client).put_flag_if_absent(FLAG) is True
    assert client.created == [("flags", "fa_1")]


def test_one_that_exists_is_left_alone():
    assert FirestoreRepo(client=Client(AlreadyExists("exists"))).put_flag_if_absent(FLAG) is False


def test_an_aborted_write_is_an_error_not_an_existing_flag():
    with pytest.raises(Aborted):
        FirestoreRepo(client=Client(Aborted("contention"))).put_flag_if_absent(FLAG)
