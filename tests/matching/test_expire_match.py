# Unit tests for the model/service boundary established by VERB-100
# (docs/decisions/0017-state-transition-model-service-split.md).
#
# Covers the pure, in-memory model methods (Match.expire, Registration.pause,
# Registration.requeue_to_front), the expire_match per-match orchestration
# function, and the handle_lapsed_participant service function that
# coordinates the per-side re-queue/pause outcome and email dispatch. Mirrors
# the conventions in tests/matching/test_expire_matches.py: pytest +
# FactoryBoy, tz-aware datetimes, factories called with .create(),
# TestCase.captureOnCommitCallbacks(execute=True) to fire on_commit callbacks.

from datetime import UTC, datetime

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test import TestCase

from core.models import StateTransitionLog
from matching.models import Match, Registration
from matching.services import expire_match, handle_lapsed_participant
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db

# A tz-aware instant in the past suitable for lapsed-match tests.
_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Match.expire() — pure, in-memory model method
# ---------------------------------------------------------------------------


def test_expire_proposed_sets_status_and_returns_self() -> None:
    """Match.expire() on a PROPOSED match sets status=EXPIRED and returns self."""
    match = MatchFactory.create(expires_at=_PAST)

    result = match.expire()

    assert result is match
    assert match.status == Match.Status.EXPIRED


def test_expire_pending_sets_status_and_returns_self() -> None:
    """Match.expire() on a PENDING match sets status=EXPIRED and returns self."""
    match = MatchFactory.create(pending=True, expires_at=_PAST)

    result = match.expire()

    assert result is match
    assert match.status == Match.Status.EXPIRED


def test_expire_does_not_persist() -> None:
    """Match.expire() mutates only the in-memory instance; it never saves."""
    match = MatchFactory.create(expires_at=_PAST)

    match.expire()

    # Not saved: the DB row is unchanged.
    assert Match.objects.get(pk=match.pk).status == Match.Status.PROPOSED


@pytest.mark.parametrize(
    "trait",
    ["accepted", "declined", "cancelled"],
)
def test_expire_raises_for_terminal_statuses(trait: str) -> None:
    """Match.expire() raises ValueError for ACCEPTED, DECLINED, or CANCELLED."""
    match = MatchFactory.create(**{trait: True})

    with pytest.raises(ValueError, match=f"pk={match.pk}"):
        match.expire()


def test_expire_raises_for_already_expired() -> None:
    """Match.expire() raises ValueError when the match is already EXPIRED."""
    match = MatchFactory.create(expires_at=_PAST)
    match.status = Match.Status.EXPIRED
    match.save(update_fields=["status"])

    with pytest.raises(ValueError, match=f"pk={match.pk}"):
        match.expire()


# ---------------------------------------------------------------------------
# Registration.pause() — pure, in-memory model method
# ---------------------------------------------------------------------------


def test_pause_sets_status_and_returns_self() -> None:
    """Registration.pause() sets status=PAUSED and returns self."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)

    result = registration.pause()

    assert result is registration
    assert registration.status == Registration.Status.PAUSED


def test_pause_does_not_persist() -> None:
    """Registration.pause() mutates only the in-memory instance; it never saves."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)

    registration.pause()

    assert (
        Registration.objects.get(pk=registration.pk).status
        == Registration.Status.VERIFIED
    )


# ---------------------------------------------------------------------------
# Registration.requeue_to_front() — pure, in-memory model method
# ---------------------------------------------------------------------------


def test_requeue_to_front_sets_status_and_increments_priority() -> None:
    """Registration.requeue_to_front() sets status=VERIFIED and priority += 1."""
    registration = RegistrationFactory.create(
        status=Registration.Status.PAUSED, priority=2
    )

    result = registration.requeue_to_front()

    assert result is registration
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 3


def test_requeue_to_front_does_not_persist() -> None:
    """Registration.requeue_to_front() mutates in-memory only; never saves."""
    registration = RegistrationFactory.create(
        status=Registration.Status.PAUSED, priority=0
    )

    registration.requeue_to_front()

    persisted = Registration.objects.get(pk=registration.pk)
    assert persisted.status == Registration.Status.PAUSED
    assert persisted.priority == 0


