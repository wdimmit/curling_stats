"""The deployable app: wire the real repo, store and YouTube client from the environment.

Kept apart from ``api.create_app`` so that importing the API in a test never
reaches for cloud credentials. ``uvicorn curling_score.service.asgi:app``.
"""

import logging
import os

from curling_score.service.api import Settings, create_app


def build():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = Settings.from_env()

    # REPO and STORE pick each backend; MEMORY_BACKENDS=1 is shorthand for both
    # in memory, which makes the whole service run in one process with no
    # cloud at all (state lives only as long as the process does).
    memory = os.environ.get("MEMORY_BACKENDS") == "1"
    repo_kind = "memory" if memory else os.environ.get("REPO", "firestore")
    store_kind = "memory" if memory else os.environ.get("STORE", "gcs")
    if repo_kind == "memory":
        from curling_score.service.repo import MemoryRepo
        repo = MemoryRepo()
    else:
        from curling_score.service.firestore_repo import FirestoreRepo
        repo = FirestoreRepo(project=os.environ.get("GCP_PROJECT"),
                             database=os.environ.get("FIRESTORE_DATABASE"))
    if store_kind == "memory":
        from curling_score.service.store import MemoryStore
        store = MemoryStore()
    else:
        from curling_score.service.store import GcsStore
        store = GcsStore(os.environ["GCS_BUCKET"],
                         signer_email=os.environ.get("SIGNER_EMAIL"))

    # YOUTUBE=api needs YT_API_KEY and is what Cloud Run should run; ytdlp is
    # for a home machine running the whole thing locally; fake refuses all.
    kind = os.environ.get("YOUTUBE") or ("api" if os.environ.get("YT_API_KEY") else "ytdlp")
    if kind == "api":
        from curling_score.service.youtube import YouTubeClient
        youtube = YouTubeClient(os.environ["YT_API_KEY"])
    elif kind == "ytdlp":
        from curling_score.service.youtube import YtDlpYouTube
        logging.getLogger(__name__).warning(
            "using yt-dlp for metadata: fine at home, blocked from a datacenter IP")
        youtube = YtDlpYouTube()
    else:
        from curling_score.service.youtube import FakeYouTube
        youtube = FakeYouTube()
    return create_app(repo, store, youtube, settings)


app = build()
