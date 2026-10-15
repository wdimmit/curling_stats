"""The terminal side of accounts: listing them, and making admins."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "users_script", Path(__file__).resolve().parents[1] / "scripts" / "users.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def account(**kw):
    base = {"id": "uid-alex", "email": "alex@example.org", "name": "Alex", "picture_url": None,
            "created_at": "2026-10-01T19:03:00+00:00", "last_seen_at": "2026-10-15T08:00:00+00:00",
            "role": None}
    base.update(kw)
    return base


def test_an_account_is_one_line():
    assert script.describe(account()) == (
        "-          alex@example.org  Alex  joined 2026-10-01  seen 2026-10-15  uid-alex")


def test_the_role_leads():
    assert script.describe(account(role="validated")).startswith("validated  alex@")
    assert script.describe(account(role="admin")).startswith("admin      alex@")


def test_what_google_did_not_give_still_lists():
    line = script.describe(account(email=None, name=None, last_seen_at=None))
    assert line == "-          (no email)  joined 2026-10-01  uid-alex"


def test_a_name_cannot_drive_the_terminal():
    line = script.describe(account(name="A\x1b]52;c;x\x07‮"))
    assert "\x1b" not in line and "\x07" not in line and "‮" not in line


def test_found_by_email_whatever_its_case_or_by_uid():
    users = [account(), account(id="uid-sarah", email="sarah@example.org")]
    assert script.find(users, "Sarah@Example.org")["id"] == "uid-sarah"
    assert script.find(users, "uid-alex")["email"] == "alex@example.org"
    assert script.find(users, "nobody@example.org") is None


def test_none_takes_the_role_away():
    assert script.ROLES == {"admin": "admin", "validated": "validated", "none": None}
