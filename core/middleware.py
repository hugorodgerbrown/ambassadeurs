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
# a small allowlist of GET/200 views — cookieless, consistent with the Cookie
# Policy's no-tracking-cookies stance. Both middlewares are production-only
# (registered in config/settings/production.py, not base.py).

from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.observability import capture_event, capture_exception, distinct_id_for

logger = logging.getLogger(__name__)


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


# The allowlisted view names for server-side page-view tracking (VERB-124).
# Deliberately small and explicit — every entry is a genuine content page, not
# an HTMX partial or a form-processing endpoint.
_PAGEVIEW_ALLOWLIST = frozenset(
    {
        "public:home",
        # Registration is a two-step journey since VERB-131: the role chooser
        # (register_role, /register/role/) followed by the role-hardwired form
        # (register_form, /register/<role>/). "public:register" is deliberately
        # absent — it is now a 302 back-compat redirect that never returns a
        # 200 GET, so it could never fire a page-view.
        "public:register_role",
        "public:register_form",
        "public:register_email_sent",
        "public:register_confirm",
        "public:how_it_works",
        "public:faq",
        "public:legal",
        "accounts:detail",
    }
)


class PostHogPageviewMiddleware:
    """Send a best-effort server-side $pageview event for allowlisted views.

    Fires only for a GET request that resolved to a 200 response against one
    of ``_PAGEVIEW_ALLOWLIST``'s view names — never for a POST, a non-200, or
    an unlisted view. Cookieless: the visitor is identified via
    ``core.observability.distinct_id_for`` (the user pk if authenticated,
    otherwise a salted anonymous hash), consistent with the Cookie Policy's
    no-tracking-cookies stance.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pass the request through, then best-effort track an allowlisted view.

        Always returns the downstream response — a failure while building or
        sending the event must never break the page it is reporting on.
        """
        response = self.get_response(request)

        try:
            if (
                request.method == "GET"
                and response.status_code == 200
                and request.resolver_match is not None
                and request.resolver_match.view_name in _PAGEVIEW_ALLOWLIST
            ):
                capture_event(
                    distinct_id_for(request),
                    "$pageview",
                    {"$current_url": request.build_absolute_uri()},
                )
        except Exception:  # noqa: BLE001 — analytics must never break the response.
            logger.warning("Failed to track pageview", exc_info=True)

        return response
