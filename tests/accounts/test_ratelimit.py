# Rate-limit tests for the accounts login_request view (VERB-66).
#
# RATELIMIT_ENABLE is False in development.py (to avoid contaminating the
# rest of the test suite). Each test here re-enables it via the module-level
# pytestmark. The autouse fixture clears the cache before each test so
# counters from previous tests do not carry over.

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_ratelimit_cache() -> None:
    """Clear the cache before each test so rate-limit counters do not leak."""
    cache.clear()


# ---------------------------------------------------------------------------
# login_request — IP limit
# ---------------------------------------------------------------------------

# Use a dedicated test IP that no other test file uses, to avoid counter
# contamination from the default 127.0.0.1 used by Client() elsewhere.
_IP_LIMIT_TEST_ADDR = "192.0.2.1"


@override_settings(RATELIMIT_ENABLE=True)
def test_login_request_post_ip_limit_triggers_429() -> None:
    """After 20 POSTs from the same IP the 21st returns 429.

    Each request uses a distinct email so the per-email limit (5/h) does not
    fire before the IP limit (20/h) is reached.
    """
    url = reverse("accounts:login")
    client = Client(REMOTE_ADDR=_IP_LIMIT_TEST_ADDR)
    for i in range(20):
        response = client.post(url, {"email": f"ip-limit-{i}@example.com"})
        assert response.status_code == 302

    # The 21st request must be rate-limited.
    response = client.post(url, {"email": "ip-limit-20@example.com"})
    assert response.status_code == 429


@override_settings(RATELIMIT_ENABLE=True)
def test_login_request_post_ip_limit_429_contains_message() -> None:
    """The 429 response body contains the rate-limit message (English source)."""
    url = reverse("accounts:login")
    client = Client(REMOTE_ADDR=_IP_LIMIT_TEST_ADDR)
    for i in range(20):
        client.post(url, {"email": f"ip-msg-{i}@example.com"})

    response = client.post(url, {"email": "ip-msg-20@example.com"})
    assert response.status_code == 429
    assert b"Too many attempts" in response.content


# ---------------------------------------------------------------------------
# login_request — per-email limit
# ---------------------------------------------------------------------------


@override_settings(RATELIMIT_ENABLE=True)
def test_login_request_post_email_limit_triggers_429() -> None:
    """After 5 POSTs for the same email the 6th returns 429."""
    url = reverse("accounts:login")
    # Use different clients to avoid the IP limit interfering.
    email = "test-ratelimit@example.com"
    for i in range(5):
        client = Client(REMOTE_ADDR=f"10.0.0.{i + 1}")
        response = client.post(url, {"email": email})
        assert response.status_code == 302

    # 6th attempt — same email, different IP.
    client = Client(REMOTE_ADDR="10.0.0.99")
    response = client.post(url, {"email": email})
    assert response.status_code == 429


# ---------------------------------------------------------------------------
# login_request — GET is never rate-limited
# ---------------------------------------------------------------------------

_IP_GET_TEST_ADDR = "192.0.2.3"


@override_settings(RATELIMIT_ENABLE=True)
def test_login_request_get_never_rate_limited() -> None:
    """GET requests to login_request are never rate-limited (limit is POST-only)."""
    url = reverse("accounts:login")
    client = Client(REMOTE_ADDR=_IP_GET_TEST_ADDR)
    for _ in range(25):
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# login_request — HTMX 429 response is a fragment
# ---------------------------------------------------------------------------

_IP_HTMX_TEST_ADDR = "192.0.2.2"


@override_settings(RATELIMIT_ENABLE=True)
def test_login_request_rate_limited_htmx_returns_fragment() -> None:
    """An HTMX POST that is rate-limited returns a small 429 fragment (no full page)."""
    url = reverse("accounts:login")
    client = Client(REMOTE_ADDR=_IP_HTMX_TEST_ADDR)
    for i in range(20):
        client.post(url, {"email": f"htmx-{i}@example.com"})

    response = client.post(
        url,
        {"email": "htmx-20@example.com"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 429
    # Fragment must not include the base.html structural elements.
    assert b"<!DOCTYPE html>" not in response.content
    assert b"Too many attempts" in response.content
