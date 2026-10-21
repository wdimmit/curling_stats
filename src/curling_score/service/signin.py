"""The server's half of signing in with an emailed link.

Firebase sends the link and signs the person in; what it cannot do is give
them a name, or carry a sign-in between the app that opened the link and the
screen that asked for it. Those jobs live here.
"""

import re
import secrets
import unicodedata
from urllib.parse import parse_qs, urlsplit

NAME_MAX = 60

# A relayed link and its code live ten minutes; the fifth wrong code deletes
# the relay. The club shares one IP, so the per-address limits are loose and
# the miss cap is the real guard against guessing six digits.
RELAY_TTL_S = 600
RELAY_MAX_MISSES = 5
RELAY_HOUR_LIMIT = 30
RELAY_DAY_LIMIT = 100
CLAIM_HOUR_LIMIT = 60
CLAIM_DAY_LIMIT = 300
# A Firebase sign-in link is a few hundred bytes; anything much longer is
# somebody using the relay to store something.
MAX_LINK_BYTES = 2048
# 16 random bytes as base64url. Firestore reserves document ids that start and
# end with "__" and raises on them, so an id with "__" at either end is
# refused too: the browser never makes one (signinState.mjs newRequestId), and
# its requestIdOf has the same rule.
REQUEST_ID = re.compile(r"^(?!__)[A-Za-z0-9_-]{22}(?<!__)$")


def clean_name(raw) -> str | None:
    """A name as the site will show it, or None if it will not do.

    Whitespace runs become one space and the ends are trimmed, so "  Jo \t
    Smith " and "Jo Smith" are the same person. Anything in a Unicode "other"
    category (controls, zero-width and bidi marks, unassigned) is refused
    rather than stripped: a name that looks like one thing and is another is
    not one a teammate should be shown.
    """
    if not isinstance(raw, str):
        return None
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not 1 <= len(name) <= NAME_MAX:
        return None
    if any(unicodedata.category(c).startswith("C") for c in name):
        return None
    return name


def relay_id_of(link, base_url: str, api_key: str) -> str | None:
    """The request id in `link`, if it is one of this site's own sign-in links.

    The relay is open to anyone, so it must hold nothing but what Firebase
    would have sent for this site: our scheme and host, the /signin page, a
    sign-in (not a password reset) action with its code, our API key, and the
    request id the asking browser put on it. Each parameter must appear exactly
    once, so there is no second `oobCode` for one reader to take and another
    to ignore. A `link` or `deep_link_id` parameter is refused outright: the
    Firebase SDK prefers the URL nested inside one over the outer URL and reads
    `oobCode`, `mode` and `apiKey` from it, so it could point the sign-in
    anywhere. No Firebase link carries non-ASCII, so none is accepted. With no
    usable `base_url` (no scheme or host) nothing is accepted.
    """
    if not isinstance(link, str) or not link.isascii() or len(link) > MAX_LINK_BYTES:
        return None
    if not base_url_ok(base_url):
        return None
    try:
        parts, base = urlsplit(link), urlsplit(base_url)
        query = parse_qs(parts.query, keep_blank_values=True)
    except ValueError:
        return None
    if (parts.scheme, parts.netloc) != (base.scheme, base.netloc) or parts.path != "/signin":
        return None
    if "link" in query or "deep_link_id" in query:
        return None

    def one(name):
        got = query.get(name, [])
        return got[0] if len(got) == 1 else None

    rid = one("r")
    if one("mode") != "signIn" or not one("oobCode") or one("apiKey") != api_key:
        return None
    return rid if rid is not None and REQUEST_ID.fullmatch(rid) else None


def base_url_ok(base_url) -> bool:
    """Whether `base_url` has the scheme and host a link is checked against.
    PUBLIC_BASE_URL may be left unset on a first deploy, and until it is set
    no link is this site's."""
    try:
        base = urlsplit(base_url)
    except ValueError:
        return False
    return bool(base.scheme and base.netloc)


def new_code() -> str:
    """Six digits, zero-padded, from the system's secure random source."""
    return f"{secrets.randbelow(10**6):06d}"
