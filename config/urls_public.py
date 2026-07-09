"""Public-site URL configuration — the Django admin is deliberately excluded.

Mounts everything the public site serves: the liveness probe, the first-party
magic-link auth + account self-service flow, the language switcher, the sitemap,
robots, the Stripe webhook, and the public catch-all — but **not** ``/admin/``.

On a deployment with ``ADMIN_HOST`` set, ``core.middleware.AdminHostMiddleware``
selects this URLconf for every host *other* than the admin subdomain, so the
admin surface does not exist on the public site. When ``ADMIN_HOST`` is unset the
combined ``config.urls`` is used instead (admin at ``/admin/``). See ADR 0022.

``webhooks/stripe/`` (VERB-86) is mounted here rather than under ``public.urls``
so it is never nested under a locale prefix or any future app-level routing
change — Stripe needs one stable, permanent path.
"""

from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.views import healthz, robots_txt
from public.sitemaps import StaticViewSitemap
from public.views import stripe_webhook

_sitemaps = {"static": StaticViewSitemap}

urlpatterns = [
    # Liveness probe — unauthenticated, must come before any catch-all route.
    path("healthz/", healthz, name="healthz"),
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
