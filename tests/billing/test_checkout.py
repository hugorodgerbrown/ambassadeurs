# Tests for billing.services.checkout: Stripe hosted Checkout session
# creation, idempotent deposit recording, webhook signature verification,
# and the finalize_paid_registration funnel. Stripe is always mocked
# (monkeypatch stripe.checkout.Session.create/.retrieve and
# stripe.Webhook.construct_event) — no test in this module makes a real
# network call.

from __future__ import annotations

from typing import Any

import pytest
import stripe

from billing.models import Payment
from billing.services.checkout import (
    create_checkout_session,
    finalize_paid_registration,
    record_deposit_paid,
    verify_webhook,
)
from matching.models import Registration
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


class _FakeCheckoutSession:
    """Minimal stand-in for a stripe.checkout.Session object."""

    def __init__(
        self,
        session_id: str = "cs_test0001",
        url: str = "https://checkout.stripe.com/pay/cs_test0001",
    ) -> None:
        self.id = session_id
        self.url = url


def _mock_session_create(
    monkeypatch: pytest.MonkeyPatch, **overrides: Any
) -> list[dict[str, Any]]:
    """Monkeypatch stripe.checkout.Session.create; return the call kwargs list."""
    calls: list[dict[str, Any]] = []

    def _fake_create(**kwargs: Any) -> _FakeCheckoutSession:
        calls.append(kwargs)
        return _FakeCheckoutSession(**overrides)

    monkeypatch.setattr(stripe.checkout.Session, "create", _fake_create)
    return calls


# ---------------------------------------------------------------------------
# create_checkout_session
# ---------------------------------------------------------------------------


def test_create_checkout_session_calls_stripe_with_expected_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_checkout_session sends the expected mode/amount/methods/metadata."""
    calls = _mock_session_create(monkeypatch)
    registration = RegistrationFactory.create(
        status=Registration.Status.UNVERIFIED, fee_chf=5
    )

    create_checkout_session(
        registration,
        success_url="https://example.com/return/",
        cancel_url="https://example.com/cancel/",
    )

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["mode"] == "payment"
    assert kwargs["payment_method_types"] == ["card", "twint"]
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 500
    assert kwargs["line_items"][0]["price_data"]["currency"] == "chf"
    assert kwargs["customer_email"] == registration.user.email
    assert kwargs["metadata"] == {"registration_pk": str(registration.pk)}
    assert kwargs["idempotency_key"] == f"checkout-registration-{registration.pk}"
    assert kwargs["success_url"] == "https://example.com/return/"
    assert kwargs["cancel_url"] == "https://example.com/cancel/"


def test_create_checkout_session_returns_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_checkout_session returns the Session object from Stripe."""
    _mock_session_create(
        monkeypatch, session_id="cs_abc", url="https://checkout.stripe.com/pay/cs_abc"
    )
    registration = RegistrationFactory.create(fee_chf=10)

    session = create_checkout_session(
        registration,
        success_url="https://example.com/return/",
        cancel_url="https://example.com/cancel/",
    )

    assert session.id == "cs_abc"
    assert session.url == "https://checkout.stripe.com/pay/cs_abc"


