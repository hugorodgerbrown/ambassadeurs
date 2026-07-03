# Tests for core service functions.

from core.services import sanitise_notification_html

# ---------------------------------------------------------------------------
# sanitise_notification_html
# ---------------------------------------------------------------------------


def test_sanitise_notification_html_strips_script_tag() -> None:
    """A <script> tag and its content are removed entirely."""
    result = sanitise_notification_html("<script>alert(1)</script>hello")
    assert "<script>" not in result
    assert "alert(1)" not in result
    assert "hello" in result


def test_sanitise_notification_html_strips_onerror_attribute() -> None:
    """An onerror event-handler attribute is stripped from the tag."""
    result = sanitise_notification_html('<b onerror="alert(1)">bold text</b>')
    assert "onerror" not in result
    assert "bold text" in result


def test_sanitise_notification_html_strips_onclick_attribute() -> None:
    """An onclick event-handler attribute is stripped from the tag."""
    result = sanitise_notification_html(
        '<a href="https://example.com" onclick="bad()">click me</a>'
    )
    assert "onclick" not in result
    assert "click me" in result


def test_sanitise_notification_html_allows_https_link() -> None:
    """An <a href="https://..."> anchor survives with its href intact."""
    result = sanitise_notification_html('<a href="https://example.com">link</a>')
    assert 'href="https://example.com"' in result
    assert "link" in result


def test_sanitise_notification_html_allows_mailto_link() -> None:
    """A mailto: href survives — mailto is in the allowed schemes."""
    result = sanitise_notification_html('<a href="mailto:info@example.com">mail</a>')
    assert 'href="mailto:info@example.com"' in result


def test_sanitise_notification_html_drops_javascript_scheme() -> None:
    """A javascript: href is dropped — only http/https/mailto are allowed."""
    result = sanitise_notification_html('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in result


def test_sanitise_notification_html_allows_plain_text() -> None:
    """Plain text with no markup passes through unchanged."""
    result = sanitise_notification_html("Registration opens July 31st.")
    assert result == "Registration opens July 31st."
