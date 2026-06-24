# Tests for the email-verification and match-access signed-link tokens.

from accounts.tokens import (
    make_email_verification_token,
    make_match_access_token,
    read_email_verification_token,
    read_match_access_token,
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


# ---------------------------------------------------------------------------
# make_match_access_token / read_match_access_token
# ---------------------------------------------------------------------------


def test_match_access_token_round_trip() -> None:
    """A freshly minted match-access token reads back as (match_pk, registration_pk)."""
    token = make_match_access_token(42, 7)
    result = read_match_access_token(token)
    assert result == (42, 7)


def test_match_access_token_tampered_is_rejected() -> None:
    """A tampered match-access token returns None."""
    token = make_match_access_token(1, 1)
    assert read_match_access_token(token + "x") is None


def test_match_access_token_expired_is_rejected() -> None:
    """A match-access token past its max_age returns None."""
    token = make_match_access_token(1, 1)
    assert read_match_access_token(token, max_age=-1) is None


def test_match_access_token_rejects_email_verification_token() -> None:
    """An email-verification token is rejected by read_match_access_token (wrong salt)."""
    email_token = make_email_verification_token("ada@example.com")
    assert read_match_access_token(email_token) is None


def test_email_verification_token_rejects_match_access_token() -> None:
    """A match-access token is rejected by read_email_verification_token (wrong salt)."""
    match_token = make_match_access_token(1, 1)
    assert read_email_verification_token(match_token) is None
