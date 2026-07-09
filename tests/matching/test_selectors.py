# Tests for the matching presentation/context selectors.

from datetime import UTC, datetime

import pytest

from matching.models import Match, Registration
from matching.selectors import match_status_context, status_pill_for
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# status_pill_for (VERB-116)
# ---------------------------------------------------------------------------


def test_status_pill_for_no_registration() -> None:
    """No registration renders a neutral 'Queued' (muted) pill."""
    assert status_pill_for(None, "none") == {"label": "Queued", "tone": "muted"}


@pytest.mark.parametrize(
    ("status", "label", "tone"),
    [
        (Registration.Status.UNVERIFIED, "Unverified", "muted"),
        (Registration.Status.VERIFIED, "Queued", "muted"),
        (Registration.Status.PAUSED, "Paused", "muted"),
        (Registration.Status.WITHDRAWN, "Withdrawn", "muted"),
        (Registration.Status.SUSPENDED, "Suspended", "muted"),
    ],
)
def test_status_pill_for_each_registration_status(
    status: str, label: str, tone: str
) -> None:
    """Each Registration.Status (no active match) maps to its own pill."""
    registration = RegistrationFactory.create(status=status)
    assert status_pill_for(registration, "none") == {"label": label, "tone": tone}


@pytest.mark.parametrize(
    ("match_state", "label", "tone"),
    [
        ("proposed", "Pending", "wait"),
        ("pending", "Pending", "wait"),
        ("accepted", "Accepted", "done"),
    ],
)
def test_status_pill_for_active_match_state_overrides_registration_status(
    match_state: str, label: str, tone: str
) -> None:
    """An active match_state overrides the pill regardless of Registration.Status.

    Exercised against every Registration.Status to confirm the override always
    wins, not just for VERIFIED.
    """
    for status in (
        Registration.Status.UNVERIFIED,
        Registration.Status.VERIFIED,
        Registration.Status.PAUSED,
        Registration.Status.WITHDRAWN,
        Registration.Status.SUSPENDED,
    ):
        registration = RegistrationFactory.create(status=status)
        assert status_pill_for(registration, match_state) == {
            "label": label,
            "tone": tone,
        }


# ---------------------------------------------------------------------------
# match_status_context (VERB-116)
# ---------------------------------------------------------------------------


def test_match_status_context_no_registration() -> None:
    """A user with no Registration gets a neutral, no-registration context."""
    user = UserFactory.create()
    context = match_status_context(user)
    assert context["registration"] is None
    assert context["status_pill"] == {"label": "Queued", "tone": "muted"}
    assert context["match_state"] == "none"
    assert context["queue_position"] is None
    assert context["can_rejoin"] is False
    assert context["can_cancel"] is False


def test_match_status_context_verified_no_match() -> None:
    """A VERIFIED registration with no active match reports its queue position."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    context = match_status_context(reg.user)
    assert context["registration"] == reg
    assert context["match_state"] == "none"
    assert context["queue_position"] == 1
    assert context["can_rejoin"] is False
    assert context["can_cancel"] is False


def test_match_status_context_paused_no_match() -> None:
    """A PAUSED registration with no active match allows rejoin and cancel."""
    reg = RegistrationFactory.create(status=Registration.Status.PAUSED)
    context = match_status_context(reg.user)
    assert context["match_state"] == "none"
    assert context["queue_position"] is None
    assert context["can_rejoin"] is True
    assert context["can_cancel"] is True


def test_match_status_context_proposed_match() -> None:
    """An active PROPOSED match surfaces match_state and partner details."""
    reg = RegistrationFactory.create()
    partner_user = UserFactory.create(first_name="Bernard")
    partner = RegistrationFactory.create(referee=True, user=partner_user)
    MatchFactory.create(
        ambassador_registration=reg,
        referee_registration=partner,
        status=Match.Status.PROPOSED,
    )
    context = match_status_context(reg.user)
    assert context["match_state"] == "proposed"
    assert context["partner_first_name"] == "Bernard"
    assert context["partner_accepted"] is False


def test_match_status_context_referee_side() -> None:
    """The context resolves the partner correctly from the referee's side too."""
    reg = RegistrationFactory.create(referee=True)
    partner_user = UserFactory.create(first_name="Léa")
    partner = RegistrationFactory.create(user=partner_user)
    MatchFactory.create(
        ambassador_registration=partner,
        referee_registration=reg,
        status=Match.Status.PROPOSED,
    )
    context = match_status_context(reg.user)
    assert context["match_state"] == "proposed"
    assert context["partner_first_name"] == "Léa"


def test_match_status_context_lapsed_proposed_match_returns_none() -> None:
    """A lapsed, unswept PROPOSED match is treated as inactive (VERB-113 parity)."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    MatchFactory.create(
        ambassador_registration=reg,
        status=Match.Status.PROPOSED,
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    context = match_status_context(reg.user)
    assert context["match_state"] == "none"
    # queue_position() is pool-eligibility-based (_without_active_match), which
    # still excludes a lapsed-but-unswept match (Invariant 3) — unlike
    # match_state, which uses active_at() and treats it as inactive.
    assert context["queue_position"] is None
