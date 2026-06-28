# Signed-link tokens for registration confirmation, magic-link login, and match access.
#
# Tokens are single-purpose (scoped by a dedicated salt) and expiring
# (``max_age``), per CLAUDE.md invariant 6. They carry only the minimum payload
# needed for each action; the object is looked up when the token is consumed.

from __future__ import annotations

from django.conf import settings
from django.core import signing

# Salt for the combined-form registration confirmation token. Scopes the token
# to a single action so it cannot be replayed against other signing uses.
_CONFIRM_SALT = "accounts.registration-confirm"

# Tokens expire after 24 hours.
MAX_AGE_SECONDS = 60 * 60 * 24

# Salt for match-access tokens; separate from the confirmation salt so the two
# cannot be replayed against each other (Invariant 6).
_MATCH_SALT = "accounts.match-access"

# Salt for magic-link login tokens. Scoped separately from registration-confirm
# and match-access salts so tokens cannot be replayed across purposes (Invariant 6).
_LOGIN_SALT = "accounts.login"

# Login tokens expire after 1 hour.
LOGIN_TOKEN_MAX_AGE = 60 * 60


def make_registration_confirmation_token(registration_pk: int) -> str:
    """Return a signed, single-purpose token carrying ``registration_pk``.

    Used in the combined-form flow: the token is emailed to the registrant and
    consumed by ``register_confirm`` to transition the registration from
    UNVERIFIED to VERIFIED. Salt is distinct from the match-access salt
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


def make_match_access_token(match_pk: int, registration_pk: int) -> str:
    """Return a signed, single-purpose token granting access to a match page.

    The token carries the match and registration primary keys so the view can
    load and validate the correct objects without embedding any PII in the URL.
    Scoped by ``_MATCH_SALT`` so it cannot be replayed as a registration
    confirmation token (Invariant 6).

    Args:
        match_pk: Primary key of the Match the token grants access to.
        registration_pk: Primary key of the Registration (ambassador or
            referee) the token is issued to. Proves the holder is a party.
    """
    return signing.dumps(
        {"match_pk": match_pk, "registration_pk": registration_pk},
        salt=_MATCH_SALT,
    )


def make_login_token(user_pk: int) -> str:
    """Return a signed, single-purpose magic-link login token carrying ``user_pk``.

    Used in the magic-link login flow: the token is emailed to the user and
    consumed by ``login_verify`` to authenticate without a password. Salt is
    distinct from the registration-confirmation and match-access salts (Invariant 6).
    Token expires after ``LOGIN_TOKEN_MAX_AGE`` seconds (1 hour).

    Args:
        user_pk: Primary key of the User to authenticate.
    """
    return signing.dumps({"user_pk": user_pk}, salt=_LOGIN_SALT)


def read_login_token(
    token: str, max_age: int = LOGIN_TOKEN_MAX_AGE
) -> int | None:
    """Return the user pk for a valid login token, else ``None``.

    Returns ``None`` for a tampered, malformed, expired token, or one whose
    payload is not a valid integer. The default ``max_age`` is 1 hour; pass
    ``max_age=-1`` in tests to force expiry without mocking time.

    Args:
        token: A token previously returned by ``make_login_token``.
        max_age: Override the maximum token age in seconds.
    """
    try:
        data = signing.loads(token, salt=_LOGIN_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    pk = data.get("user_pk")
    return pk if isinstance(pk, int) else None


def read_match_access_token(
    token: str, max_age: int | None = None
) -> tuple[int, int] | None:
    """Return ``(match_pk, registration_pk)`` for a valid token, else ``None``.

    The default ``max_age`` equals the configured contact window
    (``settings.CONTACT_WINDOW_HOURS * 3600``) so tokens expire when the window
    does. Pass ``max_age=-1`` in tests to force expiry without mocking time.

    Returns ``None`` for tampered, malformed, or expired tokens.

    **Token expiry vs ``match.expires_at``.**
    Tokens are minted inside ``send_match_notification`` (via
    ``transaction.on_commit``), which fires seconds *after* the match is created.
    ``match.expires_at`` is set at match-creation time, so the token's ``max_age``
    clock starts slightly later than ``expires_at`` — the two can drift by a few
    seconds. The view's ``match.expires_at`` check (``display_state`` logic) is
    the authoritative gate on whether a match is still actionable; this token
    ``max_age`` is a backstop that prevents links from working past the window even
    if the view's clock check is somehow bypassed.

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
