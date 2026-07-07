# Tests for core.middleware — PostHog exception reporting (VERB-65) and
# server-side page-view tracking (VERB-124).

from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from django.urls import ResolverMatch

from core.middleware import PostHogExceptionMiddleware, PostHogPageviewMiddleware


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


# ---------------------------------------------------------------------------
# PostHogPageviewMiddleware (VERB-124)
# ---------------------------------------------------------------------------


def _resolved_request(view_name: str, method: str = "GET") -> HttpRequest:
    """Build an anonymous request whose resolver_match.view_name is ``view_name``."""
    request = getattr(RequestFactory(), method.lower())("/")
    request.user = AnonymousUser()
    request.resolver_match = ResolverMatch(
        func=lambda: None, args=(), kwargs={}, url_name=view_name.split(":")[-1]
    )
    # ResolverMatch derives view_name from url_name plus the namespace; set it
    # directly to keep this test independent of URLconf wiring.
    request.resolver_match.view_name = view_name
    return request


def test_pageview_fires_for_each_allowlisted_view_name() -> None:
    """A GET/200 to every allowlisted view_name fires exactly one $pageview."""
    allowlisted_view_names = [
        "public:home",
        "public:register_role",
        "public:register_form",
        "public:register_email_sent",
        "public:register_confirm",
        "public:how_it_works",
        "public:faq",
        "public:legal",
        "accounts:detail",
    ]
    for view_name in allowlisted_view_names:
        request = _resolved_request(view_name)
        middleware = PostHogPageviewMiddleware(lambda req: HttpResponse(status=200))  # type: ignore[arg-type]

        with patch("core.middleware.capture_event") as mock_capture:
            middleware(request)

        mock_capture.assert_called_once()
        args, kwargs = mock_capture.call_args
        assert args[1] == "$pageview"


def test_pageview_does_not_fire_for_non_allowlisted_view() -> None:
    """A GET/200 to a view not in the allowlist fires nothing."""
    request = _resolved_request("public:tip_page")
    middleware = PostHogPageviewMiddleware(lambda req: HttpResponse(status=200))  # type: ignore[arg-type]

    with patch("core.middleware.capture_event") as mock_capture:
        middleware(request)

    mock_capture.assert_not_called()


def test_pageview_does_not_fire_for_non_200_response() -> None:
    """A GET to an allowlisted view that returns a non-200 fires nothing."""
    request = _resolved_request("public:home")
    middleware = PostHogPageviewMiddleware(lambda req: HttpResponse(status=404))  # type: ignore[arg-type]

    with patch("core.middleware.capture_event") as mock_capture:
        middleware(request)

    mock_capture.assert_not_called()


def test_pageview_does_not_fire_for_post_request() -> None:
    """A POST to an allowlisted view fires nothing, even on a 200 response."""
    request = _resolved_request("public:home", method="POST")
    middleware = PostHogPageviewMiddleware(lambda req: HttpResponse(status=200))  # type: ignore[arg-type]

    with patch("core.middleware.capture_event") as mock_capture:
        middleware(request)

    mock_capture.assert_not_called()


def test_pageview_does_not_fire_without_resolver_match() -> None:
    """A request with no resolver_match (e.g. a raw RequestFactory call) is safe."""
    request = RequestFactory().get("/")
    request.resolver_match = None
    middleware = PostHogPageviewMiddleware(lambda req: HttpResponse(status=200))  # type: ignore[arg-type]

    with patch("core.middleware.capture_event") as mock_capture:
        middleware(request)

    mock_capture.assert_not_called()


def test_pageview_raising_capture_does_not_break_response() -> None:
    """A raising capture_event must not prevent the response being returned."""
    request = _resolved_request("public:home")
    sentinel = HttpResponse(status=200)
    middleware = PostHogPageviewMiddleware(lambda req: sentinel)  # type: ignore[arg-type]

    with patch(
        "core.middleware.capture_event", side_effect=RuntimeError("network down")
    ):
        # Must not raise.
        result = middleware(request)

    assert result is sentinel
