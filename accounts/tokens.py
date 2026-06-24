# Signed-link tokens for passwordless email verification and match access.
#
# Tokens are single-purpose (scoped by a dedicated salt) and expiring
# (``max_age``), per CLAUDE.md invariant 6. They carry only the minimum payload
# needed for each action; the user is created/looked up when the token is
# consumed.

from __future__ import annotations

from django.conf import settings
from django.core import signing

# Salt scopes the email-verification token to that one action; a token minted
# here cannot be replayed against any other signing use.
_SALT = "accounts.register-verify"

# Tokens expire after 24 hours.
MAX_AGE_SECONDS = 60 * 60 * 24

# Salt for match-access tokens; separate from the verification salt so the two
# cannot be replayed against each other (Invariant 6).
_MATCH_SALT = "accounts.match-access"


def make_email_verification_token(email: str) -> str:
    """Return a signed, single-purpose token that verifies ``email``."""
    return signing.dumps({"email": email.lower()}, salt=_SALT)


def read_email_verification_token(
    token: str, max_age: int = MAX_AGE_SECONDS
) -> str | None:
    """Return the verified email for a valid token, else ``None``.

    Returns ``None`` for a tampered, malformed, or expired token.
    """
    try:
        data = signing.loads(token, salt=_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    email = data.get("email")
    return email if isinstance(email, str) else None


def make_match_access_token(match_pk: int, registration_pk: int) -> str:
    """Return a signed, single-purpose token granting access to a match page.

    The token carries the match and registration primary keys so the view can
    load and validate the correct objects without embedding any PII in the URL.
    Scoped by ``_MATCH_SALT`` so it cannot be replayed as an email-verification
    token (Invariant 6).

    Args:
        match_pk: Primary key of the Match the token grants access to.
        registration_pk: Primary key of the Registration (ambassador or
            referee) the token is issued to. Proves the holder is a party.
    """
    return signing.dumps(
        {"match_pk": match_pk, "registration_pk": registration_pk},
        salt=_MATCH_SALT,
    )


def read_match_access_token(
    token: str, max_age: int | None = None
) -> tuple[int, int] | None:
    """Return ``(match_pk, registration_pk)`` for a valid token, else ``None``.

    The default ``max_age`` equals the configured contact window
    (``settings.CONTACT_WINDOW_HOURS * 3600``) so tokens expire when the window
    does. Pass ``max_age=-1`` in tests to force expiry without mocking time.

    Returns ``None`` for tampered, malformed, or expired tokens.

    Args:
        token: A token previously returned by ``make_match_access_token``.
        max_age: Override the maximum token age in seconds. Defaults to the
            contact-window duration from settings.
    """
    if max_age is None:
        max_age = settings.CONTACT_WINDOW_HOURS * 3600
    try:
        data = signing.loads(token, salt=_MATCH_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    match_pk = data.get("match_pk")
    registration_pk = data.get("registration_pk")
    if not isinstance(match_pk, int) or not isinstance(registration_pk, int):
        return None
    return match_pk, registration_pk
