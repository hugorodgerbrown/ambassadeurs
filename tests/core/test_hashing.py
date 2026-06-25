# Tests for core.hashing.hash_email.

import pytest
from django.test import override_settings

from core.hashing import hash_email

pytestmark = pytest.mark.django_db


def test_hash_email_is_deterministic() -> None:
    """hash_email returns the same digest for the same input."""
    result1 = hash_email("alice@example.com")
    result2 = hash_email("alice@example.com")
    assert result1 == result2


def test_hash_email_returns_64_char_hex() -> None:
    """hash_email returns a 64-character lowercase hex string (SHA-256 digest)."""
    result = hash_email("alice@example.com")
    assert len(result) == 64
    assert result == result.lower()
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_email_normalises_case() -> None:
    """hash_email produces the same digest regardless of email casing."""
    lower = hash_email("alice@example.com")
    upper = hash_email("ALICE@EXAMPLE.COM")
    mixed = hash_email("Alice@Example.COM")
    assert lower == upper == mixed


def test_hash_email_strips_whitespace() -> None:
    """hash_email strips leading and trailing whitespace before hashing."""
    clean = hash_email("alice@example.com")
    padded = hash_email("  alice@example.com  ")
    assert clean == padded


def test_hash_email_differs_for_different_addresses() -> None:
    """Different email addresses produce different digests."""
    assert hash_email("alice@example.com") != hash_email("bob@example.com")


@override_settings(EMAIL_HASH_SECRET="secret-a")
def test_hash_email_differs_under_different_secret() -> None:
    """hash_email uses the configured secret; changing it changes the digest."""
    digest_a = hash_email("alice@example.com")

    with override_settings(EMAIL_HASH_SECRET="secret-b"):
        digest_b = hash_email("alice@example.com")

    assert digest_a != digest_b


@override_settings(EMAIL_HASH_SECRET="test-known-vector-secret")
def test_hash_email_known_vector() -> None:
    """hash_email produces a stable, pre-computed digest for a known input.

    This locks down the HMAC-SHA256 computation so a future refactor cannot
    silently change the digest algorithm and break prior-decline history lookups.

    The expected value was computed with:
        import hashlib, hmac
        hmac.new(b"test-known-vector-secret", b"alice@example.com",
                 hashlib.sha256).hexdigest()
    """
    import hashlib
    import hmac

    expected = hmac.new(
        b"test-known-vector-secret",
        b"alice@example.com",
        hashlib.sha256,
    ).hexdigest()
    assert hash_email("alice@example.com") == expected
