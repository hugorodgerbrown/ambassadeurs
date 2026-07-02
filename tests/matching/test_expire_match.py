# Unit tests for the model/service boundary established by VERB-100
# (docs/decisions/0017-state-transition-model-service-split.md).
#
# Covers the pure, in-memory model methods (Match.expire, Registration.pause,
# Registration.requeue_to_front) and the handle_lapsed_participant service
# function that coordinates the per-side re-queue/pause outcome and email
# dispatch. Mirrors the conventions in tests/matching/test_expire_matches.py:
# pytest + FactoryBoy, tz-aware datetimes, factories called with .create(),
# TestCase.captureOnCommitCallbacks(execute=True) to fire on_commit callbacks.

from datetime import UTC, datetime

import pytest
from django.core import mail
from django.test import TestCase

from matching.models import Match, Registration
from matching.services import handle_lapsed_participant
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
