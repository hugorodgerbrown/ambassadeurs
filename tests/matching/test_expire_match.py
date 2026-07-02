# Unit tests for the model/service boundary established by VERB-100
# (docs/decisions/0017-state-transition-model-service-split.md).
#
# Covers the pure, in-memory model methods (Match.expire, Registration.pause,
# Registration.requeue), the expire_match per-match orchestration function,
# and the handle_lapsed_participant service function that coordinates the
# per-side re-queue/pause outcome and email dispatch. Mirrors the conventions
# in tests/matching/test_expire_matches.py: pytest + FactoryBoy, tz-aware
# datetimes, factories called with .create(),
# TestCase.captureOnCommitCallbacks(execute=True) to fire on_commit callbacks.
#
# Fail-hard-low / catch-high (ADR 0017): the model methods validate their own
# source state and raise core.exceptions.StateTransitionError — not
# ValueError — on an illegal transition.

from datetime import UTC, datetime

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test import TestCase

from core.exceptions import StateTransitionError
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
    """Match.expire() raises StateTransitionError for ACCEPTED, DECLINED, CANCELLED."""
    match = MatchFactory.create(**{trait: True})
    status_before = match.status

    with pytest.raises(StateTransitionError) as exc_info:
        match.expire()

    assert exc_info.value.current == status_before
    assert exc_info.value.proposed == Match.Status.EXPIRED


def test_expire_raises_for_already_expired() -> None:
    """Match.expire() raises StateTransitionError when the match is already EXPIRED."""
    match = MatchFactory.create(expires_at=_PAST)
    match.status = Match.Status.EXPIRED
    match.save(update_fields=["status"])

    with pytest.raises(StateTransitionError) as exc_info:
        match.expire()

    assert exc_info.value.current == Match.Status.EXPIRED
    assert exc_info.value.proposed == Match.Status.EXPIRED


# ---------------------------------------------------------------------------
# Registration.pause() — pure, in-memory model method
# ---------------------------------------------------------------------------


def test_pause_sets_status_and_returns_self() -> None:
    """Registration.pause() from VERIFIED sets status=PAUSED and returns self."""
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


@pytest.mark.parametrize(
    "trait",
    ["paused", "suspended", "unverified"],
)
def test_pause_raises_for_illegal_source_states(trait: str) -> None:
    """Registration.pause() raises StateTransitionError from a non-VERIFIED state.

    Only VERIFIED is a legal source for PAUSED — the decline and expiry
    non-responder paths both act on VERIFIED registrations (VERB-74 / ADR
    0013). This is the fail-hard-low guard at the model layer.
    """
    registration = RegistrationFactory.create(**{trait: True})
    status_before = registration.status

    with pytest.raises(StateTransitionError) as exc_info:
        registration.pause()

    assert exc_info.value.current == status_before
    assert exc_info.value.proposed == Registration.Status.PAUSED
    # Not mutated on the illegal-transition path.
    assert registration.status == status_before


# ---------------------------------------------------------------------------
# Registration.requeue() — pure, in-memory model method
# ---------------------------------------------------------------------------


def test_requeue_default_priority_sets_status_and_increments_by_one() -> None:
    """Registration.requeue() with default args sets status=VERIFIED, priority += 1."""
    registration = RegistrationFactory.create(
        status=Registration.Status.PAUSED, priority=2
    )

    result = registration.requeue()

    assert result is registration
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 3


def test_requeue_explicit_priority_increments_by_the_given_amount() -> None:
    """Registration.requeue(priority=...) increments by the given amount."""
    registration = RegistrationFactory.create(
        status=Registration.Status.PAUSED, priority=2
    )

    result = registration.requeue(priority=3)

    assert result is registration
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 5


def test_requeue_does_not_persist() -> None:
    """Registration.requeue() mutates in-memory only; never saves."""
    registration = RegistrationFactory.create(
        status=Registration.Status.PAUSED, priority=0
    )

    registration.requeue()

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
def test_expire_match_raises_for_terminal_status(trait: str) -> None:
    """expire_match on an already-terminal match raises and is a no-op.

    expire_match no longer pre-checks the status itself (ADR 0017,
    fail-hard-low / catch-high): the guard lives solely in Match.expire(),
    which raises StateTransitionError. expire_match does not catch it — that
    is the caller's (expire_lapsed_matches's) responsibility. Mirrors the
    idempotency-skip path exercised at the sweep level in
    test_concurrency_skip_when_match_no_longer_proposed
    (tests/matching/test_expire_matches.py), but calls expire_match directly.
    """
    match = MatchFactory.create(**{trait: True})
    status_before = match.status
    log_count_before = StateTransitionLog.objects.count()

    with pytest.raises(StateTransitionError) as exc_info:
        expire_match(match)

    assert exc_info.value.current == status_before
    assert exc_info.value.proposed == Match.Status.EXPIRED
    assert match.status == status_before
    assert StateTransitionLog.objects.count() == log_count_before

    match.refresh_from_db()
    assert match.status == status_before


def test_expire_match_raises_for_already_expired() -> None:
    """expire_match on an already-EXPIRED match raises and is a no-op."""
    match = MatchFactory.create(expires_at=_PAST)
    match.status = Match.Status.EXPIRED
    match.save(update_fields=["status"])
    log_count_before = StateTransitionLog.objects.count()

    with pytest.raises(StateTransitionError):
        expire_match(match)

    assert match.status == Match.Status.EXPIRED
    assert StateTransitionLog.objects.count() == log_count_before

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED


def test_expire_match_proposed_transitions_and_handles_participants() -> None:
    """expire_match on a lapsed PROPOSED match expires it and handles participants.

    Asserts the status transition, exactly one StateTransitionLog row, and
    that both participants were handled (here, neither accepted, so both are
    paused).
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
        expire_match(match)

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


def test_expire_match_pending_transitions_and_handles_participants() -> None:
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
        expire_match(match)

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
#
# VERB-107: the re-queued / window-expired notification is no longer sent
# from here — it is dispatched by the matching.side_effects handlers bound to
# expire_match's @has_side_effects label (covered in the expire_match and
# expire_lapsed_matches tests below). This function now only applies the pure
# re-queue/pause mutation.
# ---------------------------------------------------------------------------


def test_handle_lapsed_participant_kept_faith_requeues_ambassador() -> None:
    """kept_faith=True re-queues an ambassador to the front."""
    registration = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )

    handle_lapsed_participant(registration, kept_faith=True)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 1


def test_handle_lapsed_participant_kept_faith_requeues_referee() -> None:
    """kept_faith=True re-queues a referee to the front."""
    registration = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )

    handle_lapsed_participant(registration, kept_faith=True)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.VERIFIED
    assert registration.priority == 1


def test_handle_lapsed_participant_non_responder_pauses_ambassador() -> None:
    """kept_faith=False pauses an ambassador."""
    registration = RegistrationFactory.create(
        status=Registration.Status.VERIFIED, priority=0
    )

    handle_lapsed_participant(registration, kept_faith=False)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.PAUSED
    assert registration.priority == 0  # unchanged on pause


def test_handle_lapsed_participant_non_responder_pauses_referee() -> None:
    """kept_faith=False pauses a referee."""
    registration = RegistrationFactory.create(
        referee=True, status=Registration.Status.VERIFIED, priority=0
    )

    handle_lapsed_participant(registration, kept_faith=False)

    registration.refresh_from_db()
    assert registration.status == Registration.Status.PAUSED
    assert registration.priority == 0  # unchanged on pause
