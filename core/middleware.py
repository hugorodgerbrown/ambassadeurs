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
#
# MarketingSourceMiddleware normalises and stashes utm_*/click-id querystring
# params into the session (VERB-147, ADR 0023), replacing the upstream
# django-utm-tracker UtmSessionMiddleware so click-ID-only visits (e.g. bare
# "?fbclid=...") get a synthesised utm_source/utm_medium before the library's
# LeadSourceMiddleware persists them. Registered in base.py (all environments).
#
# MarkdownRepresentationMiddleware advertises and serves the .md twin of each
# content page (SKI-155, ADR 0026). It runs on the *response*, so it can reuse
# the HTML the view already rendered rather than rendering the page twice.
# Registered in base.py — unlike the PostHog pair, this one is part of the
# product surface, not observability, so it must behave identically everywhere.

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.translation import get_language
from utm_tracker.request import parse_qs
from utm_tracker.session import stash_utm_params

from core.markdown import MARKDOWN_CONTENT_TYPE, html_to_markdown
from core.marketing import normalise_source_params
from core.negotiation import accepts_nothing_we_serve, prefers_markdown
from core.observability import capture_event, capture_exception, distinct_id_for
from public.content import ContentPage, page_for_view

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


class MarketingSourceMiddleware:
    """Normalise then stash utm_*/click-id querystring params into the session.

    Combines two steps into one so the session — and therefore the downstream
    ``LeadSource`` row the library's ``LeadSourceMiddleware`` persists — always
    carries a ``utm_source``/``utm_medium`` pair, even for a click-ID-only visit
    (e.g. organic Facebook traffic sharing a bare ``?fbclid=...`` link, our main
    channel). Without normalisation such a visit would carry a click ID but no
    ``utm_source`` and the library would silently drop it (VERB-147, ADR 0023).

    This middleware **replaces**
    ``utm_tracker.middleware.UtmSessionMiddleware`` in the stack — do not
    register both.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Stash the (possibly normalised) querystring params, then continue."""
        stash_utm_params(request.session, normalise_source_params(parse_qs(request)))
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
                    {
                        "$current_url": request.build_absolute_uri(),
                        # Language is implicit in the URL since SKI-153
                        # (/faq/ vs /fr/faq/), but only as a substring. Sending
                        # it as its own property makes "how is the French site
                        # doing?" a one-click breakdown rather than a regex in
                        # every query. LocaleMiddleware has already resolved it
                        # from the URL prefix by the time this runs, so it is
                        # exactly the language the page rendered in. Not PII.
                        "language": get_language(),
                    },
                )
        except Exception:  # noqa: BLE001 — analytics must never break the response.
            logger.warning("Failed to track pageview", exc_info=True)

        return response


class MarkdownRepresentationMiddleware:
    """Advertise and serve the Markdown twin of each public content page.

    Two jobs, both on the response side of a content page:

    1. **Advertise.** Add ``Link: <...faq.md>; rel="alternate";
       type="text/markdown"`` and ``Vary: Accept``. The matching
       ``<link rel="alternate">`` tag is emitted in the template head — both are
       needed, because a DOM-parsing crawler reads the tag while a headless
       fetcher may only ever see the headers.

    2. **Negotiate.** If the client asked for Markdown in preference to HTML,
       replace the body with the converted Markdown at the *same* URL. If it
       accepts neither type, return 406 rather than silently handing back
       something it said it did not want.

    Serving two representations of one resource, with ``Vary: Accept``
    declared, is ordinary HTTP content negotiation — not cloaking, which is
    serving crawlers *different content* from users. The Markdown is a
    conversion of the very bytes the browser would have received.

    Running on the response lets it reuse the HTML the view already produced,
    so a negotiated request renders the page once, not twice.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Add the alternate-representation headers and negotiate the type."""
        response = self.get_response(request)

        page = self._content_page_for(request)
        if page is None:
            return response

        accept = request.headers.get("Accept", "")
        if accepts_nothing_we_serve(accept):
            # 406 rather than a silent substitution: a client that excluded both
            # of our representations has a bug worth surfacing, and guessing on
            # its behalf hides it.
            return HttpResponse(status=406)

        markdown_url = request.build_absolute_uri(f"/{page.slug}.md")
        response["Vary"] = "Accept"

        if not prefers_markdown(accept):
            response["Link"] = (
                f'<{markdown_url}>; rel="alternate"; type="text/markdown"'
            )
            return response

        markdown = html_to_markdown(
            response.content.decode(), request.build_absolute_uri("/")
        )
        negotiated = HttpResponse(markdown, content_type=MARKDOWN_CONTENT_TYPE)
        negotiated["Vary"] = "Accept"
        negotiated["Link"] = (
            f"<{request.build_absolute_uri(page.path)}>; "
            'rel="alternate"; type="text/html"'
        )
        return negotiated

    def _content_page_for(self, request: HttpRequest) -> ContentPage | None:
        """Return the content page this request rendered, if it is one.

        Only GET/HEAD requests that resolved to a 200 HTML response qualify. A
        redirect, an error page, or a POST has no meaningful Markdown twin, and
        converting one would produce a document that misrepresents the page.
        """
        match = request.resolver_match
        if match is None or request.method not in {"GET", "HEAD"}:
            return None
        return page_for_view(match.view_name, match.kwargs)
