"""Root URL configuration.

Mounts the Django admin, the account login/logout flow, the language switcher,
the sitemap, and the public site. HTMX fragment routes live under each app's
``partials/`` prefix. allauth has been removed (VERB-46); login is now
first-party magic-link under ``accounts/``.

``webhooks/stripe/`` (VERB-86) is mounted here rather than under
``public.urls`` so it is never nested under a locale prefix or any future
app-level routing change — Stripe needs one stable, permanent path.
"""

from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.views import healthz, robots_txt
from public.sitemaps import StaticViewSitemap
from public.views import stripe_webhook

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
    # Search-engine control (VERB-63). Must come before the public catch-all.
    path("robots.txt", robots_txt, name="robots_txt"),
    # Stripe checkout.session.completed webhook (VERB-86) — un-prefixed.
    path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
    path("", include("public.urls")),
]
