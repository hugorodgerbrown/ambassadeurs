# Tests for core view decorators.

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory, override_settings

from core.decorators import require_debug, require_htmx


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


@override_settings(DEBUG=False)
def test_require_debug_raises_http404_in_production() -> None:
    """require_debug raises Http404 when settings.DEBUG is False."""
    request = RequestFactory().get("/debug/example/")
    with pytest.raises(Http404):
        require_debug(_view)(request)


@override_settings(DEBUG=True)
def test_require_debug_allows_request_in_debug_mode() -> None:
    """require_debug passes through to the wrapped view when DEBUG is True."""
    request = RequestFactory().get("/debug/example/")
    response = require_debug(_view)(request)
    assert response.status_code == 200
