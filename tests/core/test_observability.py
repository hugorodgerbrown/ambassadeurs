# Tests for core.observability — PostHog error monitoring (VERB-65) and
# product analytics (VERB-124).
#
# No django_db marker: these exercise pure helpers and mocked PostHog calls;
# they never touch the ORM.

from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from core.observability import (
    alias_identities,
    anonymous_distinct_id,
    capture_event,
    capture_exception,
    distinct_id_for,
    init_error_monitoring,
    scrub_pii,
)
from tests.accounts.factories import UserFactory

# ---------------------------------------------------------------------------
# scrub_pii — the before_send PII redaction hook
# ---------------------------------------------------------------------------


def test_scrub_pii_redacts_email_in_properties() -> None:
    """Email addresses inside event properties are replaced with a placeholder."""
    event = {
        "event": "$exception",
        "properties": {"message": "failed for ada@example.com while matching"},
    }
    scrubbed = scrub_pii(event)
    assert (
        scrubbed["properties"]["message"]
        == "failed for [email redacted] while matching"
    )


def test_scrub_pii_redacts_phone_in_properties() -> None:
    """Phone numbers (including the Swiss +41 form) inside properties are replaced."""
    event = {"event": "$exception", "properties": {"note": "call +41 79 000 88 88 now"}}
    scrubbed = scrub_pii(event)
    assert "+41" not in scrubbed["properties"]["note"]
    assert "[phone redacted]" in scrubbed["properties"]["note"]


def test_scrub_pii_preserves_envelope_fields() -> None:
    """The SDK envelope (event name, distinct_id, ISO timestamp) is left intact.

    Regression test for the ingestion failure where the broad phone pattern
    matched the date and time digit runs of an ISO-8601 ``timestamp``,
    corrupting it into ``[phone redacted]T…`` so PostHog rejected the whole
    batch ("non-engage request missing event name attribute").
    """
    event = {
        "event": "registration",
        "distinct_id": "42",
        "timestamp": "2026-07-07T11:08:10.123456+00:00",
        "properties": {"role": "AMBASSADOR"},
    }
    scrubbed = scrub_pii(event)
    assert scrubbed["event"] == "registration"
    assert scrubbed["distinct_id"] == "42"
    assert scrubbed["timestamp"] == "2026-07-07T11:08:10.123456+00:00"
    assert scrubbed["properties"]["role"] == "AMBASSADOR"


def test_scrub_pii_walks_nested_structures() -> None:
    """Nested dicts and lists are scrubbed recursively; scalars pass through."""
    event = {
        "properties": {
            "$exception_message": "boom for grace@example.com",
            "frames": ["+41790008888", "ok"],
            "count": 3,
        },
    }
    scrubbed = scrub_pii(event)
    assert scrubbed["properties"]["$exception_message"] == "boom for [email redacted]"
    assert scrubbed["properties"]["frames"][0] == "[phone redacted]"
    assert scrubbed["properties"]["frames"][1] == "ok"
    assert scrubbed["properties"]["count"] == 3


def test_scrub_pii_never_returns_none() -> None:
    """The hook always returns an event so monitoring is never silently off."""
    assert scrub_pii({}) == {}


# ---------------------------------------------------------------------------
# init_error_monitoring
# ---------------------------------------------------------------------------


def test_init_is_no_op_without_api_key() -> None:
    """With no POSTHOG_API_KEY configured, init is a no-op and returns False."""
    with patch("core.observability.config", return_value=""):
        assert init_error_monitoring() is False


def test_init_configures_posthog_when_key_present() -> None:
    """With a key, init sets the PII hook, enables autocapture, and disables
    local-variable capture (PII), then returns True.
    """
    values = {"POSTHOG_API_KEY": "phc_test", "POSTHOG_HOST": "https://ph.test"}

    def fake_config(key: str, default: str = "") -> str:
        return values.get(key, default)

    with (
        patch("core.observability.config", side_effect=fake_config),
        patch("core.observability.posthog") as mock_posthog,
    ):
        assert init_error_monitoring() is True
        assert mock_posthog.before_send is scrub_pii
        assert mock_posthog.enable_exception_autocapture is True
        assert mock_posthog.capture_exception_code_variables is False
        mock_posthog.setup.assert_called_once()


# ---------------------------------------------------------------------------
# capture_exception
# ---------------------------------------------------------------------------


def test_capture_exception_swallows_reporting_errors() -> None:
    """A failure in the PostHog client must not propagate into the caller."""
    with patch(
        "core.observability.posthog.capture_exception",
        side_effect=RuntimeError("network down"),
    ):
        # Must not raise.
        capture_exception(ValueError("boom"))


