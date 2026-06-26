# Tests for core.emails.normalise_email.

import pytest

from core.emails import normalise_email

pytestmark = pytest.mark.django_db


def test_normalise_email_lowercases() -> None:
    """normalise_email returns the address in lowercase."""
    assert normalise_email("Alice@Example.COM") == "alice@example.com"
    assert normalise_email("ALICE@EXAMPLE.COM") == "alice@example.com"


def test_normalise_email_strips_leading_trailing_whitespace() -> None:
    """normalise_email strips leading and trailing whitespace."""
    assert normalise_email("  alice@example.com  ") == "alice@example.com"
    assert normalise_email("\talice@example.com\t") == "alice@example.com"
    assert normalise_email("\nalice@example.com\n") == "alice@example.com"


def test_normalise_email_removes_null_byte() -> None:
    """normalise_email removes the NUL control character."""
    assert normalise_email("alice\x00@example.com") == "alice@example.com"


def test_normalise_email_removes_interior_control_chars() -> None:
    """normalise_email removes non-printable control characters anywhere."""
    # Interior tab (\t is not printable, so it is stripped out).
    assert normalise_email("alice\t@example.com") == "alice@example.com"
    # Embedded newline.
    assert normalise_email("alice\n@example.com") == "alice@example.com"
    # Carriage return.
    assert normalise_email("alice\r@example.com") == "alice@example.com"


def test_normalise_email_removes_non_printable_unicode() -> None:
    """normalise_email removes zero-width and non-printable Unicode code points."""
    # Zero-width space (U+200B) — not printable.
    assert normalise_email("alice​@example.com") == "alice@example.com"
    # Soft hyphen (U+00AD) — not printable.
    assert normalise_email("alice­@example.com") == "alice@example.com"


def test_normalise_email_leaves_clean_address_unchanged() -> None:
    """normalise_email is a no-op on an already-normalised address."""
    address = "alice@example.com"
    assert normalise_email(address) == address


def test_normalise_email_is_idempotent() -> None:
    """Applying normalise_email twice produces the same result as once."""
    inputs = [
        "  Alice@Example.COM  ",
        "alice\x00@example.com",
        "ALICE@EXAMPLE.COM",
        "alice@example.com",
        "\talice@example.com\n",
    ]
    for raw in inputs:
        once = normalise_email(raw)
        twice = normalise_email(once)
        assert once == twice, f"Not idempotent for {raw!r}"
