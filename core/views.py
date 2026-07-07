# Core views shared across the project.
#
# Currently holds utility views that do not belong to any single domain app:
#   - robots_txt: serves /robots.txt as text/plain, dynamically building the
#     Sitemap absolute URL from the request host so it works on every
#     deployment (local dev, staging, production) without configuration.
#
#   - healthz: health-check view.
#
# Provides a cheap liveness probe used by Render's health-check mechanism and
# any external uptime monitors. The endpoint is unauthenticated, GET-only, and
# performs a trivial SELECT 1 to confirm the database is reachable.
#
# SSL redirect reasoning: production settings set SECURE_SSL_REDIRECT = True,
# but also set SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https").
# Render terminates TLS at its proxy and forwards requests with the header
# X-Forwarded-Proto: https, so Django sees the probe as already secure and does
# NOT issue a 301 redirect. No SSL-redirect exemption is needed here.

import logging

from django.db import OperationalError, connection
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def robots_txt(request: HttpRequest) -> HttpResponse:
    """Serve /robots.txt as plain text.

    Allows all public content pages (home, how-it-works, faq, legal) and
    disallows authenticated / partial routes, plus transactional pages that
    set no meta description and have no standalone search value, so crawlers
    do not index private or machine-facing endpoints.

    Disallow (rather than a ``noindex`` meta tag) is the deliberate mechanism
    for these paths, consistent with the existing entries: a Disallow'd URL
    is never crawled, so a ``noindex`` meta on it would never be read anyway.

    The Sitemap line uses ``request.build_absolute_uri`` so the host is always
    correct regardless of the deployment environment.

    Args:
        request: The incoming HTTP request.

    Returns:
        An ``HttpResponse`` with content-type ``text/plain`` containing the
        robots.txt directives.
    """
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    body = (
        "User-agent: *\n"
        "Disallow: /account/\n"
        "Disallow: /admin/\n"
        "Disallow: /match/\n"
        "Disallow: /register/confirm/\n"
        "Disallow: /register/sent/\n"
        "Disallow: /register/done/\n"
        "Disallow: /register/pay/\n"
        "Disallow: /tip/\n"
        f"Sitemap: {sitemap_url}\n"
    )
    return HttpResponse(body, content_type="text/plain")


@require_GET
def healthz(request: HttpRequest) -> HttpResponse:
    """Return HTTP 200 when the application and database are reachable.

    Performs a ``SELECT 1`` via the default database connection. Returns a
    plain-text ``ok`` body on success, or HTTP 503 if the database query
    raises an exception.

    This view is unauthenticated and CSRF-irrelevant (GET-only). It is
    intentionally exempt from login guards and session overhead so that
    monitoring probes never need a valid session or CSRF token.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError:
        logger.exception("Health check failed: database unreachable")
        return HttpResponse(status=503)
    return HttpResponse("ok", content_type="text/plain")
