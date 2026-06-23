# Tests for core view decorators.

from django.http import HttpResponse
from django.test import RequestFactory

from core.decorators import require_htmx


def _view(request: object) -> HttpResponse:
    """Trivial wrapped view returning 200."""
    return HttpResponse("ok")


def test_require_htmx_rejects_non_htmx_request() -> None:
    """A plain (non-HTMX) request is rejected with a 400."""
    request = RequestFactory().get("/partials/example/")
    request.htmx = False
    response = require_htmx(_view)(request)
    assert response.status_code == 400


def test_require_htmx_allows_htmx_request() -> None:
    """An HTMX request passes through to the wrapped view."""
    request = RequestFactory().get("/partials/example/")
    request.htmx = True
    response = require_htmx(_view)(request)
    assert response.status_code == 200
