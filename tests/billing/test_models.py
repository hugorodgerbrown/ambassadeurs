# Tests for the Payment model and PaymentQuerySet, and the Tip model and
# TipQuerySet.

import pytest
from django.db import IntegrityError, transaction

from billing.models import Payment, Tip
from tests.billing.factories import PaymentFactory, TipFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_payment_to_string_contains_amount_and_status() -> None:
    """Payment.to_string contains the CHF amount and status label."""
    payment = PaymentFactory.create(amount_chf=5, status=Payment.Status.HELD)
    s = str(payment)
    assert "5" in s
    assert "Held" in s


def test_payment_default_status_is_held() -> None:
    """A newly-created payment defaults to HELD with a blank reason."""
    payment = PaymentFactory.create()
    assert payment.status == Payment.Status.HELD
    assert payment.reason == ""


def test_payment_ordering_is_most_recent_first() -> None:
    """Payment.Meta.ordering is -created_at (most recent first)."""
    first = PaymentFactory.create()
    second = PaymentFactory.create()
    assert list(Payment.objects.all())[:2] == [second, first]


def test_payment_registration_set_null_on_registration_delete() -> None:
    """Deleting the registration sets Payment.registration to NULL, not CASCADE.

    The audit row must survive account deletion (VERB-88).
    """
    registration = RegistrationFactory.create()
    payment = PaymentFactory.create(registration=registration)
    registration.delete()
    payment.refresh_from_db()
    assert payment.registration_id is None


# ---------------------------------------------------------------------------
# is_terminal
# ---------------------------------------------------------------------------


def test_is_terminal_false_while_held() -> None:
    """is_terminal is False for a HELD payment."""
    payment = PaymentFactory.create(status=Payment.Status.HELD)
    assert payment.is_terminal is False


@pytest.mark.parametrize(
    "status",
    [Payment.Status.CAPTURED, Payment.Status.REFUNDED, Payment.Status.FORFEITED],
)
def test_is_terminal_true_for_terminal_statuses(status: Payment.Status) -> None:
    """is_terminal is True for CAPTURED, REFUNDED, and FORFEITED."""
    payment = PaymentFactory.create(status=status)
    assert payment.is_terminal is True


# ---------------------------------------------------------------------------
# PaymentQuerySet
# ---------------------------------------------------------------------------


def test_queryset_held_returns_only_held_payments() -> None:
    """PaymentQuerySet.held excludes terminal payments."""
    held = PaymentFactory.create(status=Payment.Status.HELD)
    PaymentFactory.create(status=Payment.Status.CAPTURED)
    assert list(Payment.objects.held()) == [held]


def test_queryset_for_registration_filters_by_registration() -> None:
    """PaymentQuerySet.for_registration returns only that registration's rows."""
    registration = RegistrationFactory.create()
    mine = PaymentFactory.create(registration=registration)
    PaymentFactory.create()  # different registration
    assert list(Payment.objects.for_registration(registration)) == [mine]


# ---------------------------------------------------------------------------
# Tip
# ---------------------------------------------------------------------------


def test_tip_to_string_contains_amount_and_status() -> None:
    """Tip.to_string contains the CHF amount and status label."""
    tip = TipFactory.create(amount_chf=10, status=Tip.Status.PAID)
    s = str(tip)
    assert "10" in s
    assert "Paid" in s


def test_tip_default_status_is_paid() -> None:
    """A newly-created tip defaults to PAID."""
    tip = TipFactory.create()
    assert tip.status == Tip.Status.PAID


def test_tip_ordering_is_most_recent_first() -> None:
    """Tip.Meta.ordering is -created_at (most recent first)."""
    first = TipFactory.create()
    second = TipFactory.create()
    assert list(Tip.objects.all())[:2] == [second, first]


def test_tip_registration_set_null_on_registration_delete() -> None:
    """Deleting the registration sets Tip.registration to NULL, not CASCADE.

    The audit row must survive account deletion.
    """
    registration = RegistrationFactory.create()
    tip = TipFactory.create(registration=registration)
    registration.delete()
    tip.refresh_from_db()
    assert tip.registration_id is None


def test_tip_message_defaults_to_blank() -> None:
    """A tip with no message stores a blank string, not None."""
    tip = TipFactory.create()
    assert tip.message == ""


# ---------------------------------------------------------------------------
# TipQuerySet
# ---------------------------------------------------------------------------


def test_tip_queryset_paid_returns_only_paid_tips() -> None:
    """TipQuerySet.paid excludes refunded tips."""
    paid = TipFactory.create(status=Tip.Status.PAID)
    TipFactory.create(status=Tip.Status.REFUNDED)
    assert list(Tip.objects.paid()) == [paid]


def test_tip_queryset_for_registration_filters_by_registration() -> None:
    """TipQuerySet.for_registration returns only that registration's rows."""
    registration = RegistrationFactory.create()
    mine = TipFactory.create(registration=registration)
    TipFactory.create()  # different registration
    assert list(Tip.objects.for_registration(registration)) == [mine]


# ---------------------------------------------------------------------------
# unique_tip_stripe_payment_intent_id constraint
# ---------------------------------------------------------------------------


def test_duplicate_stripe_payment_intent_id_raises_integrity_error() -> None:
    """Two Tip rows with the same non-blank stripe_payment_intent_id collide.

    This is the database-level guard record_tip_paid relies on to degrade a
    create race to idempotency rather than a duplicate.
    """
    TipFactory.create(stripe_payment_intent_id="pi_dupe")
    with pytest.raises(IntegrityError), transaction.atomic():
        TipFactory.create(stripe_payment_intent_id="pi_dupe")


def test_blank_stripe_payment_intent_id_does_not_collide() -> None:
    """Multiple Tip rows with a blank stripe_payment_intent_id do not collide.

    The constraint's condition excludes the empty-string default, so this
    scenario (which record_tip_paid never actually creates — a Tip is only
    ever inserted with a real payment intent id) cannot break row creation.
    """
    TipFactory.create(stripe_payment_intent_id="")
    TipFactory.create(stripe_payment_intent_id="")
    assert Tip.objects.filter(stripe_payment_intent_id="").count() == 2
