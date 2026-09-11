"""Blob storage for the artifacts a run produces.

The API never proxies uploads: the worker asks for a signed URL and PUTs the
file straight to the bucket, because Cloud Run caps a request body at 32 MB and
a detection cache for one video is already that. Reads of the 1.7 MB timeline
do go through the API, so the browser never needs bucket credentials or CORS.
"""

import threading
from datetime import timedelta
from typing import Protocol


class Store(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def presign_put(self, key: str, content_type: str, expires_s: int = 3600) -> dict: ...
    def presign_get(self, key: str, expires_s: int = 3600) -> str: ...


class MemoryStore:
    """A dict standing in for a bucket. Signed URLs are ``memory://`` names."""

    def __init__(self):
        self._lock = threading.Lock()
        self.blobs: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        with self._lock:
            self.blobs[key] = (bytes(data), content_type)

    def get_bytes(self, key):
        item = self.blobs.get(key)
        return item[0] if item else None

    def exists(self, key):
        return key in self.blobs

    def list_keys(self, prefix):
        return sorted(k for k in self.blobs if k.startswith(prefix))

    def presign_put(self, key, content_type, expires_s=3600):
        return {"url": f"memory://{key}", "headers": {"Content-Type": content_type}}

    def presign_get(self, key, expires_s=3600):
        return f"memory://{key}"


class GcsStore:
    """A Google Cloud Storage bucket.

    Signed URLs need a private key, which the Cloud Run service account does
    not carry. Pass ``signer_email`` and the store will sign through the IAM
    ``signBlob`` API using the ambient access token instead -- the account
    needs ``roles/iam.serviceAccountTokenCreator`` on itself for that.
    """

    def __init__(self, bucket: str, client=None, signer_email: str | None = None):
        from google.cloud import storage

        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._signer_email = signer_email

    def _blob(self, key):
        return self._bucket.blob(key)

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self._blob(key).upload_from_string(data, content_type=content_type)

    def get_bytes(self, key):
        blob = self._blob(key)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def exists(self, key):
        return self._blob(key).exists()

    def list_keys(self, prefix):
        return [b.name for b in self._client.list_blobs(self._bucket, prefix=prefix)]

    def _sign_kwargs(self):
        if not self._signer_email:
            return {}
        import google.auth
        import google.auth.transport.requests

        credentials, _project = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        return {"service_account_email": self._signer_email,
                "access_token": credentials.token}

    def presign_put(self, key, content_type, expires_s=3600):
        url = self._blob(key).generate_signed_url(
            version="v4", expiration=timedelta(seconds=expires_s), method="PUT",
            content_type=content_type, **self._sign_kwargs(),
        )
        return {"url": url, "headers": {"Content-Type": content_type}}

    def presign_get(self, key, expires_s=3600):
        return self._blob(key).generate_signed_url(
            version="v4", expiration=timedelta(seconds=expires_s), method="GET",
            **self._sign_kwargs(),
        )


def timeline_key(video_id: str, run_id: str) -> str:
    return f"runs/{video_id}/{run_id}/timeline.json"


def meta_key(video_id: str, run_id: str) -> str:
    return f"runs/{video_id}/{run_id}/meta.json"


def detcache_key(video_id: str, digest: str) -> str:
    return f"detcache/{video_id}/{digest}.npz"
