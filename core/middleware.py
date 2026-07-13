# HTTP-layer middleware.
#
# PostHogExceptionMiddleware reports unhandled request exceptions to PostHog
# (VERB-65). Django catches request exceptions in its core handler and returns a
# 500 itself, so they never reach the interpreter excepthook that PostHog's
# autocapture hooks — process_exception is the reliable capture point for the
# web service. Management commands / crons are covered separately by
# enable_exception_autocapture (see core.observability).
#
# PostHogPageviewMiddleware sends a server-side $pageview event (VERB-124) for
# full-page content views — cookieless, consistent with the Cookie Policy's
# no-tracking-cookies stance. It tracks by default (coverage cannot silently
# decay as pages are added) and subtracts non-pages centrally: a request is a
# page-view only when it is a GET that resolved to a 200 *HTML* response, is
# not an HTMX partial swap, and does not resolve into an excluded namespace
# (admin / debug). The Content-Type gate does most of the filtering — it drops
# JSON/API, images, robots.txt and healthz (text/plain), sw.js
# (application/javascript), sitemap.xml (application/xml) and the redirect-based
# application-form download without enumerating any of them. Both PostHog
# middlewares are production-only (registered in config/settings/production.py,
# not base.py).
#
# AdminHostMiddleware confines the Django admin to its own subdomain (ADR 0022)
# by swapping request.urlconf per host. It is registered in base.py (all
# environments) and is a no-op unless settings.ADMIN_HOST is set.

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from core.observability import capture_event, capture_exception, distinct_id_for

logger = logging.getLogger(__name__)


class AdminHostMiddleware:
    """Route each request to the admin-only or public-only URLconf by hostname.

    When ``settings.ADMIN_HOST`` is set, a request whose host matches it is
    served ``config.urls_admin`` (the Django admin only) and every other host is
    served ``config.urls_public`` (the public site, with no ``/admin/``). This
    is how the admin is confined to its own subdomain (ADR 0022).

    When ``ADMIN_HOST`` is empty — local development, the test suite, any
    single-host deployment — the middleware is a no-op and the default combined
    ``ROOT_URLCONF`` (``config.urls``) serves both the admin and the public site.

    ``request.urlconf`` must be set before URL resolution and before
    ``LocaleMiddleware`` / ``CommonMiddleware`` inspect it, so this middleware is
    registered early in ``MIDDLEWARE`` (see config/settings/base.py).
    ``settings.ADMIN_HOST`` is read on every request rather than cached in
    ``__init__`` so ``@override_settings`` works in tests.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Select the per-host URLconf, then defer to the rest of the chain."""
        admin_host = settings.ADMIN_HOST
        if admin_host:
            # get_host() strips nothing itself but validates against
            # ALLOWED_HOSTS; drop any :port before comparing to ADMIN_HOST.
            host = request.get_host().split(":", 1)[0]
            urlconf = (
                "config.urls_admin" if host == admin_host else "config.urls_public"
            )
            # django-stubs omits the dynamic urlconf attribute Django reads
            # during resolution; the assignment is valid at runtime.
            request.urlconf = urlconf  # type: ignore[attr-defined]
        return self.get_response(request)


class PostHogExceptionMiddleware:
    """Report unhandled view exceptions to PostHog, then let Django handle them.

    process_exception only reports; it returns None so Django's normal 500
    handling (and any downstream middleware) is unchanged. Reporting is
    best-effort and never raises into the request (see capture_exception).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pass the request through unchanged; capture happens in the hook."""
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> None:
        """Report the exception to PostHog and defer to Django's handling."""
        capture_exception(exception)
        return None


# URL namespaces whose views are never page-views, even when they return an
# HTML 200 GET. The Django admin and the DEBUG-only test-data panel are the only
# HTML-rendering surfaces that would otherwise pass every other gate: everything
# else non-page (robots.txt, healthz, sw.js, sitemap.xml, the application-form
# redirect) is already excluded by the Content-Type / status / method gates.
# `debug` is moot in production (its views 404 when DEBUG is false, and this
# middleware is production-only) but listed for clarity.
_PAGEVIEW_DENYLIST_NAMESPACES = frozenset({"admin", "debug"})


def _is_trackable_pageview(request: HttpRequest, response: HttpResponse) -> bool:
    """Return whether this request/response pair is a full-page content view.

    True only for a GET that resolved to a 200 HTML response, is not an HTMX
    partial swap, and does not resolve into an excluded namespace. See the
    module header for the rationale behind each gate.
    """
    resolver_match = request.resolver_match
    if (
        request.method != "GET"
        or response.status_code != 200
        or resolver_match is None
        or "HX-Request" in request.headers
    ):
        return False
    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("text/html"):
        return False
    return not _PAGEVIEW_DENYLIST_NAMESPACES.intersection(resolver_match.namespaces)


class PostHogPageviewMiddleware:
    """Send a best-effort server-side $pageview event for full-page views.

    Tracks every GET that resolves to a 200 HTML response, excluding HTMX
    partial swaps and the admin / debug namespaces (see
    ``_is_trackable_pageview``). Cookieless: the visitor is identified via
    ``core.observability.distinct_id_for`` (the user pk if authenticated,
    otherwise a salted anonymous hash), consistent with the Cookie Policy's
    no-tracking-cookies stance.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pass the request through, then best-effort track a full-page view.

        Always returns the downstream response — a failure while building or
        sending the event must never break the page it is reporting on.
        """
        response = self.get_response(request)

        try:
            if _is_trackable_pageview(request, response):
                capture_event(
                    distinct_id_for(request),
                    "$pageview",
                    {"$current_url": request.build_absolute_uri()},
                )
        except Exception:  # noqa: BLE001 — analytics must never break the response.
            logger.warning("Failed to track pageview", exc_info=True)

        return response
