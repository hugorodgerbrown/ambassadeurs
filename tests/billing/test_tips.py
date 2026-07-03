# Tests for billing.services.tips: Stripe hosted Checkout session creation
# for a voluntary contribution, and idempotent tip recording. Stripe is
# always mocked (monkeypatch stripe.checkout.Session.create) — no test in
# this module makes a real network call. Mirrors tests/billing/test_checkout.py.

from __future__ import annotations

from typing import Any

import pytest
import stripe

from billing.models import Tip
from billing.services.tips import create_tip_checkout_session, record_tip_paid
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
# create_tip_checkout_session
# ---------------------------------------------------------------------------


def test_create_tip_checkout_session_calls_stripe_with_expected_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_tip_checkout_session sends the expected mode/amount/methods/metadata."""
    calls = _mock_session_create(monkeypatch)
    registration = RegistrationFactory.create()

    create_tip_checkout_session(
        registration,
        amount_chf=10,
        message="Thanks for the help!",
        success_url="https://example.com/return/",
        cancel_url="https://example.com/cancel/",
    )

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["mode"] == "payment"
    assert kwargs["payment_method_types"] == ["card", "twint"]
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 1000
    assert kwargs["line_items"][0]["price_data"]["currency"] == "chf"
    assert kwargs["customer_email"] == registration.user.email
    assert kwargs["metadata"] == {
        "purpose": "tip",
        "registration_pk": str(registration.pk),
        "amount_chf": "10",
        "message": "Thanks for the help!",
    }
    assert kwargs["success_url"] == "https://example.com/return/"
    assert kwargs["cancel_url"] == "https://example.com/cancel/"


def test_create_tip_checkout_session_has_no_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike the deposit flow, no fixed idempotency key is set.

    A registrant may legitimately start several tip sessions with different
    amounts; a fixed key with changed params would make Stripe error.
    """
    calls = _mock_session_create(monkeypatch)
    registration = RegistrationFactory.create()

    create_tip_checkout_session(
        registration,
        amount_chf=5,
        message="",
        success_url="https://example.com/return/",
        cancel_url="https://example.com/cancel/",
    )

    assert "idempotency_key" not in calls[0]


def test_create_tip_checkout_session_returns_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_tip_checkout_session returns the Session object from Stripe."""
    _mock_session_create(
        monkeypatch, session_id="cs_abc", url="https://checkout.stripe.com/pay/cs_abc"
    )
    registration = RegistrationFactory.create()

    session = create_tip_checkout_session(
        registration,
        amount_chf=20,
        message="",
        success_url="https://example.com/return/",
        cancel_url="https://example.com/cancel/",
    )

    assert session.id == "cs_abc"
    assert session.url == "https://checkout.stripe.com/pay/cs_abc"


def test_create_tip_checkout_session_configures_stripe_api_key(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    """create_tip_checkout_session sets stripe.api_key from settings at call time."""
    _mock_session_create(monkeypatch)
    settings.STRIPE_SECRET_KEY = "sk_test_from_settings"  # noqa: S105
    registration = RegistrationFactory.create()

    create_tip_checkout_session(
        registration,
        amount_chf=5,
        message="",
        success_url="https://example.com/",
        cancel_url="https://example.com/",
    )

    assert stripe.api_key == "sk_test_from_settings"


# ---------------------------------------------------------------------------
# record_tip_paid
# ---------------------------------------------------------------------------


def test_record_tip_paid_creates_paid_tip() -> None:
    """record_tip_paid creates a PAID Tip when none exists yet."""
    registration = RegistrationFactory.create()

    tip, created = record_tip_paid(
        registration=registration,
        amount_chf=10,
        message="Cheers!",
        stripe_customer_id="cus_test0001",
        stripe_payment_intent_id="pi_test0001",
    )

    assert created is True
    assert tip.status == Tip.Status.PAID
    assert tip.amount_chf == 10
    assert tip.message == "Cheers!"
    assert tip.registration_id == registration.pk
    assert tip.stripe_customer_id == "cus_test0001"
    assert tip.stripe_payment_intent_id == "pi_test0001"
    assert Tip.objects.count() == 1


def test_record_tip_paid_is_idempotent_on_payment_intent_id() -> None:
    """A second call with the same stripe_payment_intent_id is a no-op."""
    registration = RegistrationFactory.create()

    first, first_created = record_tip_paid(
        registration=registration,
        amount_chf=10,
        message="",
        stripe_customer_id="cus_1",
        stripe_payment_intent_id="pi_shared",
    )
    second, second_created = record_tip_paid(
        registration=registration,
        amount_chf=10,
        message="",
        stripe_customer_id="cus_1",
        stripe_payment_intent_id="pi_shared",
    )

    assert first_created is True
    assert second_created is False
    assert first.pk == second.pk
    assert Tip.objects.count() == 1
