# Label-level tests for the django-side-effects dispatch wiring (VERB-107 /
# ADR 0018): asserts which @has_side_effects label each of the five match
# transitions fires, using registry.disable_side_effects() as the events
# collector. These are additional to (not a replacement for) the
# captureOnCommitCallbacks(execute=True) outbox assertions in
# tests/matching/test_services.py, tests/matching/test_expire_match.py, and
# tests/matching/test_run_matching.py, which remain the regression oracle for
# email delivery/content. This module only asserts dispatch — that the right
# label fires (or does not fire) — decoupled from the email body.

from datetime import UTC, datetime

import pytest
from side_effects import registry

from matching.models import Registration
from matching.services import (
    expire_match,
    propose_match,
    record_acceptance,
    record_decline,
    report_no_show,
)
from matching.side_effects import (
    MATCH_ACCEPTED,
    MATCH_DECLINED,
    MATCH_EXPIRED,
    MATCH_NO_SHOW,
    MATCH_PROPOSED,
)
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db

_PAST = datetime(2020, 1, 1, tzinfo=UTC)


def test_propose_match_fires_match_proposed_when_counterpart_waiting() -> None:
    """propose_match fires MATCH_PROPOSED when it creates a match."""
    RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )

    with registry.disable_side_effects() as events:
        match = propose_match(ambassador)

    assert match is not None
    assert events == [MATCH_PROPOSED]


def test_propose_match_fires_nothing_when_no_counterpart_waiting() -> None:
    """propose_match fires no label when no eligible counterpart is waiting.

    Exercises the run_on_exit=lambda match: match is not None gate: the
    handlers must never fire on the None-returning path.
    """
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )

    with registry.disable_side_effects() as events:
        match = propose_match(ambassador)

    assert match is None
    assert events == []


def test_record_acceptance_first_accept_fires_match_accepted() -> None:
    """The first accept (PROPOSED -> PENDING) fires MATCH_ACCEPTED."""
    match = MatchFactory.create()

    with registry.disable_side_effects() as events:
        record_acceptance(match, match.ambassador_registration)

    assert events == [MATCH_ACCEPTED]


def test_record_acceptance_second_accept_fires_match_accepted() -> None:
    """The second (mutual) accept also fires MATCH_ACCEPTED."""
    match = MatchFactory.create(pending=True)

    with registry.disable_side_effects() as events:
        record_acceptance(match, match.referee_registration)

    assert events == [MATCH_ACCEPTED]


def test_record_decline_fires_match_declined() -> None:
    """record_decline fires MATCH_DECLINED."""
    match = MatchFactory.create()

    with registry.disable_side_effects() as events:
        record_decline(match, match.ambassador_registration)

    assert events == [MATCH_DECLINED]


def test_expire_match_fires_match_expired() -> None:
    """expire_match fires MATCH_EXPIRED."""
    match = MatchFactory.create(expires_at=_PAST)

    with registry.disable_side_effects() as events:
        expire_match(match)

    assert events == [MATCH_EXPIRED]


def test_expire_match_referee_kept_faith_sends_requeued_not_window_expired() -> None:
    """A referee who accepted before expiry gets the requeued copy, not window-expired.

    Covers the referee-kept-faith branch of notify_referee_of_expiry (the
    ambassador-kept-faith case is covered in tests/matching/test_expire_match.py);
    exercised via captureOnCommitCallbacks so the actual handler body runs.
    """
    from django.core import mail
    from django.test import TestCase

    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=_PAST,
        referee_accepted_at=datetime(2019, 12, 31, tzinfo=UTC),  # kept faith
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        expire_match(match)

    referee_message = next(
        message for message in mail.outbox if message.to == [referee_reg.user.email]
    )
    assert "front of the queue" in referee_message.body


def test_report_no_show_fires_match_no_show() -> None:
    """report_no_show fires MATCH_NO_SHOW."""
    match = MatchFactory.create(accepted=True)

    with registry.disable_side_effects() as events:
        report_no_show(match, match.ambassador_registration)

    assert events == [MATCH_NO_SHOW]


def test_disable_side_effects_suppresses_email_dispatch() -> None:
    """Suppressed side effects mean no email is queued, even after commit.

    disable_side_effects() only records which labels *would* have fired; it
    does not execute the handlers. This confirms suppression is real, not
    just an events log, using a transition whose handler would otherwise
    queue mail on commit.
    """
    from django.core import mail
    from django.test import TestCase

    match = MatchFactory.create()

    with registry.disable_side_effects() as events:
        with TestCase.captureOnCommitCallbacks(execute=True):
            record_decline(match, match.ambassador_registration)

    assert events == [MATCH_DECLINED]
    assert len(mail.outbox) == 0
