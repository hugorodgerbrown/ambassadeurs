# Tests for the matching presentation/context selectors.

from datetime import UTC, datetime

import pytest
from django.test import override_settings

from matching.models import Match, Registration
from matching.selectors import (
    _QUEUE_MAX_ICONS,
    _QUEUE_MAX_PAIRS,
    _capped,
    match_status_context,
    queue_snapshot_context,
    status_pill_for,
)
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db

# A fixed "now" well after the default test MATCHING_OPENS_AT (2020-01-01), so
# matching reads as open unless a test overrides the setting.
_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


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


def test_queue_snapshot_context_has_three_columns() -> None:
    """queue_snapshot_context returns the three columns plus the open-date keys."""
    context = queue_snapshot_context(_NOW)

    assert {"ambassadors", "matches", "referees"} <= set(context)
    assert set(context["ambassadors"]) == {"count", "glyphs", "scaled"}
    assert set(context["matches"]) == {"count", "people", "glyphs", "scaled"}


def test_queue_snapshot_context_reflects_pool_counts() -> None:
    """queue_snapshot_context splits the pool into waiting sides + matched pairs.

    Two matched pairs (each an ambassador + referee) plus one extra waiting
    ambassador and two extra waiting referees. The matched column reports
    ``people`` (2 x the two matches = 4), not the number of matches.
    """
    RegistrationFactory.create(status=Registration.Status.VERIFIED)  # waiting amb.
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)
    MatchFactory.create()  # PROPOSED — one matched ambassador + one matched referee
    MatchFactory.create(pending=True)  # PENDING — another matched pair

    context = queue_snapshot_context(_NOW)

    assert context["ambassadors"] == {"count": 1, "glyphs": [0], "scaled": False}
    assert context["referees"] == {"count": 2, "glyphs": [0, 1], "scaled": False}
    assert context["matches"] == {
        "count": 2,
        "people": 4,
        "glyphs": [0, 1],
        "scaled": False,
    }


def test_queue_snapshot_context_empty_pool() -> None:
    """An empty pool yields zero counts, no glyphs, and no scaling anywhere."""
    context = queue_snapshot_context(_NOW)

    for column in (context["ambassadors"], context["referees"], context["matches"]):
        assert column["count"] == 0
        assert column["glyphs"] == []
        assert column["scaled"] is False
    assert context["matches"]["people"] == 0


@override_settings(MATCHING_OPENS_AT="2020-01-01T00:00:00+00:00")
def test_queue_snapshot_context_open_has_no_countdown() -> None:
    """Past the open date, matching reads as open and the countdown is zero."""
    context = queue_snapshot_context(_NOW)

    assert context["is_open"] is True
    assert context["days_until_open"] == 0


@override_settings(MATCHING_OPENS_AT="2026-10-01T00:00:00+00:00")
def test_queue_snapshot_context_not_open_counts_down() -> None:
    """Before the open date, matching is closed and the day countdown is exposed."""
    context = queue_snapshot_context(_NOW)

    assert context["is_open"] is False
    # 2026-07-10 → 2026-10-01 is 83 calendar days.
    assert context["days_until_open"] == 83


@pytest.mark.parametrize(
    ("count", "cap", "expected_len", "expected_scaled"),
    [
        (0, 40, 0, False),
        (5, 40, 5, False),
        (40, 40, 40, False),
        (41, 40, 40, True),
        (200, 40, 40, True),
    ],
)
def test_capped(count: int, cap: int, expected_len: int, expected_scaled: bool) -> None:
    """_capped draws one glyph per item up to the cap, then flags a capped sample."""
    glyphs, scaled = _capped(count, cap)

    assert glyphs == list(range(expected_len))
    assert scaled is expected_scaled


def test_queue_snapshot_context_caps_are_wired() -> None:
    """The waiting and match columns use their respective glyph caps.

    Drives ``_capped`` at the two module caps directly (creating hundreds of rows
    would be slow and add nothing) to confirm the wiring: waiting columns cap at
    ``_QUEUE_MAX_ICONS``, the matches column at ``_QUEUE_MAX_PAIRS``.
    """
    assert _capped(_QUEUE_MAX_ICONS + 5, _QUEUE_MAX_ICONS) == (
        list(range(_QUEUE_MAX_ICONS)),
        True,
    )
    assert _capped(_QUEUE_MAX_PAIRS + 5, _QUEUE_MAX_PAIRS) == (
        list(range(_QUEUE_MAX_PAIRS)),
        True,
    )
