# Tests for the email-verification signed-link tokens.

from accounts.tokens import (
    make_email_verification_token,
    read_email_verification_token,
)


def test_round_trip_returns_lowercased_email() -> None:
    """A freshly minted token reads back as the lowercased email."""
    token = make_email_verification_token("ADA@Example.com")
    assert read_email_verification_token(token) == "ada@example.com"


def test_tampered_token_is_rejected() -> None:
    """A tampered token does not validate."""
    token = make_email_verification_token("ada@example.com")
    assert read_email_verification_token(token + "x") is None


def test_expired_token_is_rejected() -> None:
    """A token past its max age does not validate."""
    token = make_email_verification_token("ada@example.com")
    assert read_email_verification_token(token, max_age=-1) is None
