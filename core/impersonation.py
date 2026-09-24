# Staff impersonation helpers (django-impersonate, ADR 0028).
#
# The impersonate routes are mounted on the public URLconf only
# (config.urls_public). When ADMIN_HOST is set, the admin is served from a
# separate host whose URLconf (config.urls_admin) has no impersonate routes and
# whose session cookie does not reach the public host — so a link built there
# with a plain reverse() would fail, and a relative one would 404. This module
# builds the link against the public URLconf and, on a split-host deployment,
# makes it absolute on BASE_URL.

from __future__ import annotations

import logging

from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

_PUBLIC_URLCONF = "config.urls_public"


def impersonate_start_url(user_pk: int) -> str:
    """Return the URL that starts impersonating the user with ``user_pk``.

    Relative when the admin and the public site share a host (``ADMIN_HOST``
    empty); absolute on ``settings.BASE_URL`` when the admin has its own host,
    because the impersonation must run in the public host's session.
    """
    path = reverse("impersonate-start", args=[user_pk], urlconf=_PUBLIC_URLCONF)
    if settings.ADMIN_HOST:
        return f"{settings.BASE_URL}{path}"
    return path
