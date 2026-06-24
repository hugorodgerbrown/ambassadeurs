# Tests for the email-verification and registration-confirmation signed-link
# tokens.

from accounts.tokens import (
    make_email_verification_token,
    make_registration_confirmation_token,
    read_email_verification_token,
    read_registration_confirmation_token,
)

# ---------------------------------------------------------------------------
# Email-verification tokens (original flow — retained for the social path)
# ---------------------------------------------------------------------------


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
# Registration confirmation tokens (VERB-24 combined-form flow)
# ---------------------------------------------------------------------------


def test_registration_confirmation_round_trip() -> None:
    """A freshly minted confirmation token reads back the same registration pk."""
    token = make_registration_confirmation_token(42)
    assert read_registration_confirmation_token(token) == 42


def test_registration_confirmation_tampered_token_is_rejected() -> None:
    """A tampered confirmation token returns None."""
    token = make_registration_confirmation_token(42)
    assert read_registration_confirmation_token(token + "x") is None


def test_registration_confirmation_expired_token_is_rejected() -> None:
    """A confirmation token past its max age returns None."""
    token = make_registration_confirmation_token(42)
    assert read_registration_confirmation_token(token, max_age=-1) is None


def test_verification_token_not_accepted_as_confirmation_token() -> None:
    """An email-verification token is rejected by the confirmation reader.

    The two token types use different salts, so they cannot be interchanged.
    """
    verify_token = make_email_verification_token("ada@example.com")
    assert read_registration_confirmation_token(verify_token) is None


def test_confirmation_token_not_accepted_as_verification_token() -> None:
    """A confirmation token is rejected by the verification reader (different salt)."""
    confirm_token = make_registration_confirmation_token(42)
    assert read_email_verification_token(confirm_token) is None
