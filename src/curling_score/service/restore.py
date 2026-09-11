"""Load an admin export back into a repository.

The export is plain JSON, so datetimes arrive as ISO strings; this turns them
back into datetimes and writes every record. Idempotent: loading the same
export twice leaves the same data.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

_TIME_FIELDS = {"created_at", "updated_at", "ready_at", "run_after", "lease_expires_at",
                "progress_at", "started_at", "finished_at", "last_seen_at",
                "last_polled_at", "published_at", "played_at"}


def _revive(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if k in _TIME_FIELDS and isinstance(v, str):
            out[k] = datetime.fromisoformat(v)
        else:
            out[k] = v
    return out


def restore(repo, data: dict) -> dict:
    revived = {col: [_revive(r) for r in rows] for col, rows in data.items()}
    repo.import_all(revived)
    return {col: len(rows) for col, rows in revived.items()}


def main(argv=None) -> int:
    import os

    path = Path((argv or sys.argv[1:])[0])
    data = json.loads(path.read_text())
    if os.environ.get("MEMORY_BACKENDS") == "1":
        from curling_score.service.repo import MemoryRepo
        repo = MemoryRepo()
    else:
        from curling_score.service.firestore_repo import FirestoreRepo
        repo = FirestoreRepo(project=os.environ.get("GCP_PROJECT"),
                             database=os.environ.get("FIRESTORE_DATABASE"))
    print(restore(repo, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
