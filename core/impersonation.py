# Staff impersonation helpers (django-impersonate, ADR 0028).
#
# The impersonate routes are mounted on the public URLconf only
# (config.urls_public). When ADMIN_HOST is set, the admin is served from a
# separate host whose URLconf (config.urls_admin) has no impersonate routes and
# whose session cookie does not reach the public host — so the superuser who
# clicks "View as" in the admin has no session on the public site, and the
# impersonate view would bounce them to the login page.
#
# The admin therefore hands the superuser across with a signed token
# (ADR 0028, "The hand-off"): the admin-host "View as" view mints a
# short-lived, single-use token carrying the superuser and the target, and
# redirects to the public hand-off view, which signs the superuser in on the
# public host and starts the impersonation. This module owns the token and the
# public URLs.

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.urls import reverse

logger = logging.getLogger(__name__)

_PUBLIC_URLCONF = "config.urls_public"

# Salt for impersonation hand-off tokens; distinct from every accounts salt so a
# hand-off token cannot be replayed as a login or match token (Invariant 6).
_HANDOFF_SALT = "core.impersonation.handoff"

# The token only travels in the Location header of the admin's redirect, so it
# needs to survive one browser round trip and no more.
HANDOFF_MAX_AGE = 60


def _last_login_stamp(user: User) -> str:
    """Return ``user.last_login`` as a string for binding into a token.

    Redeeming a hand-off logs the superuser in, which moves ``last_login`` on,
    so a token bound to the old value is spent after one use.
    """
    return user.last_login.isoformat() if user.last_login else ""


def make_handoff_token(impersonator: User, target_pk: int) -> str:
    """Return a signed, single-use token handing ``impersonator`` to the public host.

    Carries the superuser's pk, the target user's pk and the superuser's
    current ``last_login`` (which makes the token single-use). Expires after
    ``HANDOFF_MAX_AGE`` seconds.
    """
    return signing.dumps(
        {"i": impersonator.pk, "t": target_pk, "l": _last_login_stamp(impersonator)},
        salt=_HANDOFF_SALT,
    )


def read_handoff_token(token: str) -> tuple[User, int] | None:
    """Return ``(superuser, target_pk)`` for a valid hand-off token, else ``None``.

    ``None`` for a tampered, malformed or expired token, one already redeemed
    (``last_login`` has moved on), or one whose impersonator is no longer an
    active superuser.
    """
    try:
        data = signing.loads(token, salt=_HANDOFF_SALT, max_age=HANDOFF_MAX_AGE)
    except signing.BadSignature:
        return None
    impersonator_pk, target_pk = data.get("i"), data.get("t")
    if not isinstance(impersonator_pk, int) or not isinstance(target_pk, int):
        return None
    impersonator = User.objects.filter(
        pk=impersonator_pk, is_active=True, is_superuser=True
    ).first()
    if impersonator is None or data.get("l") != _last_login_stamp(impersonator):
        return None
    return impersonator, target_pk


def _public_url(path: str) -> str:
    """Return ``path`` as-is on a single host, absolute on BASE_URL when split."""
    if settings.ADMIN_HOST:
        return f"{settings.BASE_URL}{path}"
    return path


def impersonate_start_url(user_pk: int) -> str:
    """Return the URL that starts impersonating the user with ``user_pk``.

    Relative when the admin and the public site share a host (``ADMIN_HOST``
    empty); absolute on ``settings.BASE_URL`` when the admin has its own host,
    because the impersonation must run in the public host's session.
    """
    return _public_url(
        reverse("impersonate-start", args=[user_pk], urlconf=_PUBLIC_URLCONF)
    )


def impersonate_handoff_url(impersonator: User, target_pk: int) -> str:
    """Return the public hand-off URL for ``impersonator`` to view as ``target_pk``.

    Redeeming it signs ``impersonator`` in on the public host, then starts the
    impersonation. Mint it at click time: the token lasts ``HANDOFF_MAX_AGE``.
    """
    token = make_handoff_token(impersonator, target_pk)
    return _public_url(
        reverse("impersonate-handoff", args=[token], urlconf=_PUBLIC_URLCONF)
    )
