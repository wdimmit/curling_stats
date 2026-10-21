"""The server's half of signing in with an emailed link.

Firebase sends the link and signs the person in; what it cannot do is give
them a name, or carry a sign-in between the app that opened the link and the
screen that asked for it. Those jobs live here.
"""

import unicodedata

NAME_MAX = 60


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
