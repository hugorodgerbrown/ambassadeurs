# Tests for core.observability — PostHog error monitoring (VERB-65).
#
# No django_db marker: these exercise pure helpers and mocked PostHog calls;
# they never touch the ORM.

from unittest.mock import patch

from core.observability import (
    capture_exception,
    init_error_monitoring,
    scrub_pii,
)

# ---------------------------------------------------------------------------
# scrub_pii — the before_send PII redaction hook
# ---------------------------------------------------------------------------


def test_scrub_pii_redacts_email() -> None:
    """Email addresses are replaced with a placeholder."""
    event = {"message": "failed for ada@example.com while matching"}
    assert scrub_pii(event) == {"message": "failed for [email redacted] while matching"}


def test_scrub_pii_redacts_phone() -> None:
    """Phone numbers (including the Swiss +41 form) are replaced."""
    scrubbed = scrub_pii("call +41 79 000 88 88 now")
    assert "+41" not in scrubbed
    assert "[phone redacted]" in scrubbed


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
