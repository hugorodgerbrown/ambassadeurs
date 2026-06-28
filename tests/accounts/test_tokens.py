# Tests for the registration-confirmation, login, and match-access signed-link tokens.

from accounts.tokens import (
    make_login_token,
    make_match_access_token,
    make_registration_confirmation_token,
    read_login_token,
    read_match_access_token,
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


def test_match_access_token_rejects_registration_confirmation_token() -> None:
    """A registration-confirmation token is rejected by read_match_access_token.

    The salt mismatch prevents cross-purpose token replay (Invariant 6).
    """
    confirm_token = make_registration_confirmation_token(42)
    assert read_match_access_token(confirm_token) is None


def test_registration_confirmation_token_rejects_match_access_token() -> None:
    """A match-access token is rejected by read_registration_confirmation_token.

    The salt mismatch prevents cross-purpose token replay (Invariant 6).
    """
    match_token = make_match_access_token(1, 1)
    assert read_registration_confirmation_token(match_token) is None


# ---------------------------------------------------------------------------
# Login tokens (VERB-46 magic-link login)
# ---------------------------------------------------------------------------


def test_login_token_round_trip() -> None:
    """A freshly minted login token reads back the same user pk."""
    token = make_login_token(99)
    assert read_login_token(token) == 99


def test_login_token_expired_is_rejected() -> None:
    """A login token past its max_age returns None."""
    token = make_login_token(99)
    assert read_login_token(token, max_age=-1) is None


def test_login_token_tampered_is_rejected() -> None:
    """A tampered login token returns None."""
    token = make_login_token(99)
    assert read_login_token(token + "x") is None


def test_login_token_rejects_registration_confirmation_token() -> None:
    """A registration-confirmation token is rejected by read_login_token.

    The salt mismatch prevents cross-purpose token replay (Invariant 6).
    """
    confirm_token = make_registration_confirmation_token(42)
    assert read_login_token(confirm_token) is None


def test_registration_confirmation_token_rejects_login_token() -> None:
    """A login token is rejected by read_registration_confirmation_token.

    The salt mismatch prevents cross-purpose token replay (Invariant 6).
    """
    login_token = make_login_token(99)
    assert read_registration_confirmation_token(login_token) is None
