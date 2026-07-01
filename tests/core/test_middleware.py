# Tests for core.middleware — PostHog exception reporting (VERB-65).

from unittest.mock import patch

from django.test import RequestFactory

from core.middleware import PostHogExceptionMiddleware


def test_process_exception_reports_and_returns_none() -> None:
    """process_exception reports the exception to PostHog and returns None so
    Django's own 500 handling is unchanged.
    """
    request = RequestFactory().get("/")
    exc = ValueError("boom")
    middleware = PostHogExceptionMiddleware(lambda req: None)  # type: ignore[arg-type,return-value]

    with patch("core.middleware.capture_exception") as mock_capture:
        result = middleware.process_exception(request, exc)

    assert result is None
    mock_capture.assert_called_once_with(exc)


def test_call_passes_request_through() -> None:
    """The middleware returns the downstream response unchanged."""
    sentinel = object()
    middleware = PostHogExceptionMiddleware(lambda req: sentinel)  # type: ignore[arg-type,return-value]
    assert middleware(RequestFactory().get("/")) is sentinel
