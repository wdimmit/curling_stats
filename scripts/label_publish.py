"""The admin client the labelling publishers share: create items, put their
images in the bucket, open them.

    publish(base, token, items, images)

The server decides what each item needs. A new id is made `pending` and comes
back with a signed upload; an id still `pending` comes back with a fresh one
(so a run that died between create and upload is finished by running it
again); an id any further along comes back with no upload and is left alone.
Only items with an upload have their image sent, then every id with an image
is asked to open, which the server does only for a pending item whose image
has arrived.

The signed URL is the bucket's, not ours: the admin token goes to the API and
nowhere else.
"""

import json
import urllib.error
import urllib.request

# Items per create call; the API refuses more than this.
PAGE = 500


def why(err: Exception) -> str:
    """An HTTP or network failure as one line: the status and the API's detail."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            detail = json.loads(err.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return f"{err.code} {detail or err.reason}"
    return str(getattr(err, "reason", err))


def _post(base, token, path, body, opener):
    req = urllib.request.Request(
        base.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with opener(req, timeout=60) as r:
        return json.loads(r.read())


def _put(upload: dict, data: bytes, opener) -> None:
    headers = dict(upload.get("headers") or {})
    if not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "image/jpeg"
    req = urllib.request.Request(upload["url"], data=data, headers=headers, method="PUT")
    with opener(req, timeout=120) as r:
        r.read()


def publish(base: str, token: str, items: list[dict], images: dict[str, bytes], *,
            dry_run: bool = False, opener=urllib.request.urlopen) -> dict:
    """Create the items, upload what the server asks for, open what has arrived.

    Returns ``{"opened": [...], "skipped": [...], "missing_image": [...]}``:
    ``skipped`` are the ids the server already had past `pending` (nothing was
    uploaded for them), and ``missing_image`` are ids the server reports
    without an image, or that we had no image for. ``dry_run`` sends nothing.
    """
    result = {"opened": [], "skipped": [], "missing_image": []}
    if dry_run:
        return result
    uploads: dict[str, dict | None] = {}
    for i in range(0, len(items), PAGE):
        got = _post(base, token, "/api/admin/label-items", {"items": items[i:i + PAGE]}, opener)
        for g in got["items"]:
            uploads[g["id"]] = g.get("upload")
    ready = []
    for item_id, upload in uploads.items():
        if upload is None:
            result["skipped"].append(item_id)
            ready.append(item_id)
        elif item_id not in images:
            result["missing_image"].append(item_id)
        else:
            _put(upload, images[item_id], opener)
            ready.append(item_id)
    if ready:
        got = _post(base, token, "/api/admin/label-items/ready", {"ids": ready}, opener)
        result["opened"] = got["opened"]
        result["missing_image"] += got["missing_image"]
    return result
