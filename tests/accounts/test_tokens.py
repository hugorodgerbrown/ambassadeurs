# Tests for the registration-confirmation signed-link token.

from accounts.tokens import (
    make_registration_confirmation_token,
    read_registration_confirmation_token,
)


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
