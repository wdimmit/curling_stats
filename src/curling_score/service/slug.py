"""Unguessable identifiers, since a link is the only key anyone holds.

Sixteen random bytes is 128 bits: nobody enumerates that. Base62 keeps the id
to 22 characters of letters and digits, so it survives being read aloud,
pasted into a chat, or written on a whiteboard at the rink.
"""

import secrets

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(ALPHABET)}

CHART_BYTES = 16   # 22 chars
SHORT_BYTES = 12   # 17 chars, for runs and jobs nobody types


def _length_for(nbytes: int) -> int:
    # Enough base62 digits to hold any nbytes value, zero-padded so every id
    # of a kind is the same length.
    n, length = 1 << (8 * nbytes), 0
    while n > 1:
        n = (n + 61) // 62
        length += 1
    return length


def encode(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    out = []
    while value:
        value, rem = divmod(value, 62)
        out.append(ALPHABET[rem])
    text = "".join(reversed(out)) or "0"
    return text.rjust(_length_for(len(data)), "0")


def new_slug(nbytes: int = CHART_BYTES, prefix: str = "") -> str:
    return prefix + encode(secrets.token_bytes(nbytes))


def new_run_id() -> str:
    return new_slug(SHORT_BYTES, "r_")


def new_job_id() -> str:
    return new_slug(SHORT_BYTES, "j_")


def is_slug(text: str, nbytes: int = CHART_BYTES) -> bool:
    """Whether ``text`` has the exact shape of a slug we would have issued."""
    return (isinstance(text, str) and len(text) == _length_for(nbytes)
            and all(c in _INDEX for c in text))
