# Signed-link tokens for registration confirmation.
#
# Tokens are single-purpose (scoped by a dedicated salt) and expiring
# (``max_age``), per CLAUDE.md invariant 6. Each token type carries only the
# minimum payload needed for its single action.

from __future__ import annotations

from django.core import signing

# Salt for the combined-form registration confirmation token. Scopes the token
# to a single action so it cannot be replayed against other signing uses.
_CONFIRM_SALT = "accounts.registration-confirm"

# Tokens expire after 24 hours.
MAX_AGE_SECONDS = 60 * 60 * 24


def make_registration_confirmation_token(registration_pk: int) -> str:
    """Return a signed, single-purpose token carrying ``registration_pk``.

    Used in the combined-form flow: the token is emailed to the registrant and
    consumed by ``register_confirm`` to transition the registration from
    PENDING to WAITING. Salt is distinct from the email-verification salt
    (Invariant 6).
    """
    return signing.dumps({"registration_pk": registration_pk}, salt=_CONFIRM_SALT)


def read_registration_confirmation_token(
    token: str, max_age: int = MAX_AGE_SECONDS
) -> int | None:
    """Return the registration pk for a valid confirmation token, else ``None``.

    Returns ``None`` for a tampered, malformed, expired token, or one whose
    payload is not a valid integer.
    """
    try:
        data = signing.loads(token, salt=_CONFIRM_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    pk = data.get("registration_pk")
    return pk if isinstance(pk, int) else None
