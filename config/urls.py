"""Root URL configuration — the combined default (admin at ``/admin/``).

The default ``ROOT_URLCONF``. Mounts the Django admin at ``/admin/`` alongside
the full public site — the single-host behaviour used when ``ADMIN_HOST`` is
unset (local development, the test suite, and any single-host deployment). The
public routes are reused verbatim from ``config.urls_public`` so the two stay in
lock-step.

When ``ADMIN_HOST`` is set, ``core.middleware.AdminHostMiddleware`` overrides
``request.urlconf`` per request — the admin subdomain is served
``config.urls_admin`` (admin only) and every other host ``config.urls_public``
(no admin) — so this combined module is not used. See ADR 0022.
"""

from django.contrib import admin
from django.urls import path

from config.urls_public import urlpatterns as _public_urlpatterns

urlpatterns = [
    path("admin/", admin.site.urls),
    *_public_urlpatterns,
]
