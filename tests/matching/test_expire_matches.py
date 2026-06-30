# Tests for the expire_lapsed_matches service and the expire_matches management command.
#
# Mirrors the conventions in tests/matching/test_services.py: pytest + FactoryBoy,
# tz-aware datetimes, factories called with .create().
#
# VERB-74: non-responders are now paused (pause_registration) rather than
# record_flake_and_requeue'd. The two-strike flake model is retired.

from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from core.models import StateTransitionLog
from matching.models import Match, Registration
from matching.services import expire_lapsed_matches, pause_registration
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db

# A tz-aware instant in the past suitable for lapsed-match tests.
_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
# A tz-aware instant in the future (default MatchFactory value).
_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


# ---------------------------------------------------------------------------
# expire_lapsed_matches — core behaviour
# ---------------------------------------------------------------------------


def test_lapsed_both_sides_no_accept_pauses_both_and_expires() -> None:
    """Lapsed match, neither side accepted → both registrations PAUSED and EXPIRED.

    VERB-74: non-responders are paused (not re-queued with a flake).
    """
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
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
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert ambassador_reg.priority == 0  # unchanged on pause

    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.PAUSED
    assert referee_reg.priority == 0  # unchanged on pause


def test_lapsed_ambassador_accepted_gets_front_referee_paused() -> None:
    """Lapsed match, ambassador accepted → ambassador to front, referee paused."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=_PAST,
        referee_accepted_at=None,
        status=Match.Status.PENDING,
    )

    count = expire_lapsed_matches()

    assert count == 1

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 1  # front: priority += 1

    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.PAUSED
    assert referee_reg.priority == 0  # unchanged


def test_lapsed_referee_accepted_gets_front_ambassador_paused() -> None:
    """Lapsed match, referee accepted → referee to front, ambassador paused."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=_PAST,
        status=Match.Status.PENDING,
    )

    count = expire_lapsed_matches()

    assert count == 1

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert ambassador_reg.priority == 0  # unchanged

    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.VERIFIED
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
    assert ambassador_reg.status == Registration.Status.VERIFIED

    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.VERIFIED


def test_idempotency_second_run_returns_zero() -> None:
    """Running the sweep twice: second call returns 0 and DB state is unchanged."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
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
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert referee_reg.status == Registration.Status.PAUSED

    second_count = expire_lapsed_matches()
    assert second_count == 0

    # DB state is unchanged (no double-pause).
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert referee_reg.status == Registration.Status.PAUSED


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


def test_expiry_email_sent_to_non_responders() -> None:
    """A window-expired notification is sent to each non-responding party on commit."""
    from django.core import mail

    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        expire_lapsed_matches()

    # Both parties are non-responders — each receives a notification.
    assert len(mail.outbox) == 2
    recipients = {msg.to[0] for msg in mail.outbox}
    assert ambassador_reg.user.email in recipients
    assert referee_reg.user.email in recipients


def test_no_expiry_email_for_faithful_party() -> None:
    """No expiry email is sent to the faithful party (they are re-queued, not paused)."""
    from django.core import mail

    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=_PAST,  # ambassador accepted; referee did not
        referee_accepted_at=None,
        status=Match.Status.PENDING,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        expire_lapsed_matches()

    # Only the referee (non-responder) gets the email.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [referee_reg.user.email]


def test_concurrency_skip_when_match_no_longer_proposed() -> None:
    """Sweep skips a match that is no longer PROPOSED by the time it is locked.

    Simulates a race: lapsed() returns the PK, but by the time the sweep does
    the locked re-fetch the match has already been transitioned (e.g. accepted
    just before the sweep locked it). The ``if match.status not in PROPOSED/PENDING:
    continue`` guard must skip the match without mutating anything.
    """
    match = MatchFactory.create(
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )
    match_pk = match.pk

    # Bypass services to force a terminal state without going through expiry.
    Match.objects.filter(pk=match_pk).update(status=Match.Status.DECLINED)

    # Patch lapsed() so it still returns this PK as if the race happened
    # between the initial PK query and the locked re-fetch.
    fake_qs = Match.objects.filter(pk=match_pk)

    log_count_before = StateTransitionLog.objects.count()

    with patch.object(Match.objects.__class__, "lapsed", return_value=fake_qs):
        count = expire_lapsed_matches()

    assert count == 0

    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED

    # No new transition log row must have been written.
    assert StateTransitionLog.objects.count() == log_count_before


def test_per_match_exception_isolation_continues_sweep(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exception mid-sweep is isolated: the remaining matches are still processed.

    Creates two lapsed PROPOSED matches. Patches pause_registration to raise
    on the first call only (simulating a DB or logic failure). The second match
    must still be transitioned to EXPIRED and paused; the failed match is left
    untouched. expire_lapsed_matches returns 1 (the successfully processed
    match) and logs the error.
    """
    ambassador_reg_1 = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )
    referee_reg_1 = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )
    match_1 = MatchFactory.create(
        ambassador_registration=ambassador_reg_1,
        referee_registration=referee_reg_1,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    ambassador_reg_2 = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )
    referee_reg_2 = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )
    match_2 = MatchFactory.create(
        ambassador_registration=ambassador_reg_2,
        referee_registration=referee_reg_2,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    # Fail on the first call to pause_registration, succeed thereafter.
    _call_count = {"n": 0}
    _real = pause_registration

    def _failing_pause(registration: Registration) -> None:
        """Raise on the first invocation; delegate to the real function after."""
        _call_count["n"] += 1
        if _call_count["n"] == 1:
            raise RuntimeError("simulated failure")
        _real(registration)

    import matching.services as _svc

    with patch.object(_svc, "pause_registration", _failing_pause):
        with caplog.at_level("ERROR", logger="matching.services"):
            count = expire_lapsed_matches()

    # Only one match successfully expired (the second one).
    assert count == 1

    # The ordering of candidate_pks is deterministic (Match.Meta.ordering =
    # ["-created_at"]), so match_2 was created last → it appears first in the
    # PK list → match_1 is processed second.  Exactly one match should be
    # EXPIRED; the other stays PROPOSED (rolled back by the atomic block).
    match_1.refresh_from_db()
    match_2.refresh_from_db()
    statuses = {match_1.status, match_2.status}
    assert Match.Status.EXPIRED in statuses
    assert Match.Status.PROPOSED in statuses

    # The error must have been logged.
    assert any("Error expiring match" in record.message for record in caplog.records)


def test_already_terminal_declined_match_not_reprocessed() -> None:
    """A lapsed DECLINED match is not reprocessed by the sweep.

    lapsed() filters to PROPOSED and PENDING only, so a DECLINED match with a
    past expires_at is structurally excluded from the candidate set. The sweep
    returns 0 and leaves the match and its registrations untouched.
    """
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    match = MatchFactory.create(
        declined=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
    )

    count = expire_lapsed_matches()

    assert count == 0

    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 0

    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.VERIFIED
    assert referee_reg.priority == 0


# ---------------------------------------------------------------------------
# expire_matches management command
# ---------------------------------------------------------------------------


def test_expire_matches_command_expires_lapsed_match() -> None:
    """call_command('expire_matches') expires a lapsed match and reports the count."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
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
