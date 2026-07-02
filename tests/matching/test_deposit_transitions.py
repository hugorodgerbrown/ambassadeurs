# Tests for the VERB-87 deposit transitions wired into the match lifecycle and
# the close_season season-end refund sweep.
#
# Covers three surfaces:
#   - capture on mutual accept (record_acceptance): both parties' HELD deposits
#     become CAPTURED; a free-tier party with no Payment is skipped; a one-sided
#     accept (PENDING) captures nothing.
#   - forfeit on post-accept no-show (report_no_show): only the accused's HELD
#     deposit is FORFEITED; the reporter's stays HELD.
#   - close_season: refunds exactly the HELD + no-ACCEPTED-match + not-SUSPENDED
#     set; dry-run writes nothing; the command exits non-zero on a partial
#     failure and is quiet at --verbosity 0.
#
# Stripe is always mocked on refund paths (monkeypatch stripe.Refund.create) —
# no test makes a real network call. capture()/forfeit() are pure state
# transitions and need no mock.

from io import StringIO
from typing import Any

import pytest
import stripe
from django.core.management import call_command
from django.core.management.base import CommandError

from billing.models import Payment
from matching.models import Match, Registration
from matching.services import close_season, record_acceptance, report_no_show
from tests.billing.factories import PaymentFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


class _FakeRefund:
    """Minimal stand-in for a stripe.Refund object."""

    def __init__(self, refund_id: str = "re_test0001") -> None:
        self.id = refund_id


