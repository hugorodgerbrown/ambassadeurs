"""Public-site URL configuration — the Django admin is deliberately excluded.

Mounts everything the public site serves: the liveness probe, the first-party
magic-link auth + account self-service flow, the language switcher, the sitemap,
robots, llms.txt, the Stripe webhook, and the public catch-all — but **not**
``/admin/``.

On a deployment with ``ADMIN_HOST`` set, ``core.middleware.AdminHostMiddleware``
selects this URLconf for every host *other* than the admin subdomain, so the
admin surface does not exist on the public site. When ``ADMIN_HOST`` is unset the
combined ``config.urls`` is used instead (admin at ``/admin/``). See ADR 0022.

``webhooks/stripe/`` (VERB-86) is mounted here rather than under ``public.urls``
so it is never nested under a locale prefix or any future app-level routing
change — Stripe needs one stable, permanent path.

**Language prefixing (SKI-153, ADR 0025).** The patterns split in two:

- *Unprefixed* — machine-facing documents (robots.txt, llms.txt, sitemap.xml),
  the liveness probe, the Stripe webhook, the language switcher, and the
  DEBUG-only panel. None of these render translated content for a human, and
  three of them are fetched by clients that must find them at a fixed, well-known
  path.
- *Prefixed* via ``i18n_patterns(..., prefix_default_language=False)`` — every
  human-facing page. English keeps the existing unprefixed URLs (``/faq/``) and
  French gains its own (``/fr/faq/``), so no inbound link breaks and French
  becomes crawlable for the first time.

The signed-token routes (match actions, registration confirmation, magic-link
login) sit **inside** the prefix on purpose: ``core.emails`` already renders each
message under ``translation.override(recipient_language)``, so ``reverse()``
inside that block yields a ``/fr/`` link for a French recipient and the page
renders in their language regardless of the browser's ``Accept-Language``.
Tokens issued before this change carry unprefixed URLs, which still resolve as
English — no link already in an inbox breaks.
"""

from django.conf.urls.i18n import i18n_patterns
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.views import healthz, llms_txt, robots_txt
from public.sitemaps import StaticViewSitemap
from public.views import stripe_webhook

_sitemaps = {"static": StaticViewSitemap}

# Unprefixed routes — never carry a language prefix. See the module docstring.
urlpatterns = [
    # Liveness probe — unauthenticated, must come before any catch-all route.
    path("healthz/", healthz, name="healthz"),
    # NB: django.conf.urls.i18n (set_language) is deliberately NOT mounted.
    # Since the URL carries the language, switching is navigation — the footer
    # links straight to the other language's URL. With
    # prefix_default_language=False an unprefixed path is pinned to the default
    # language regardless of the django_language cookie, so set_language had
    # nothing left to select. Restore it here if a cookie-driven preference is
    # ever needed again. See ADR 0025.
    # Machine-readable sitemap for search engine indexing. Emits both language
    # variants with hreflang alternates (see public.sitemaps).
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
    # Curated Markdown index for LLMs and AI agents, llmstxt.org (SKI-150).
    # Root-level by convention, like robots.txt — must precede the catch-all.
    path("llms.txt", llms_txt, name="llms_txt"),
    # Stripe checkout.session.completed webhook (VERB-86) — un-prefixed.
    path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
    # CSP violation report endpoint + diagnostics (SKI-170, ADR 0027). The
    # report URL is written into the header by csp.policy, which reverses
    # "csp:report_uri" — so this must stay OUTSIDE i18n_patterns: a browser
    # posting a violation report is a machine, and a language-prefixed report
    # URL would vary by the language of the page that generated it.
    path("csp/", include("csp.urls", namespace="csp")),
]

# Human-facing routes — English unprefixed, French under /fr/.
urlpatterns += i18n_patterns(
    # First-party magic-link auth + account self-service (VERB-46).
    path("account/", include("accounts.urls")),
    path("", include("public.urls")),
    prefix_default_language=False,
)