def test_create_checkout_session_configures_stripe_api_key(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """create_checkout_session sets stripe.api_key from settings at call time."""
    _mock_session_create(monkeypatch)
    settings.STRIPE_SECRET_KEY = "sk_test_from_settings"  # noqa: S105
    registration = RegistrationFactory.create(fee_chf=5)

    create_checkout_session(
        registration,
        success_url="https://example.com/",
        cancel_url="https://example.com/",
    )

    assert stripe.api_key == "sk_test_from_settings"


# ---------------------------------------------------------------------------
# record_deposit_paid
# ---------------------------------------------------------------------------


def test_record_deposit_paid_creates_held_payment() -> None:
    """record_deposit_paid creates a HELD Payment when none exists yet."""
    registration = RegistrationFactory.create(fee_chf=10)

    payment, created = record_deposit_paid(
        registration=registration,
        stripe_customer_id="cus_test0001",
        stripe_payment_intent_id="pi_test0001",
    )

    assert created is True
    assert payment.status == Payment.Status.HELD
    assert payment.amount_chf == 10
    assert payment.registration_id == registration.pk
    assert payment.stripe_customer_id == "cus_test0001"
    assert payment.stripe_payment_intent_id == "pi_test0001"
    assert Payment.objects.count() == 1


def test_record_deposit_paid_is_idempotent_on_payment_intent_id() -> None:
    """A second call with the same stripe_payment_intent_id is a no-op."""
    registration = RegistrationFactory.create(fee_chf=10)

    first, first_created = record_deposit_paid(
        registration=registration,
        stripe_customer_id="cus_1",
        stripe_payment_intent_id="pi_shared",
    )
    second, second_created = record_deposit_paid(
        registration=registration,
        stripe_customer_id="cus_1",
        stripe_payment_intent_id="pi_shared",
    )

    assert first_created is True
    assert second_created is False
    assert first.pk == second.pk
    assert Payment.objects.count() == 1


# ---------------------------------------------------------------------------
# verify_webhook
# ---------------------------------------------------------------------------


def test_verify_webhook_calls_stripe_construct_event(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """verify_webhook calls construct_event with the configured webhook secret."""
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"  # noqa: S105
    calls: list[tuple[Any, ...]] = []

    def _fake_construct_event(payload: bytes, sig_header: str, secret: str) -> str:
        calls.append((payload, sig_header, secret))
        return "fake-event"

    monkeypatch.setattr(stripe.Webhook, "construct_event", _fake_construct_event)

    result = verify_webhook(b"{}", "sig-header-value")

    assert result == "fake-event"
    assert calls == [(b"{}", "sig-header-value", "whsec_test")]


def test_verify_webhook_raises_on_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_webhook propagates SignatureVerificationError from Stripe."""

    def _fake_construct_event(*args: Any, **kwargs: Any) -> None:
        raise stripe.error.SignatureVerificationError("bad signature", "sig-header")

    monkeypatch.setattr(stripe.Webhook, "construct_event", _fake_construct_event)

    with pytest.raises(stripe.error.SignatureVerificationError):
        verify_webhook(b"{}", "bad-sig")


# ---------------------------------------------------------------------------
# finalize_paid_registration
# ---------------------------------------------------------------------------


def test_finalize_paid_registration_records_payment_and_confirms() -> None:
    """finalize_paid_registration creates one HELD Payment and verifies
    the registration."""
    registration = RegistrationFactory.create(
        status=Registration.Status.UNVERIFIED, fee_chf=5
    )

    result = finalize_paid_registration(
        registration,
        stripe_customer_id="cus_test0001",
        stripe_payment_intent_id="pi_test0001",
    )

    assert result.status == Registration.Status.VERIFIED
    assert Payment.objects.count() == 1
    payment = Payment.objects.get()
    assert payment.status == Payment.Status.HELD
    assert payment.stripe_payment_intent_id == "pi_test0001"


def test_finalize_paid_registration_is_idempotent() -> None:
    """A second call for the same payment intent creates no extra Payment and
    does not error on the already-VERIFIED registration."""
    registration = RegistrationFactory.create(
        status=Registration.Status.UNVERIFIED, fee_chf=5
    )

    finalize_paid_registration(
        registration,
        stripe_customer_id="cus_test0001",
        stripe_payment_intent_id="pi_test0001",
    )
    registration.refresh_from_db()
    result = finalize_paid_registration(
        registration,
        stripe_customer_id="cus_test0001",
        stripe_payment_intent_id="pi_test0001",
    )

    assert result.status == Registration.Status.VERIFIED
    assert Payment.objects.count() == 1
