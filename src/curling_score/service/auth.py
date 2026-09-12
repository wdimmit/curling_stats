"""Who a request says it is.

Identity here is additive and never a gate. A chart link is still the whole of
the permission it carries -- holding ``/c/{slug}/`` means you may edit, signed
in or not, and that does not change. What an account buys is a way back to your
links, and a team to share one with.

So everything on this path fails open into "anonymous" rather than closed into
a 401: a token that is missing, expired, malformed or signed by someone else
simply means we do not know who this is, which is the state the whole service
was in until now. Only the handful of routes that genuinely need a person ask
for one.

Verifying a Firebase ID token needs no credential of its own -- only Google's
public signing certificates and the project id -- so nothing here reaches for
Application Default Credentials. The fake exists so the tests never reach for
Google at all, the same way ``FakeYouTube`` does for metadata.
"""

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class Verifier(Protocol):
    """Turns a bearer token into claims, or into None. Never raises."""

    enabled: bool

    def verify(self, id_token: str) -> dict | None: ...


class NoAuth:
    """Accounts are switched off.

    The default, and what every existing test gets: the site behaves exactly as
    it did before accounts existed, and the routes that need a person answer
    503 the way an unconfigured worker token does.
    """

    enabled = False

    def verify(self, id_token):
        return None


class FakeVerifier:
    """Hand-fed claims, for tests.

    Deliberately not usable for local development in a browser: signing in for
    real needs a real popup and a real project. This is only so a test can say
    "this request is Sarah" without a network.
    """

    enabled = True

    def __init__(self, tokens: dict | None = None):
        self._tokens = dict(tokens or {})

    def add(self, token: str, sub: str, email: str | None = None,
            name: str | None = None, email_verified: bool = True) -> str:
        self._tokens[token] = {"sub": sub, "email": email, "name": name,
                               "email_verified": email_verified}
        return token

    def verify(self, id_token):
        return self._tokens.get(id_token)


class FirebaseVerifier:
    """Google Sign-In, through Firebase Auth / Identity Platform.

    ``firebase-admin`` rather than ``google-auth``'s ``verify_firebase_token``,
    which looks like the lighter option and is not: it does not check ``iss``,
    and the securetoken certificates are shared by every Firebase project on
    earth, so ``aud`` is the only thing tying a token to this one. It also
    refetches those certificates on every single call. firebase-admin checks
    iss/aud/sub/auth_time, caches the certificates, and distinguishes an
    expired token from a forged one.

    Imported inside ``__init__`` so that importing the API never pulls in the
    SDK, matching FirestoreRepo and YouTubeClient.
    """

    enabled = True

    def __init__(self, project: str, clock_skew_s: int = 10, emulator: str = ""):
        import firebase_admin
        from firebase_admin import auth as fb_auth

        if emulator:
            # The emulator makes firebase-admin skip signature checking
            # altogether, so every forged token would be accepted. Fine at a
            # desk, catastrophic anywhere else -- say so loudly.
            log.warning("FIREBASE_AUTH_EMULATOR_HOST is set (%s): token signatures "
                        "are NOT checked. Never do this in production.", emulator)
        self._auth = fb_auth
        self._skew = clock_skew_s
        try:
            self._app = firebase_admin.get_app()
        except ValueError:
            self._app = firebase_admin.initialize_app(options={"projectId": project})

    def verify(self, id_token):
        try:
            # check_revoked stays off on purpose: it turns every request into a
            # call to the Identity Toolkit API. An hour of stale access to your
            # own list of links is not worth that.
            return self._auth.verify_id_token(id_token, app=self._app,
                                              clock_skew_seconds=self._skew)
        except Exception:                      # expired, forged, malformed
            return None


def from_env(env) -> Verifier:
    """Pick a verifier the way asgi picks a repo: from what is configured.

    No FIREBASE_PROJECT means no accounts, which is the right default for a
    checkout, for the test suite, and for anyone running this at home.
    """
    kind = env("AUTH") or ("firebase" if env("FIREBASE_PROJECT") else "none")
    if kind != "firebase":
        return NoAuth()
    return FirebaseVerifier(env("FIREBASE_PROJECT"),
                            emulator=env("FIREBASE_AUTH_EMULATOR_HOST", ""))
