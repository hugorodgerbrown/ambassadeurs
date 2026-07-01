# HTTP-layer middleware.
#
# PostHogExceptionMiddleware reports unhandled request exceptions to PostHog
# (VERB-65). Django catches request exceptions in its core handler and returns a
# 500 itself, so they never reach the interpreter excepthook that PostHog's
# autocapture hooks — process_exception is the reliable capture point for the
# web service. Management commands / crons are covered separately by
# enable_exception_autocapture (see core.observability).

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.observability import capture_exception


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
