# Core views shared across the project.
#
# Currently holds utility views that do not belong to any single domain app:
#   - robots_txt: serves /robots.txt as text/plain, dynamically building the
#     Sitemap absolute URL from the request host so it works on every
#     deployment (local dev, staging, production) without configuration.
#
#   - llms_txt: serves /llms.txt, the curated Markdown index of the site for
#     LLMs and AI agents (llmstxt.org).
#
#   - healthz: health-check view.
#
# robots.txt, llms.txt and sitemap.xml are machine-facing documents. Their
# copy is deliberately NOT wrapped for translation: Invariant 8 governs
# user-facing display strings, and none of these three is rendered in the UI
# or read by a human visitor.
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
from django.template.loader import render_to_string
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

    The ``Content-Signal`` line states the AI-usage policy for the content
    that *is* crawlable. The three signals are orthogonal: ``search`` (may
    appear in search results), ``ai-input`` (may be used as live context in an
    AI answer), and ``ai-train`` (may be used to train a model). The site
    permits the first two and refuses the third. Strict robots.txt validators
    warn about the directive because it is not in RFC 9309; that is expected
    and harmless, since RFC 9309 requires unknown lines to be ignored.

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
        "Content-Signal: search=yes, ai-input=yes, ai-train=no\n"
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
def llms_txt(request: HttpRequest) -> HttpResponse:
    """Serve /llms.txt — the curated Markdown index of the site for LLMs.

    Follows the llmstxt.org format: an H1 site name, a blockquote summary,
    free-form context, then sectioned lists of annotated links. It is a README
    for AI-mediated conversations, not a second sitemap — only the pages worth
    reading are listed. Transactional routes (register, match, account, tip,
    pay) are excluded here for the same reason robots.txt disallows them.

    Links are built with ``{% url %}`` and prefixed with the request's own
    scheme and host, so the file cannot drift out of step with the URLconf and
    needs no per-environment configuration.

    Served as ``text/markdown`` (RFC 7763), the content type the format
    implies; every fetcher that reads it also accepts text/plain semantics.

    Rendered via ``render_to_string`` with an explicit context rather than
    ``render(request, ...)``: the latter builds a RequestContext and so runs
    every context processor, including the notifications one, which queries the
    database. This endpoint needs none of that — like robots.txt, it serves a
    machine-facing document and touches no models.

    Args:
        request: The incoming HTTP request.

    Returns:
        An ``HttpResponse`` with content-type ``text/markdown`` containing the
        rendered llms.txt body.
    """
    body = render_to_string(
        "llms.txt",
        {"base": request.build_absolute_uri("/").rstrip("/")},
    )
    return HttpResponse(body, content_type="text/markdown; charset=utf-8")


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
