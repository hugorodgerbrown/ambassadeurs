# Unit tests for public.views.match.MatchDisplay — the value object that
# projects a match + viewer side into the three presentation keys the match
# page needs (action-guard state, design view key, per-side roster pill).
#
# Constructed directly with a fixed ``now`` so the "active but contact window
# lapsed" branch is exercised deterministically without the real clock; a
# single test covers ``for_viewer`` sampling the current time.

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from matching.models import Match
from public.views.match import (
    _STATE_ACTIONABLE,
    _STATE_TERMINAL,
    _STATE_WAITING,
    MatchDisplay,
)
from tests.matching.factories import MatchFactory

# Well inside the factory's 2099 expiry — the match is active.
_WITHIN_WINDOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=UTC)
# Past the factory's 2099 expiry — an active match's window has lapsed.
_AFTER_WINDOW = datetime(2100, 1, 1, 0, 0, 0, tzinfo=UTC)


def _display(
    match: Match, side: Match.Side, now: datetime = _WITHIN_WINDOW
) -> MatchDisplay:
    """Build a MatchDisplay at a fixed ``now`` (default: within the window)."""
    return MatchDisplay(match=match, side=side, now=now)


# --- guard_state ---------------------------------------------------------


@pytest.mark.django_db
def test_guard_state_proposed_unaccepted_is_actionable() -> None:
    """A PROPOSED match within the window, this side unaccepted → actionable."""
    match = MatchFactory.create()
    assert _display(match, Match.Side.AMBASSADOR).guard_state == _STATE_ACTIONABLE


@pytest.mark.django_db
def test_guard_state_pending_own_acceptance_is_waiting() -> None:
    """PENDING with the viewer's own acceptance recorded → waiting."""
    match = MatchFactory.create(pending=True)  # ambassador accepted
    assert _display(match, Match.Side.AMBASSADOR).guard_state == _STATE_WAITING


@pytest.mark.django_db
def test_guard_state_pending_other_acceptance_is_actionable() -> None:
    """PENDING where the *other* side accepted; this side still owes a response."""
    match = MatchFactory.create(pending=True)  # ambassador accepted
    assert _display(match, Match.Side.REFEREE).guard_state == _STATE_ACTIONABLE


@pytest.mark.django_db
def test_guard_state_window_lapsed_is_terminal() -> None:
    """A PROPOSED match whose window has passed reads as terminal."""
    match = MatchFactory.create()
    display = _display(match, Match.Side.AMBASSADOR, now=_AFTER_WINDOW)
    assert display.guard_state == _STATE_TERMINAL


@pytest.mark.django_db
def test_guard_state_accepted_is_terminal() -> None:
    """A terminal status (ACCEPTED) is never actionable."""
    match = MatchFactory.create(accepted=True)
    assert _display(match, Match.Side.AMBASSADOR).guard_state == _STATE_TERMINAL


# --- view_key ------------------------------------------------------------


@pytest.mark.django_db
def test_view_key_confirmed() -> None:
    """ACCEPTED → confirmed."""
    match = MatchFactory.create(accepted=True)
    assert _display(match, Match.Side.REFEREE).view_key == "confirmed"


@pytest.mark.django_db
def test_view_key_declined_is_relative_to_viewer() -> None:
    """DECLINED reads declined_you for the decliner, declined_partner otherwise."""
    match = MatchFactory.create(declined=True)  # declined_by AMBASSADOR
    assert _display(match, Match.Side.AMBASSADOR).view_key == "declined_you"
    assert _display(match, Match.Side.REFEREE).view_key == "declined_partner"


@pytest.mark.django_db
def test_view_key_cancelled_is_relative_to_viewer() -> None:
    """CANCELLED reads cancelled_you for the reporter, cancelled_partner otherwise."""
    match = MatchFactory.create(cancelled=True)  # reported_by REFEREE
    assert _display(match, Match.Side.REFEREE).view_key == "cancelled_you"
    assert _display(match, Match.Side.AMBASSADOR).view_key == "cancelled_partner"


@pytest.mark.django_db
def test_view_key_proposed_and_accepted_variants() -> None:
    """PROPOSED/PENDING within the window distinguish own vs partner acceptance."""
    proposed = MatchFactory.create()
    assert _display(proposed, Match.Side.AMBASSADOR).view_key == "proposed"

    pending = MatchFactory.create(pending=True)  # ambassador accepted
    assert _display(pending, Match.Side.AMBASSADOR).view_key == "you_accepted"
    assert _display(pending, Match.Side.REFEREE).view_key == "partner_accepted"


@pytest.mark.django_db
def test_view_key_window_lapsed_is_expired() -> None:
    """A PROPOSED match past its window is presented as expired."""
    match = MatchFactory.create()
    display = _display(match, Match.Side.AMBASSADOR, now=_AFTER_WINDOW)
    assert display.view_key == "expired"


# --- side_status ---------------------------------------------------------


@pytest.mark.django_db
def test_side_status_accepted_both_sides() -> None:
    """An ACCEPTED match shows both roster sides as accepted."""
    match = MatchFactory.create(accepted=True)
    display = _display(match, Match.Side.AMBASSADOR)
    assert display.side_status(Match.Side.AMBASSADOR) == "accepted"
    assert display.side_status(Match.Side.REFEREE) == "accepted"


@pytest.mark.django_db
def test_side_status_pending_splits_by_side() -> None:
    """PENDING: the accepted side reads accepted, the other pending."""
    match = MatchFactory.create(pending=True)  # ambassador accepted
    display = _display(match, Match.Side.REFEREE)
    assert display.side_status(Match.Side.AMBASSADOR) == "accepted"
    assert display.side_status(Match.Side.REFEREE) == "pending"


@pytest.mark.django_db
def test_side_status_window_lapsed_is_no_response() -> None:
    """A lapsed active match reads no_response for an unaccepted side."""
    match = MatchFactory.create()
    display = _display(match, Match.Side.AMBASSADOR, now=_AFTER_WINDOW)
    assert display.side_status(Match.Side.AMBASSADOR) == "no_response"


# --- for_viewer ----------------------------------------------------------


@pytest.mark.django_db
def test_for_viewer_samples_now_and_keeps_side() -> None:
    """for_viewer builds an instance for the given side at the current time."""
    match = MatchFactory.create()
    display = MatchDisplay.for_viewer(match, Match.Side.REFEREE)
    assert display.side == Match.Side.REFEREE
    assert display.match is match
    # Sampled now is within the factory's far-future window → still active.
    assert display.guard_state == _STATE_ACTIONABLE
