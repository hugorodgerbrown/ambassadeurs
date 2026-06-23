# View decorators shared across apps.

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest


def require_htmx(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Reject non-HTMX requests to a fragment view with a 400.

    HTMX partial endpoints return inner-HTML snippets and must never be reached
    by a plain browser navigation (CLAUDE.md invariant 7). Relies on
    ``django_htmx`` populating ``request.htmx``.
    """

    def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        """Return the view response only for HTMX requests."""
        if not getattr(request, "htmx", False):
            return HttpResponseBadRequest("This endpoint requires HTMX.")
        return view(request, *args, **kwargs)

    return wrapper
