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
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import TestCase
from pytest_django.fixtures import DjangoAssertNumQueries
from side_effects import registry

from matching.models import Registration
from matching.services import (
    expire_lapsed_matches,
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
    track_match_accepted,
    track_match_confirmed,
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


# ---------------------------------------------------------------------------
# track_match_accepted / track_match_confirmed guard clauses (VERB-124)
#
# Called directly (not via record_acceptance) so each handler's own
# match.status guard is pinned in isolation, independent of the dispatch
# wiring already covered above and the end-to-end happy path covered in
# tests/matching/test_services.py.
# ---------------------------------------------------------------------------


def test_track_match_accepted_noops_when_match_is_accepted() -> None:
    """track_match_accepted only fires on PENDING; ACCEPTED is a no-op."""
    match = MatchFactory.create(accepted=True)

    with patch("matching.side_effects.capture_event") as mock_capture:
        track_match_accepted(match, match.ambassador_registration)

    mock_capture.assert_not_called()


def test_track_match_accepted_fires_when_match_is_pending() -> None:
    """track_match_accepted fires on the PENDING (first-accept) status."""
    match = MatchFactory.create(pending=True)

    with patch("matching.side_effects.capture_event") as mock_capture:
        track_match_accepted(match, match.ambassador_registration)

    mock_capture.assert_called_once_with(
        str(match.ambassador_registration.user.pk),
        "match_accepted",
        {"role": match.ambassador_registration.role},
    )


def test_track_match_confirmed_noops_when_match_is_pending() -> None:
    """track_match_confirmed only fires on ACCEPTED; PENDING is a no-op."""
    match = MatchFactory.create(pending=True)

    with patch("matching.side_effects.capture_event") as mock_capture:
        track_match_confirmed(match, match.referee_registration)

    mock_capture.assert_not_called()


def test_track_match_confirmed_fires_when_match_is_accepted() -> None:
    """track_match_confirmed fires on the ACCEPTED (mutual-accept) status."""
    match = MatchFactory.create(accepted=True)

    with patch("matching.side_effects.capture_event") as mock_capture:
        track_match_confirmed(match, match.referee_registration)

    mock_capture.assert_called_once_with(
        str(match.referee_registration.user.pk),
        "match_confirmed",
        {"role": match.referee_registration.role},
    )


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
    match = MatchFactory.create()

    with registry.disable_side_effects() as events:
        with TestCase.captureOnCommitCallbacks(execute=True):
            record_decline(match, match.ambassador_registration)

    assert events == [MATCH_DECLINED]
    assert len(mail.outbox) == 0


# ---------------------------------------------------------------------------
# N+1 regression guards — the match_proposed / match_expired handlers read
# registration.user.email straight off the match's FK accessors, so the
# query that produces (or reloads) the match must select_related the users.
#
# Asserted via a query-count ceiling (django_assert_max_num_queries) around
# the whole call, rather than a post-hoc cache-presence check: both
# transitions dispatch their handlers (which read `.user.email`, themselves
# caching it as a side effect) inside the same call under test, so a
# cache-presence assertion taken *after* the call already reflects whatever
# the handlers triggered — it cannot distinguish "prefetched" from "lazily
# loaded during dispatch". A missing select_related shows up here as two
# extra `auth_user` SELECTs (one per side); the budget below is the measured
# query count *with* the fix applied, so it fails (num_performed > num) the
# moment either extra query reappears.
# ---------------------------------------------------------------------------


def test_propose_match_does_not_lazy_load_either_sides_user(
    django_assert_max_num_queries: DjangoAssertNumQueries,
) -> None:
    """propose_match + its match_proposed handlers cost no extra per-side query.

    Regression guard: the handlers access
    match.ambassador_registration.user.email / ...referee_registration.user.email
    off the created match (via return_value); a missing select_related on
    propose_match's post-create reload fires one extra `auth_user` SELECT per
    side when the handlers run (measured: 5 queries without the fix, 4 with).
    """
    RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    # Re-fetch bare (mirroring run_matching's per-ambassador re-fetch) so the
    # ambassador's `user` starts uncached — the passed-in factory instance
    # would otherwise carry its own cached `user` into propose_match.
    ambassador_fresh = Registration.objects.get(pk=ambassador.pk)

    # django_assert_max_num_queries must be the OUTER context manager: the
    # handlers' queries run when captureOnCommitCallbacks(execute=True)
    # fires its deferred callbacks at __exit__, which happens after an inner
    # block would already have closed and stopped counting.
    with django_assert_max_num_queries(4):
        with TestCase.captureOnCommitCallbacks(execute=True):
            match = propose_match(ambassador_fresh)

    assert match is not None
    assert len(mail.outbox) == 2


def test_expire_lapsed_matches_does_not_lazy_load_either_sides_user(
    django_assert_max_num_queries: DjangoAssertNumQueries,
) -> None:
    """The sweep + its match_expired handlers cost no extra per-side query.

    Regression guard: the handlers access
    match.ambassador_registration.user.email / ...referee_registration.user.email;
    a missing select_related on the sweep's per-match fetch fires one extra
    `auth_user` SELECT per side when the handlers run (measured: 11 queries
    without the fix, 9 with, for this single-match sweep).
    """
    past = datetime(2020, 1, 1, tzinfo=UTC)
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=past,
    )

    # django_assert_max_num_queries must be the OUTER context manager — see
    # the comment in test_propose_match_does_not_lazy_load_either_sides_user.
    with django_assert_max_num_queries(9):
        with TestCase.captureOnCommitCallbacks(execute=True):
            count = expire_lapsed_matches(cutoff=datetime.now(UTC))

    assert count == 1
    assert len(mail.outbox) == 2