def _mock_refund_create(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Monkeypatch stripe.Refund.create and return the list of call kwargs."""
    calls: list[dict[str, Any]] = []

    def _fake_create(**kwargs: Any) -> _FakeRefund:
        calls.append(kwargs)
        return _FakeRefund()

    monkeypatch.setattr(stripe.Refund, "create", _fake_create)
    return calls


def _proposed_match() -> Match:
    """Create a fresh PROPOSED match between a new ambassador and referee."""
    ambassador = RegistrationFactory.create()
    referee = RegistrationFactory.create(referee=True)
    return MatchFactory.create(
        ambassador_registration=ambassador,
        referee_registration=referee,
    )


# ---------------------------------------------------------------------------
# capture on mutual accept (record_acceptance)
# ---------------------------------------------------------------------------


def test_mutual_accept_captures_both_deposits() -> None:
    """Both parties' HELD deposits become CAPTURED on the second accept."""
    match = _proposed_match()
    amb_deposit = PaymentFactory.create(
        registration=match.ambassador_registration, status=Payment.Status.HELD
    )
    ref_deposit = PaymentFactory.create(
        registration=match.referee_registration, status=Payment.Status.HELD
    )

    # First accept → PENDING; second accept → ACCEPTED (captures).
    record_acceptance(match, match.ambassador_registration)
    result = record_acceptance(match, match.referee_registration)

    assert result.status == Match.Status.ACCEPTED

    amb_deposit.refresh_from_db()
    ref_deposit.refresh_from_db()
    assert amb_deposit.status == Payment.Status.CAPTURED
    assert amb_deposit.reason == Payment.Reason.SUCCESSFUL_MATCH
    assert ref_deposit.status == Payment.Status.CAPTURED
    assert ref_deposit.reason == Payment.Reason.SUCCESSFUL_MATCH


def test_mutual_accept_skips_free_tier_party_without_payment() -> None:
    """A party with no Payment (free tier) is skipped; the other is captured."""
    match = _proposed_match()
    # Only the ambassador has a deposit; the referee is free-tier (no Payment).
    amb_deposit = PaymentFactory.create(
        registration=match.ambassador_registration, status=Payment.Status.HELD
    )

    record_acceptance(match, match.ambassador_registration)
    result = record_acceptance(match, match.referee_registration)

    assert result.status == Match.Status.ACCEPTED

    amb_deposit.refresh_from_db()
    assert amb_deposit.status == Payment.Status.CAPTURED
    # No Payment exists for the free-tier referee, and nothing errored.
    assert not Payment.objects.for_registration(match.referee_registration).exists()


def test_one_sided_accept_captures_nothing() -> None:
    """A first (one-sided) accept leaves the match PENDING and both deposits HELD."""
    match = _proposed_match()
    amb_deposit = PaymentFactory.create(
        registration=match.ambassador_registration, status=Payment.Status.HELD
    )
    ref_deposit = PaymentFactory.create(
        registration=match.referee_registration, status=Payment.Status.HELD
    )

    result = record_acceptance(match, match.ambassador_registration)

    assert result.status == Match.Status.PENDING

    amb_deposit.refresh_from_db()
    ref_deposit.refresh_from_db()
    assert amb_deposit.status == Payment.Status.HELD
    assert ref_deposit.status == Payment.Status.HELD


# ---------------------------------------------------------------------------
# forfeit on post-accept no-show (report_no_show)
# ---------------------------------------------------------------------------


def test_no_show_forfeits_accused_deposit_only() -> None:
    """report_no_show forfeits the accused's HELD deposit; reporter's stays HELD."""
    match = MatchFactory.create(accepted=True)
    reporter = match.ambassador_registration  # ambassador reports the referee
    accused = match.referee_registration
    reporter_deposit = PaymentFactory.create(
        registration=reporter, status=Payment.Status.HELD
    )
    accused_deposit = PaymentFactory.create(
        registration=accused, status=Payment.Status.HELD
    )

    report_no_show(match, reporter)

    accused_deposit.refresh_from_db()
    reporter_deposit.refresh_from_db()
    assert accused_deposit.status == Payment.Status.FORFEITED
    assert accused_deposit.reason == Payment.Reason.POST_ACCEPT_NOSHOW
    assert reporter_deposit.status == Payment.Status.HELD


def test_no_show_with_free_tier_accused_does_not_error() -> None:
    """report_no_show against a free-tier accused (no Payment) succeeds cleanly."""
    match = MatchFactory.create(accepted=True)
    reporter = match.ambassador_registration
    accused = match.referee_registration
    # Only the reporter has a deposit; the accused is free-tier.
    reporter_deposit = PaymentFactory.create(
        registration=reporter, status=Payment.Status.HELD
    )

    result = report_no_show(match, reporter)

    assert result.status == Match.Status.CANCELLED
    accused.refresh_from_db()
    assert accused.status == Registration.Status.SUSPENDED
    reporter_deposit.refresh_from_db()
    assert reporter_deposit.status == Payment.Status.HELD
    assert not Payment.objects.for_registration(accused).exists()


# ---------------------------------------------------------------------------
# close_season — the season-end refund sweep
# ---------------------------------------------------------------------------


def _held_payment(*, status: str = Registration.Status.VERIFIED) -> Payment:
    """Create a HELD Payment on a fresh registration with the given status."""
    registration = RegistrationFactory.create(status=status)
    return PaymentFactory.create(registration=registration, status=Payment.Status.HELD)


def test_close_season_refunds_only_the_eligible_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refunds exactly HELD + no-accepted-match + not-suspended; leaves the rest."""
    _mock_refund_create(monkeypatch)

    # Eligible: HELD, VERIFIED, no accepted match.
    verified = _held_payment(status=Registration.Status.VERIFIED)
    # Eligible: HELD, PAUSED (lenient — deposit stays refundable, ADR 0013).
    paused = _held_payment(status=Registration.Status.PAUSED)

    # Ineligible: SUSPENDED registration (post-accept no-show already forfeited).
    suspended = _held_payment(status=Registration.Status.SUSPENDED)

    # Ineligible: registration currently in an ACCEPTED match.
    accepted_match = MatchFactory.create(accepted=True)
    in_accepted_match = PaymentFactory.create(
        registration=accepted_match.ambassador_registration,
        status=Payment.Status.HELD,
    )

    # Ineligible: already-terminal payments.
    captured = PaymentFactory.create(status=Payment.Status.CAPTURED)
    forfeited = PaymentFactory.create(status=Payment.Status.FORFEITED)
    already_refunded = PaymentFactory.create(status=Payment.Status.REFUNDED)

    refunded, failed = close_season(commit=True)

    assert (refunded, failed) == (2, 0)

    verified.refresh_from_db()
    paused.refresh_from_db()
    assert verified.status == Payment.Status.REFUNDED
    assert verified.reason == Payment.Reason.SEASON_END_NO_MATCH
    assert paused.status == Payment.Status.REFUNDED

    # Everything else is untouched.
    suspended.refresh_from_db()
    in_accepted_match.refresh_from_db()
    captured.refresh_from_db()
    forfeited.refresh_from_db()
    already_refunded.refresh_from_db()
    assert suspended.status == Payment.Status.HELD
    assert in_accepted_match.status == Payment.Status.HELD
    assert captured.status == Payment.Status.CAPTURED
    assert forfeited.status == Payment.Status.FORFEITED
    assert already_refunded.status == Payment.Status.REFUNDED


def test_close_season_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run reports the would-refund count, makes no Stripe call, writes nothing."""
    calls = _mock_refund_create(monkeypatch)
    p1 = _held_payment()
    p2 = _held_payment()

    refunded, failed = close_season(commit=False)

    assert (refunded, failed) == (2, 0)
    assert calls == []  # no Stripe round-trip in dry-run
    p1.refresh_from_db()
    p2.refresh_from_db()
    assert p1.status == Payment.Status.HELD
    assert p2.status == Payment.Status.HELD


def test_close_season_commit_calls_stripe_per_payment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--commit issues one Stripe refund per eligible deposit."""
    calls = _mock_refund_create(monkeypatch)
    _held_payment()
    _held_payment()

    refunded, failed = close_season(commit=True)

    assert (refunded, failed) == (2, 0)
    assert len(calls) == 2


def test_close_season_skips_payment_no_longer_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deposit transitioned out of HELD mid-sweep is skipped (not a failure).

    Simulates a race: the candidate PK list is built while both deposits are
    HELD, but a concurrent capture flips the second before the loop reaches it.
    The per-payment HELD re-check must skip it without counting a failure.
    """
    _mock_refund_create(monkeypatch)
    _held_payment()
    _held_payment()

    import matching.services as svc

    _real_refund = svc.refund

    def _refund_then_race(payment: Payment, **kwargs: Any) -> Payment:
        """Capture every other still-HELD deposit before delegating the refund."""
        Payment.objects.filter(status=Payment.Status.HELD).exclude(
            pk=payment.pk
        ).update(status=Payment.Status.CAPTURED)
        return _real_refund(payment, **kwargs)

    monkeypatch.setattr(svc, "refund", _refund_then_race)

    refunded, failed = close_season(commit=True)

    # Only the first deposit is refunded; the second is skipped (no longer HELD)
    # and is not counted as a failure.
    assert (refunded, failed) == (1, 0)


# ---------------------------------------------------------------------------
# close_season management command
# ---------------------------------------------------------------------------


def test_command_dry_run_reports_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare (dry-run) invocation reports the would-refund count and writes nothing."""
    calls = _mock_refund_create(monkeypatch)
    payment = _held_payment()

    stdout = StringIO()
    call_command("close_season", stdout=stdout)

    output = stdout.getvalue()
    assert "would refund 1" in output
    assert calls == []
    payment.refresh_from_db()
    assert payment.status == Payment.Status.HELD


def test_command_commit_refunds(monkeypatch: pytest.MonkeyPatch) -> None:
    """--commit refunds the eligible deposits and reports the count."""
    _mock_refund_create(monkeypatch)
    payment = _held_payment()

    stdout = StringIO()
    call_command("close_season", "--commit", stdout=stdout)

    output = stdout.getvalue()
    assert "Refunded 1" in output
    payment.refresh_from_db()
    assert payment.status == Payment.Status.REFUNDED


def test_command_exits_non_zero_on_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refund failure mid-batch raises CommandError; the other still refunds."""
    _mock_refund_create(monkeypatch)
    _held_payment()
    _held_payment()

    # Fail the first refund, delegate to the real transition thereafter.
    import matching.services as svc

    _real_refund = svc.refund
    _call_count = {"n": 0}

    def _failing_refund(payment: Payment, **kwargs: Any) -> Payment:
        """Raise on the first invocation; delegate to the real refund after."""
        _call_count["n"] += 1
        if _call_count["n"] == 1:
            raise RuntimeError("simulated Stripe failure")
        return _real_refund(payment, **kwargs)

    monkeypatch.setattr(svc, "refund", _failing_refund)

    with pytest.raises(CommandError):
        call_command("close_season", "--commit")

    # Exactly one of the two deposits was refunded (the second); the failed one
    # is left HELD.
    statuses = set(Payment.objects.values_list("status", flat=True))
    assert Payment.Status.REFUNDED in statuses
    assert Payment.Status.HELD in statuses


def test_command_verbosity_zero_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """--verbosity 0 produces no stdout output."""
    _mock_refund_create(monkeypatch)
    _held_payment()

    stdout = StringIO()
    call_command("close_season", verbosity=0, stdout=stdout)

    assert stdout.getvalue() == ""
