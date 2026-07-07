# Rate-limit tests for the public registration view (VERB-66).
#
# RATELIMIT_ENABLE is False in development.py (to avoid contaminating the
# rest of the test suite). Each test here re-enables it via @override_settings.
# The autouse fixture clears the cache before each test so counters from
# previous tests do not carry over.
#
# Registration open settings are inherited from the dev defaults
# (REGISTRATION_OPENS_AT=2020-01-01, REGISTRATION_CLOSES_AT=2099-12-31).
#
# The registration form is now role-hardwired at /register/<role>/
# (register_form, VERB-131); the ratelimit decorators live on that view.

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_ratelimit_cache() -> None:
    """Clear the cache before each test so rate-limit counters do not leak."""
    cache.clear()


def _register_post_data(email: str = "test@example.com") -> dict[str, str]:
    """Return minimal valid POST data for the registration form.

    Uses ``ambassador`` role and minimal required fields. The form validates
    server-side; for rate-limit tests we only need the request to reach the
    POST branch before the form is validated.
    """
    return {
        "role": "ambassador",
        "email": email,
        "first_name": "Test",
        "last_name": "User",
        "prior_pass": "SEASONAL",
        "phone": "",
        "preferred_location": "",
        "preferred_language": "en",
        "nationality": "CH",
        "terms_accuracy": "on",
        "terms_rules": "on",
        "terms_privacy": "on",
    }


# ---------------------------------------------------------------------------
# register — IP limit
# ---------------------------------------------------------------------------

# Use dedicated test IPs that no other test file uses, to avoid counter
# contamination from the default 127.0.0.1 used by Client() elsewhere.
_IP_LIMIT_TEST_ADDR = "192.0.2.10"
_IP_LIMIT_MSG_ADDR = "192.0.2.11"
_IP_LIMIT_HTMX_ADDR = "192.0.2.12"
_IP_GET_TEST_ADDR = "192.0.2.13"


@override_settings(RATELIMIT_ENABLE=True)
def test_register_post_ip_limit_triggers_429() -> None:
    """After 30 POSTs from the same IP the 31st returns 429."""
    url = reverse("public:register_form", kwargs={"role": "ambassador"})
    client = Client(REMOTE_ADDR=_IP_LIMIT_TEST_ADDR)
    for i in range(30):
        # Vary the email so the per-email counter does not trigger first.
        client.post(url, _register_post_data(email=f"ip-test-{i}@example.com"))

    response = client.post(url, _register_post_data(email="ip-test-final@example.com"))
    assert response.status_code == 429


@override_settings(RATELIMIT_ENABLE=True)
def test_register_post_ip_limit_429_contains_message() -> None:
    """The 429 response body contains the rate-limit message (English source)."""
    url = reverse("public:register_form", kwargs={"role": "ambassador"})
    client = Client(REMOTE_ADDR=_IP_LIMIT_MSG_ADDR)
    for i in range(30):
        client.post(url, _register_post_data(email=f"ip-msg-{i}@example.com"))

    response = client.post(url, _register_post_data(email="ip-msg-final@example.com"))
    assert response.status_code == 429
    assert b"Too many attempts" in response.content


# ---------------------------------------------------------------------------
# register — per-email limit
# ---------------------------------------------------------------------------


@override_settings(RATELIMIT_ENABLE=True)
def test_register_post_email_limit_triggers_429() -> None:
    """After 5 POSTs for the same email the 6th returns 429."""
    url = reverse("public:register_form", kwargs={"role": "ambassador"})
    email = "email-ratelimit@example.com"
    for i in range(5):
        # Vary the IP to avoid the per-IP limit.
        client = Client(REMOTE_ADDR=f"10.1.0.{i + 1}")
        client.post(url, _register_post_data(email=email))

    client = Client(REMOTE_ADDR="10.1.0.99")
    response = client.post(url, _register_post_data(email=email))
    assert response.status_code == 429


# ---------------------------------------------------------------------------
# register — GET is never rate-limited
# ---------------------------------------------------------------------------


@override_settings(RATELIMIT_ENABLE=True)
def test_register_get_never_rate_limited() -> None:
    """GET requests to register_form are never rate-limited (limit is POST-only)."""
    url = reverse("public:register_form", kwargs={"role": "ambassador"})
    client = Client(REMOTE_ADDR=_IP_GET_TEST_ADDR)
    for _ in range(35):
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# register — HTMX 429 response is a fragment
# ---------------------------------------------------------------------------


@override_settings(RATELIMIT_ENABLE=True)
def test_register_rate_limited_htmx_returns_fragment() -> None:
    """An HTMX POST that is rate-limited returns a 429 fragment (no full page)."""
    url = reverse("public:register_form", kwargs={"role": "ambassador"})
    client = Client(REMOTE_ADDR=_IP_LIMIT_HTMX_ADDR)
    for i in range(30):
        client.post(url, _register_post_data(email=f"htmx-{i}@example.com"))

    response = client.post(
        url,
        _register_post_data(email="htmx-final@example.com"),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 429
    assert b"<!DOCTYPE html>" not in response.content
    assert b"Too many attempts" in response.content
