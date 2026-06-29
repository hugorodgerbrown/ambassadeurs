"""Root URL configuration.

Mounts the Django admin, the account login/logout flow, the language switcher,
the sitemap, and the public site. HTMX fragment routes live under each app's
``partials/`` prefix. allauth has been removed (VERB-46); login is now
first-party magic-link under ``accounts/``.
"""

from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.views import healthz
from public.sitemaps import StaticViewSitemap

_sitemaps = {"static": StaticViewSitemap}

urlpatterns = [
    # Liveness probe — unauthenticated, must come before any catch-all route.
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    # First-party magic-link auth + account self-service (VERB-46).
    path("account/", include("accounts.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    # Machine-readable sitemap for search engine indexing.
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": _sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    # DEBUG-only test-data panel. Always mounted; every view raises Http404
    # when settings.DEBUG is false (via require_debug decorator).
    path("debug/", include("debug.urls")),
    path("", include("public.urls")),
]
