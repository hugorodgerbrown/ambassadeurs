# Signed-link tokens for passwordless email verification.
#
# Tokens are single-purpose (scoped by a dedicated salt) and expiring
# (``max_age``), per CLAUDE.md invariant 6. They carry only the email being
# verified; the user is created/looked up when the token is consumed.

from __future__ import annotations

from django.core import signing

# Salt scopes the token to this one action; a token minted here cannot be
# replayed against any other signing use.
_SALT = "accounts.register-verify"

# Tokens expire after 24 hours.
MAX_AGE_SECONDS = 60 * 60 * 24


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
