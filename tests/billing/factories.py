# Test factories for the billing domain.

import factory

from billing.models import Payment, Tip
from tests.matching.factories import RegistrationFactory


class PaymentFactory(factory.django.DjangoModelFactory[Payment]):
    """Factory for Payment (HELD by default, amount_chf=5).

    FK to a fresh RegistrationFactory by default.
    """

    class Meta:
        model = Payment

    registration = factory.SubFactory(RegistrationFactory)
    amount_chf = 5
    status = Payment.Status.HELD
    reason = ""
    stripe_customer_id = factory.Sequence(lambda n: f"cus_test{n:04d}")
    stripe_payment_intent_id = factory.Sequence(lambda n: f"pi_test{n:04d}")
    stripe_refund_id = ""


class TipFactory(factory.django.DjangoModelFactory[Tip]):
    """Factory for Tip (PAID by default, amount_chf=5).

    FK to a fresh RegistrationFactory by default.
    """

    class Meta:
        model = Tip

    registration = factory.SubFactory(RegistrationFactory)
    amount_chf = 5
    message = ""
    status = Tip.Status.PAID
    stripe_customer_id = factory.Sequence(lambda n: f"cus_tip_test{n:04d}")
    stripe_payment_intent_id = factory.Sequence(lambda n: f"pi_tip_test{n:04d}")
    stripe_refund_id = ""
