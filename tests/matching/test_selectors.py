# Tests for the matching presentation/context selectors.

from datetime import UTC, datetime

import pytest

from matching.models import Match, Registration
from matching.selectors import (
    _QUEUE_MAX_ICONS,
    _pictograph,
    match_status_context,
    queue_snapshot_context,
    status_pill_for,
)
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


# ---------------------------------------------------------------------------
# queue_snapshot_context (VERB-145)
# ---------------------------------------------------------------------------


def test_queue_snapshot_context_two_columns_in_role_order() -> None:
    """queue_snapshot_context returns ambassador first, then referee."""
    context = queue_snapshot_context()

    assert len(context["columns"]) == 2
    assert context["columns"][0]["role_label"] == "Ambassador"
    assert context["columns"][1]["role_label"] == "Referee"


def test_queue_snapshot_context_reflects_pool_counts() -> None:
    """queue_snapshot_context surfaces the counts from queue_snapshot()."""
    RegistrationFactory.create(status=Registration.Status.VERIFIED)
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)
    MatchFactory.create()  # PROPOSED — one matched ambassador + one matched referee

    context = queue_snapshot_context()

    ambassador_column, referee_column = context["columns"]
    assert ambassador_column == {
        "role_label": "Ambassador",
        "is_referee": False,
        "matched": 1,
        "unmatched": 1,
        "total": 2,
        "icons": ["matched", "waiting"],
        "scaled": False,
    }
    assert referee_column == {
        "role_label": "Referee",
        "is_referee": True,
        "matched": 1,
        "unmatched": 1,
        "total": 2,
        "icons": ["matched", "waiting"],
        "scaled": False,
    }


def test_queue_snapshot_context_empty_pool_has_no_icons() -> None:
    """An empty pool yields zero totals, no icons, and unscaled columns."""
    context = queue_snapshot_context()

    for column in context["columns"]:
        assert column["total"] == 0
        assert column["matched"] == 0
        assert column["unmatched"] == 0
        assert column["icons"] == []
        assert column["scaled"] is False


def test_queue_snapshot_context_pictograph_is_exact_below_cap() -> None:
    """Below the icon cap the pictograph is one icon per person, matched first."""
    # One PROPOSED match (matched ambassador + referee) plus one waiting ambassador.
    RegistrationFactory.create(status=Registration.Status.VERIFIED)
    MatchFactory.create()

    ambassador_column = queue_snapshot_context()["columns"][0]
    assert ambassador_column["total"] == 2
    assert ambassador_column["icons"] == ["matched", "waiting"]
    assert ambassador_column["icons"].count("matched") == ambassador_column["matched"]
    assert ambassador_column["icons"].count("waiting") == ambassador_column["unmatched"]


@pytest.mark.parametrize(
    ("matched", "total"),
    [(0, 0), (3, 5), (5, 5), (0, 4)],
)
def test_pictograph_exact_below_cap(matched: int, total: int) -> None:
    """Below the cap, _pictograph is one icon per person, matched first, unscaled."""
    icons, scaled = _pictograph(matched=matched, total=total)

    assert scaled is False
    assert icons == ["matched"] * matched + ["waiting"] * (total - matched)


def test_pictograph_scales_above_cap() -> None:
    """Above the cap the icon list is capped and proportional; scaled is True."""
    icons, scaled = _pictograph(matched=50, total=100)

    assert scaled is True
    assert len(icons) == _QUEUE_MAX_ICONS
    # 50/100 of the cap is matched — the proportion is preserved.
    assert icons.count("matched") == _QUEUE_MAX_ICONS // 2
    assert icons.count("waiting") == _QUEUE_MAX_ICONS // 2
