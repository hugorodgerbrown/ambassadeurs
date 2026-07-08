"""Admin-subdomain URL configuration — serves *only* the Django admin.

The admin is mounted at the **root** of the admin subdomain, so the index is
``admin.<domain>/`` rather than ``admin.<domain>/admin/`` (there is no longer a
public site on this host to disambiguate it from). The liveness probe and the
i18n language switcher are also mounted so Render health checks and admin
language switching keep working on the subdomain.

``core.middleware.AdminHostMiddleware`` selects this URLconf when ``ADMIN_HOST``
is set and the request host matches it. See ADR 0022 and ``config.urls_public``.
"""

from django.contrib import admin
from django.urls import include, path

from core.views import healthz

urlpatterns = [
    # Liveness probe first, so a request to ``healthz/`` is not swallowed by the
    # admin's ``<app_label>/`` route below.
    path("healthz/", healthz, name="healthz"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", admin.site.urls),
]
