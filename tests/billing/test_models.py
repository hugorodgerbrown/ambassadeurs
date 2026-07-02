# Tests for the Payment model and PaymentQuerySet.

import pytest

from billing.models import Payment
from tests.billing.factories import PaymentFactory
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
