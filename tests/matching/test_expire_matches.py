# Tests for the expire_lapsed_matches service and the expire_matches management command.
#
# Mirrors the conventions in tests/matching/test_services.py: pytest + FactoryBoy,
# tz-aware datetimes, factories called with .create().

from datetime import UTC, datetime
from io import StringIO

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from core.models import StateTransitionLog
from matching.models import Match, Registration
from matching.services import expire_lapsed_matches
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db

# A tz-aware instant in the past suitable for lapsed-match tests.
_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
# A tz-aware instant in the future (default MatchFactory value).
_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


# ---------------------------------------------------------------------------
# expire_lapsed_matches — core behaviour
# ---------------------------------------------------------------------------


def test_lapsed_both_sides_no_accept_flakes_both_and_expires() -> None:
    """Lapsed match, neither side accepted → both registrations flaked and EXPIRED."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        # Neither side has accepted.
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    count = expire_lapsed_matches()

    assert count == 1

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.flake_count == 1
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == -1

    referee_reg.refresh_from_db()
    assert referee_reg.flake_count == 1
    assert referee_reg.status == Registration.Status.WAITING
    assert referee_reg.priority == -1


def test_lapsed_ambassador_accepted_gets_front_referee_flaked() -> None:
    """Lapsed match, ambassador accepted → ambassador to front, referee flaked."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=_PAST,
        referee_accepted_at=None,
    )

    count = expire_lapsed_matches()

    assert count == 1

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.flake_count == 0  # kept faith — no flake
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == 1  # front: priority += 1

    referee_reg.refresh_from_db()
    assert referee_reg.flake_count == 1
    assert referee_reg.status == Registration.Status.WAITING
    assert referee_reg.priority == -1


def test_lapsed_referee_accepted_gets_front_ambassador_flaked() -> None:
    """Lapsed match, referee accepted → referee to front, ambassador flaked."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=_PAST,
    )

    count = expire_lapsed_matches()

    assert count == 1

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.flake_count == 1
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == -1

    referee_reg.refresh_from_db()
    assert referee_reg.flake_count == 0  # kept faith — no flake
    assert referee_reg.status == Registration.Status.WAITING
    assert referee_reg.priority == 1  # front: priority += 1


def test_non_lapsed_match_untouched() -> None:
    """Non-lapsed PROPOSED match (default far-future expires_at) is not touched."""
    match = MatchFactory.create(expires_at=_FUTURE)

    count = expire_lapsed_matches()

    assert count == 0
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED


def test_already_terminal_match_not_reprocessed() -> None:
    """An already-ACCEPTED match is not reprocessed even if expires_at is past."""
    match = MatchFactory.create(accepted=True, expires_at=_PAST)
    ambassador_reg = match.ambassador_registration
    referee_reg = match.referee_registration

    count = expire_lapsed_matches()

    assert count == 0

    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.CONFIRMED

    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.CONFIRMED


def test_idempotency_second_run_returns_zero() -> None:
    """Running the sweep twice: second call returns 0 and DB state is unchanged."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    first_count = expire_lapsed_matches()
    assert first_count == 1

    # Capture state after first run.
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    amb_priority_after_first = ambassador_reg.priority
    ref_priority_after_first = referee_reg.priority

    second_count = expire_lapsed_matches()
    assert second_count == 0

    # DB state must be identical to after the first run.
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.priority == amb_priority_after_first
    assert referee_reg.priority == ref_priority_after_first


def test_state_transition_log_written_for_proposed_to_expired() -> None:
    """A StateTransitionLog row is created for the PROPOSED → EXPIRED transition."""
    match = MatchFactory.create(
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    expire_lapsed_matches()

    match_ct = ContentType.objects.get_for_model(Match)
    log = StateTransitionLog.objects.filter(
        content_type=match_ct,
        object_id=match.pk,
        field_name="status",
        state_after=Match.Status.EXPIRED,
    ).first()

    assert log is not None
    assert log.state_before == Match.Status.PROPOSED
    assert log.state_after == Match.Status.EXPIRED


def test_second_flake_suspends_registration() -> None:
    """Second flake (flake_count 1→2) suspends the registration."""
    # Registration already has one flake on record.
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=1,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,  # ambassador did not accept → second flake
        referee_accepted_at=None,
    )

    expire_lapsed_matches()

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.flake_count == 2
    assert ambassador_reg.status == Registration.Status.SUSPENDED


# ---------------------------------------------------------------------------
# expire_matches management command
# ---------------------------------------------------------------------------


def test_expire_matches_command_expires_lapsed_match() -> None:
    """call_command('expire_matches') expires a lapsed match and reports the count."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    stdout = StringIO()
    call_command("expire_matches", stdout=stdout)

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    output = stdout.getvalue()
    assert "1" in output


def test_expire_matches_command_reports_zero_when_nothing_to_expire() -> None:
    """expire_matches command reports 0 when there are no lapsed matches."""
    MatchFactory.create(expires_at=_FUTURE)

    stdout = StringIO()
    call_command("expire_matches", stdout=stdout)

    output = stdout.getvalue()
    assert "0" in output
