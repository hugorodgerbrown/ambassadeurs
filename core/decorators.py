# View decorators shared across apps.

import functools
from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest


def require_debug(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Raise Http404 for any request when ``settings.DEBUG`` is false.

    DEBUG-only views (e.g. the test-data panel) must be unreachable in
    production. Using a decorator — rather than a conditional URL include —
    allows the guard to be toggled in tests via
    ``override_settings(DEBUG=False)`` without re-importing the URL conf.
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Return the view response only when DEBUG is enabled."""
        if not settings.DEBUG:
            raise Http404("This endpoint is only available in development.")
        return view(request, *args, **kwargs)

    return wrapper


def require_htmx(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Reject non-HTMX requests to a fragment view with a 400.

    HTMX partial endpoints return inner-HTML snippets and must never be reached
    by a plain browser navigation (CLAUDE.md invariant 7). Relies on
    ``django_htmx`` populating ``request.htmx``.
    """

    @functools.wraps(view)
    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Return the view response only for HTMX requests."""
        if not getattr(request, "htmx", False):
            return HttpResponseBadRequest("This endpoint requires HTMX.")
        return view(request, *args, **kwargs)

    return wrapper
