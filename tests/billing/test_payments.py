# Tests for billing.services.payments: capture / forfeit / refund and
# to_centimes. Stripe is always mocked (monkeypatch stripe.Refund.create) —
# no test in this module makes a real network call.

from typing import Any

import pytest
import stripe

from billing.models import Payment
from billing.services.payments import (
    InvalidPaymentTransition,
    capture,
    forfeit,
    refund,
    to_centimes,
)
from core.models import StateTransitionLog
from tests.billing.factories import PaymentFactory

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


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_from_held_transitions_to_captured() -> None:
    """capture() on a HELD payment sets status=CAPTURED and the given reason."""
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    result = capture(payment, reason=Payment.Reason.SUCCESSFUL_MATCH)

    assert result.status == Payment.Status.CAPTURED
    assert result.reason == Payment.Reason.SUCCESSFUL_MATCH


def test_capture_writes_state_transition_log() -> None:
    """capture() writes one StateTransitionLog row for the status field."""
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    capture(payment)

    assert StateTransitionLog.objects.count() == 1
    log = StateTransitionLog.objects.get()
    assert log.state_before == Payment.Status.HELD
    assert log.state_after == Payment.Status.CAPTURED


def test_capture_default_reason_is_successful_match() -> None:
    """capture() defaults reason to SUCCESSFUL_MATCH when not given."""
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    result = capture(payment)

    assert result.reason == Payment.Reason.SUCCESSFUL_MATCH


def test_capture_makes_no_stripe_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """capture() never calls stripe.Refund.create (pure state transition)."""
    calls = _mock_refund_create(monkeypatch)
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    capture(payment)

    assert calls == []


@pytest.mark.parametrize(
    "status",
    [Payment.Status.CAPTURED, Payment.Status.REFUNDED, Payment.Status.FORFEITED],
)
def test_capture_raises_on_terminal_payment(status: Payment.Status) -> None:
    """capture() raises InvalidPaymentTransition on a non-HELD payment."""
    payment = PaymentFactory.create(status=status)

    with pytest.raises(InvalidPaymentTransition):
        capture(payment)


# ---------------------------------------------------------------------------
# forfeit
# ---------------------------------------------------------------------------


def test_forfeit_from_held_transitions_to_forfeited() -> None:
    """forfeit() on a HELD payment sets status=FORFEITED and the given reason."""
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    result = forfeit(payment, reason=Payment.Reason.POST_ACCEPT_NOSHOW)

    assert result.status == Payment.Status.FORFEITED
    assert result.reason == Payment.Reason.POST_ACCEPT_NOSHOW


def test_forfeit_writes_state_transition_log() -> None:
    """forfeit() writes one StateTransitionLog row for the status field."""
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    forfeit(payment)

    assert StateTransitionLog.objects.count() == 1
    log = StateTransitionLog.objects.get()
    assert log.state_before == Payment.Status.HELD
    assert log.state_after == Payment.Status.FORFEITED


def test_forfeit_makes_no_stripe_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """forfeit() never calls stripe.Refund.create (pure state transition)."""
    calls = _mock_refund_create(monkeypatch)
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    forfeit(payment)

    assert calls == []


@pytest.mark.parametrize(
    "status",
    [Payment.Status.CAPTURED, Payment.Status.REFUNDED, Payment.Status.FORFEITED],
)
def test_forfeit_raises_on_terminal_payment(status: Payment.Status) -> None:
    """forfeit() raises InvalidPaymentTransition on a non-HELD payment."""
    payment = PaymentFactory.create(status=status)

    with pytest.raises(InvalidPaymentTransition):
        forfeit(payment)


# ---------------------------------------------------------------------------
# refund
# ---------------------------------------------------------------------------


def test_refund_from_held_transitions_to_refunded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refund() on a HELD payment sets status=REFUNDED and the given reason."""
    _mock_refund_create(monkeypatch)
    payment = PaymentFactory.create(
        status=Payment.Status.HELD,
        stripe_payment_intent_id="pi_test0001",
    )

    result = refund(payment, reason=Payment.Reason.SEASON_END_NO_MATCH)

    assert result.status == Payment.Status.REFUNDED
    assert result.reason == Payment.Reason.SEASON_END_NO_MATCH


def test_refund_calls_stripe_refund_create_once_with_expected_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refund() calls stripe.Refund.create once with the expected kwargs."""
    calls = _mock_refund_create(monkeypatch)
    payment = PaymentFactory.create(
        status=Payment.Status.HELD,
        stripe_payment_intent_id="pi_test0001",
    )

    refund(payment, reason=Payment.Reason.USER_CANCELLED)

    assert len(calls) == 1
    assert calls[0]["payment_intent"] == "pi_test0001"
    assert calls[0]["idempotency_key"] == f"refund-payment-{payment.pk}"


def test_refund_stores_stripe_refund_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """refund() stores the id returned by stripe.Refund.create."""
    monkeypatch.setattr(
        stripe.Refund, "create", lambda **kwargs: _FakeRefund("re_abc123")
    )
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    result = refund(payment, reason=Payment.Reason.USER_CANCELLED)

    assert result.stripe_refund_id == "re_abc123"


def test_refund_writes_state_transition_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """refund() writes one StateTransitionLog row for the status field."""
    _mock_refund_create(monkeypatch)
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    refund(payment, reason=Payment.Reason.USER_CANCELLED)

    assert StateTransitionLog.objects.count() == 1
    log = StateTransitionLog.objects.get()
    assert log.state_before == Payment.Status.HELD
    assert log.state_after == Payment.Status.REFUNDED


@pytest.mark.parametrize(
    "status",
    [Payment.Status.CAPTURED, Payment.Status.REFUNDED, Payment.Status.FORFEITED],
)
def test_refund_raises_on_terminal_payment(
    status: Payment.Status, monkeypatch: pytest.MonkeyPatch
) -> None:
    """refund() raises InvalidPaymentTransition on a non-HELD payment."""
    calls = _mock_refund_create(monkeypatch)
    payment = PaymentFactory.create(status=status)

    with pytest.raises(InvalidPaymentTransition):
        refund(payment, reason=Payment.Reason.USER_CANCELLED)

    # The guard must fire before any Stripe call is made.
    assert calls == []


def test_refund_configures_stripe_api_key_from_settings(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """refund() sets stripe.api_key from settings.STRIPE_SECRET_KEY at call time."""
    _mock_refund_create(monkeypatch)
    # Not a real credential — a fixture value to prove the setting is read
    # lazily at call time, not cached at import time.
    settings.STRIPE_SECRET_KEY = "sk_test_from_settings"  # noqa: S105
    payment = PaymentFactory.create(status=Payment.Status.HELD)

    refund(payment, reason=Payment.Reason.USER_CANCELLED)

    assert stripe.api_key == "sk_test_from_settings"


# ---------------------------------------------------------------------------
# to_centimes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("amount_chf", "expected"), [(5, 500), (0, 0), (20, 2000)])
def test_to_centimes(amount_chf: int, expected: int) -> None:
    """to_centimes converts whole CHF to Stripe's minor unit."""
    assert to_centimes(amount_chf) == expected
