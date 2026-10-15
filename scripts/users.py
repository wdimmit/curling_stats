#!/usr/bin/env python
"""Who has an account, and who may do what.

    ADMIN_TOKEN=... python scripts/users.py                        # every account
    ADMIN_TOKEN=... python scripts/users.py role WHO admin|validated|none

WHO is an account's email address or its uid. BASE_URL defaults to
https://curling.dimmit.net. The admin token is the `curling-admin-token`
secret:
    ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest \\
        --secret=curling-admin-token --project=curling-stats-508323)

A validated account may rename a game's teams; an admin may also list the
accounts and validate them, on /users. Only this script makes or unmakes an
admin. An account exists once its person has signed in.
"""

import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROLES = {"admin": "admin", "validated": "validated", "none": None}

# As flags.py: the characters that change what a terminal does or shows.
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def _safe(text: str) -> str:
    return "".join(ch.encode("unicode_escape", "backslashreplace").decode()
                   if unicodedata.category(ch) in ("Cc", "Cs") or ord(ch) in _BIDI else ch
                   for ch in text)


def describe(u: dict) -> str:
    """One account as one line: role, email, name, joined, last seen, uid."""
    parts = [f"{u.get('role') or '-':<9}", u.get("email") or "(no email)"]
    if u.get("name"):
        parts.append(u["name"])
    parts.append(f"joined {(u.get('created_at') or '?')[:10]}")
    if u.get("last_seen_at"):
        parts.append(f"seen {u['last_seen_at'][:10]}")
    parts.append(u["id"])
    return _safe("  ".join(parts))


def find(users: list, who: str) -> dict | None:
    """The account with this uid, or this email address in any case."""
    for u in users:
        if u["id"] == who or (u.get("email") or "").lower() == who.lower():
            return u
    return None


def _call(url: str, token: str, body=None):
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "PUT")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _why(err: Exception) -> str:
    """An HTTP or network failure as one line: the status and the API's detail."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            detail = json.loads(err.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return f"{err.code} {detail or err.reason}"
    return str(getattr(err, "reason", err))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    role = sub.add_parser("role", help="set an account's role")
    role.add_argument("who", help="email address or uid")
    role.add_argument("role", choices=sorted(ROLES))
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see this script's docstring)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    try:
        users = _call(f"{base}/api/admin/users?limit=1000", token)["users"]
        if args.cmd != "role":
            print("\n".join(describe(u) for u in users) or "no accounts")
            return 0
        u = find(users, args.who)
        if u is None:
            print(f"no account for {args.who!r}; they have to sign in once first",
                  file=sys.stderr)
            return 1
        got = _call(f"{base}/api/admin/users/{urllib.parse.quote(u['id'], safe='')}/role",
                    token, {"role": ROLES[args.role]})
    except OSError as err:              # URLError and HTTPError are OSErrors
        print(f"could not reach the accounts: {_why(err)}", file=sys.stderr)
        return 1
    print(describe(got))
    return 0


if __name__ == "__main__":
    sys.exit(main())