# ---------------------------------------------------------------------------
# expire_match — per-match orchestration (already-locked match)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trait",
    ["accepted", "declined", "cancelled"],
)
def test_expire_match_skips_terminal_status_and_returns_false(trait: str) -> None:
    """expire_match on an already-terminal match returns False and is a no-op.

    Mirrors the idempotency-skip path exercised at the sweep level in
    test_concurrency_skip_when_match_no_longer_proposed
    (tests/matching/test_expire_matches.py), but calls expire_match directly.
    """
    match = MatchFactory.create(**{trait: True})
    status_before = match.status
    log_count_before = StateTransitionLog.objects.count()

    result = expire_match(match)

    assert result is False
    assert match.status == status_before
    assert StateTransitionLog.objects.count() == log_count_before

    match.refresh_from_db()
    assert match.status == status_before


def test_expire_match_skips_already_expired_and_returns_false() -> None:
    """expire_match on an already-EXPIRED match returns False and is a no-op."""
    match = MatchFactory.create(expires_at=_PAST)
    match.status = Match.Status.EXPIRED
    match.save(update_fields=["status"])
    log_count_before = StateTransitionLog.objects.count()

    result = expire_match(match)

    assert result is False
    assert match.status == Match.Status.EXPIRED
    assert StateTransitionLog.objects.count() == log_count_before

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED


def test_expire_match_proposed_returns_true_and_transitions() -> None:
    """expire_match on a lapsed PROPOSED match expires it and handles participants.

    Asserts the return value, the status transition, exactly one
    StateTransitionLog row, and that both participants were handled (here,
    neither accepted, so both are paused).
    """
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=None,
        referee_accepted_at=None,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = expire_match(match)

    assert result is True
    assert match.status == Match.Status.EXPIRED

    match_ct = ContentType.objects.get_for_model(Match)
    logs = StateTransitionLog.objects.filter(
        content_type=match_ct,
        object_id=match.pk,
        field_name="status",
    )
    assert logs.count() == 1
    log = logs.first()
    assert log is not None
    assert log.state_before == Match.Status.PROPOSED
    assert log.state_after == Match.Status.EXPIRED

    # Both participants handled: neither accepted, so both are paused.
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert referee_reg.status == Registration.Status.PAUSED
    assert len(mail.outbox) == 2


def test_expire_match_pending_returns_true_and_transitions() -> None:
    """expire_match on a lapsed PENDING match expires it and handles participants.

    The ambassador had accepted (kept faith) so is re-queued to the front; the
    referee had not responded so is paused.
    """
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        ambassador_accepted_at=_PAST,
        referee_accepted_at=None,
        status=Match.Status.PENDING,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        result = expire_match(match)

    assert result is True
    assert match.status == Match.Status.EXPIRED

    match_ct = ContentType.objects.get_for_model(Match)
    logs = StateTransitionLog.objects.filter(
        content_type=match_ct,
        object_id=match.pk,
        field_name="status",
    )
    assert logs.count() == 1
    log = logs.first()
    assert log is not None
    assert log.state_before == Match.Status.PENDING
    assert log.state_after == Match.Status.EXPIRED

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 1
    assert referee_reg.status == Registration.Status.PAUSED
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# handle_lapsed_participant — service coordination, role-agnostic
# ---------------------------------------------------------------------------


def test_handle_lapsed_participant_kept_faith_requeues_ambassador() -> None:
    """kept_faith=True re-queues an ambassador to the front and emails them."""
    registration = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        handle_lapsed_participant(registration, kept_faith=True)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 1

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [registration.user.email]
    assert "front of the queue" in mail.outbox[0].body


def test_handle_lapsed_participant_kept_faith_requeues_referee() -> None:
    """kept_faith=True re-queues a referee to the front and emails them."""
    registration = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        handle_lapsed_participant(registration, kept_faith=True)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 1

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [registration.user.email]
    assert "front of the queue" in mail.outbox[0].body


def test_handle_lapsed_participant_non_responder_pauses_ambassador() -> None:
    """kept_faith=False pauses an ambassador and sends the window-expired email."""
    registration = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        handle_lapsed_participant(registration, kept_faith=False)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.PAUSED
    assert registration.priority == 0  # unchanged on pause

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [registration.user.email]
    assert "rejoin the queue" in mail.outbox[0].subject


def test_handle_lapsed_participant_non_responder_pauses_referee() -> None:
    """kept_faith=False pauses a referee and sends the window-expired email."""
    registration = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        handle_lapsed_participant(registration, kept_faith=False)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.PAUSED
    assert registration.priority == 0  # unchanged on pause

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [registration.user.email]
    assert "rejoin the queue" in mail.outbox[0].subject
