# Tests for core.middleware — PostHog exception reporting (VERB-65),
# server-side page-view tracking (VERB-124), and admin-subdomain routing
# (ADR 0022).

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import ResolverMatch

from core.middleware import (
    AdminHostMiddleware,
    PostHogExceptionMiddleware,
    PostHogPageviewMiddleware,
)


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


# ---------------------------------------------------------------------------
# AdminHostMiddleware (ADR 0022)
# ---------------------------------------------------------------------------

_ADMIN_HOST = "admin.example.test"
_PUBLIC_HOST = "public.example.test"


def _urlconf_for(host: str) -> str | None:
    """Run AdminHostMiddleware for a request from ``host``; return request.urlconf.

    ``get_response`` is a no-op returning an empty response — the middleware sets
    ``request.urlconf`` on the passed-in request, which we read back afterwards.
    """
    request = RequestFactory().get("/")
    request.META["HTTP_HOST"] = host
    middleware = AdminHostMiddleware(lambda req: HttpResponse())
    middleware(request)
    return getattr(request, "urlconf", None)


@override_settings(ADMIN_HOST="", ALLOWED_HOSTS=[_ADMIN_HOST, _PUBLIC_HOST])
def test_admin_host_unset_leaves_urlconf_untouched() -> None:
    """With ADMIN_HOST empty the middleware is a no-op — no urlconf override."""
    assert _urlconf_for(_ADMIN_HOST) is None


@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST, _PUBLIC_HOST])
def test_admin_host_selects_admin_urlconf() -> None:
    """A request to the admin host is routed to the admin-only URLconf."""
    assert _urlconf_for(_ADMIN_HOST) == "config.urls_admin"


@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST, _PUBLIC_HOST])
def test_non_admin_host_selects_public_urlconf() -> None:
    """Any host other than the admin host is routed to the public-only URLconf."""
    assert _urlconf_for(_PUBLIC_HOST) == "config.urls_public"


@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST])
def test_admin_host_match_ignores_port() -> None:
    """The host:port form still matches the bare ADMIN_HOST."""
    assert _urlconf_for(f"{_ADMIN_HOST}:8000") == "config.urls_admin"


@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST])
def test_admin_host_middleware_returns_response() -> None:
    """The middleware returns the downstream response unchanged."""
    sentinel = HttpResponse()
    request = RequestFactory().get("/")
    request.META["HTTP_HOST"] = _ADMIN_HOST
    middleware = AdminHostMiddleware(lambda req: sentinel)
    assert middleware(request) is sentinel


@pytest.mark.django_db
@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST, _PUBLIC_HOST])
def test_admin_served_at_root_of_admin_host(client: Client) -> None:
    """On the admin host the admin index is at '/', redirecting anon to login."""
    response = client.get("/", HTTP_HOST=_ADMIN_HOST)
    assert response.status_code == 302
    assert "/login/" in response["Location"]


@pytest.mark.django_db
@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST, _PUBLIC_HOST])
def test_healthz_reachable_on_admin_host(client: Client) -> None:
    """healthz is mounted before the admin, so a request to it is not swallowed."""
    assert client.get("/healthz/", HTTP_HOST=_ADMIN_HOST).status_code == 200


@pytest.mark.django_db
@override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=[_ADMIN_HOST, _PUBLIC_HOST])
def test_public_host_serves_home_and_hides_admin(client: Client) -> None:
    """The public host serves the home page and 404s the old /admin/ path."""
    assert client.get("/", HTTP_HOST=_PUBLIC_HOST).status_code == 200
    assert client.get("/admin/", HTTP_HOST=_PUBLIC_HOST).status_code == 404