# ---------------------------------------------------------------------------
# capture_event (VERB-124)
# ---------------------------------------------------------------------------


def test_capture_event_sends_event_with_distinct_id_and_properties() -> None:
    """capture_event forwards the event, distinct_id, and properties to PostHog."""
    with patch("core.observability.posthog.capture") as mock_capture:
        capture_event("42", "registration", {"role": "AMBASSADOR"})

    mock_capture.assert_called_once_with(
        "registration", distinct_id="42", properties={"role": "AMBASSADOR"}
    )


def test_capture_event_defaults_properties_to_empty_dict() -> None:
    """With no properties given, capture_event passes an empty dict, not None."""
    with patch("core.observability.posthog.capture") as mock_capture:
        capture_event("42", "form_downloaded")

    mock_capture.assert_called_once_with(
        "form_downloaded", distinct_id="42", properties={}
    )


def test_capture_event_swallows_reporting_errors() -> None:
    """A failure in the PostHog client must not propagate into the caller."""
    with patch(
        "core.observability.posthog.capture",
        side_effect=RuntimeError("network down"),
    ):
        # Must not raise.
        capture_event("42", "registration")


# ---------------------------------------------------------------------------
# anonymous_distinct_id
# ---------------------------------------------------------------------------


def test_anonymous_distinct_id_is_deterministic_for_equal_ip_and_ua() -> None:
    """Two requests with the same IP and user-agent yield the same hash."""
    request_one = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="Mozilla/5.0"
    )
    request_two = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="Mozilla/5.0"
    )
    assert anonymous_distinct_id(request_one) == anonymous_distinct_id(request_two)


def test_anonymous_distinct_id_differs_when_ip_differs() -> None:
    """A different IP (same user-agent) yields a different hash."""
    request_one = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="Mozilla/5.0"
    )
    request_two = RequestFactory().get(
        "/", REMOTE_ADDR="5.6.7.8", HTTP_USER_AGENT="Mozilla/5.0"
    )
    assert anonymous_distinct_id(request_one) != anonymous_distinct_id(request_two)


def test_anonymous_distinct_id_differs_when_user_agent_differs() -> None:
    """A different user-agent (same IP) yields a different hash."""
    request_one = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="Mozilla/5.0"
    )
    request_two = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="curl/8.0"
    )
    assert anonymous_distinct_id(request_one) != anonymous_distinct_id(request_two)


def test_anonymous_distinct_id_never_contains_raw_ip() -> None:
    """The returned hash never leaks the raw IP address."""
    request = RequestFactory().get(
        "/", REMOTE_ADDR="203.0.113.42", HTTP_USER_AGENT="Mozilla/5.0"
    )
    result = anonymous_distinct_id(request)
    assert "203.0.113.42" not in result
    assert result.startswith("anon:")


# ---------------------------------------------------------------------------
# distinct_id_for
# ---------------------------------------------------------------------------


def test_distinct_id_for_returns_pk_for_authenticated_user(db: None) -> None:
    """An authenticated user is identified by their User.pk."""
    user = UserFactory.create()
    request = RequestFactory().get("/")
    request.user = user
    assert distinct_id_for(request) == str(user.pk)


def test_distinct_id_for_returns_anonymous_hash_for_anonymous_user() -> None:
    """An anonymous visitor is identified by the anonymous hash."""
    request = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="Mozilla/5.0"
    )
    request.user = AnonymousUser()
    assert distinct_id_for(request) == anonymous_distinct_id(request)


# ---------------------------------------------------------------------------
# alias_identities
# ---------------------------------------------------------------------------


def test_alias_identities_calls_posthog_alias_with_correct_ids(db: None) -> None:
    """alias_identities calls posthog.alias with the anon hash and the user pk."""
    user = UserFactory.create()
    request = RequestFactory().get(
        "/", REMOTE_ADDR="1.2.3.4", HTTP_USER_AGENT="Mozilla/5.0"
    )

    with patch("core.observability.posthog.alias") as mock_alias:
        alias_identities(request, user)

    mock_alias.assert_called_once_with(
        previous_id=anonymous_distinct_id(request), distinct_id=str(user.pk)
    )


def test_alias_identities_swallows_reporting_errors(db: None) -> None:
    """A failure in the PostHog client must not propagate into the caller."""
    user = UserFactory.create()
    request = RequestFactory().get("/")

    with patch(
        "core.observability.posthog.alias", side_effect=RuntimeError("network down")
    ):
        # Must not raise.
        alias_identities(request, user)
