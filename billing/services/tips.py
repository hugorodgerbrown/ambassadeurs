# Stripe hosted Checkout flow for a voluntary contribution ("tip"), VERB-110.
#
# A tip is a separate money flow from the prepaid registration deposit
# (billing.services.checkout) — it has no match-outcome lifecycle, so the Tip
# row is created PAID directly on confirmed payment (mirroring how
# record_deposit_paid creates a HELD Payment on completion, never at session
# creation).
#
# Unlike create_checkout_session's fixed per-registration idempotency key, a
# tip session has NO fixed idempotency key: a registrant may legitimately
# start several tip sessions with different amounts, and a fixed key with
# changed params would make Stripe error.
#
# verify_webhook and retrieve_checkout_session are reused as-is from
# billing.services.checkout — no duplication.

from __future__ import annotations

import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from matching.models import Registration

from ..models import Tip
from .payments import _configure_stripe, to_centimes

logger = logging.getLogger(__name__)


def create_tip_checkout_session(
    registration: Registration,
    *,
    amount_chf: int,
    message: str,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    """Create a Stripe hosted Checkout Session for a voluntary contribution.

    ``mode="payment"`` with a single line item for ``amount_chf`` (converted
    to centimes via ``to_centimes``), offering both card and TWINT. No
    idempotency key is set — a registrant may legitimately start several tip
    sessions with different amounts, and a fixed key with changed params
    makes Stripe error.

    Args:
        registration: The registration making the contribution.
        amount_chf: The whole-CHF amount to charge (1-500, validated by the
            caller's form).
        message: An optional "say something nice" message, stored in
            metadata and later persisted onto the Tip row (staff-only,
            not displayed to the tipper's counterpart).
        success_url: Where Stripe redirects on success. Must contain the
            literal ``{CHECKOUT_SESSION_ID}`` placeholder, which Stripe
            substitutes with the real session id.
        cancel_url: Where Stripe redirects if the payer cancels.

    Returns:
        The created ``stripe.checkout.Session`` (``.url`` is the redirect
        target, ``.id`` the session id).
    """
    _configure_stripe()
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card", "twint"],
        line_items=[
            {
                "price_data": {
                    "currency": settings.STRIPE_CURRENCY,
                    "unit_amount": to_centimes(amount_chf),
                    "product_data": {
                        "name": _("4 Vallées Ambassadors — thank-you tip"),
                    },
                },
                "quantity": 1,
            }
        ],
        customer_email=registration.user.email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "purpose": "tip",
            "registration_pk": str(registration.pk),
            "amount_chf": str(amount_chf),
            "message": message,
        },
    )
    logger.info(
        "Created Stripe tip Checkout session id=%s for registration pk=%s",
        session.id,
        registration.pk,
    )
    return session


def record_tip_paid(
    *,
    registration: Registration,
    amount_chf: int,
    message: str,
    stripe_customer_id: str,
    stripe_payment_intent_id: str,
) -> tuple[Tip, bool]:
    """Idempotently record a PAID Tip for a completed Stripe payment.

    Idempotent on ``stripe_payment_intent_id``: if a Tip already exists for
    this payment intent (the return view and the webhook both racing to
    record the same completion), the existing row is returned unchanged
    rather than creating a duplicate. The row is only created on confirmed
    payment — an abandoned checkout leaves no DB trace.

    Args:
        registration: The registration the tip belongs to.
        amount_chf: The whole-CHF amount charged.
        message: The optional "say something nice" message.
        stripe_customer_id: The Stripe Customer id from the completed
            session.
        stripe_payment_intent_id: The Stripe PaymentIntent id from the
            completed session — the idempotency key for this function.

    Returns:
        A ``(tip, created)`` tuple, mirroring ``QuerySet.get_or_create``'s
        return shape.
    """
    with transaction.atomic():
        existing = Tip.objects.filter(
            stripe_payment_intent_id=stripe_payment_intent_id
        ).first()
        if existing is not None:
            logger.info(
                "record_tip_paid: Tip already recorded for "
                "stripe_payment_intent_id=%s (pk=%s); no-op.",
                stripe_payment_intent_id,
                existing.pk,
            )
            return existing, False

        tip = Tip.objects.create(
            registration=registration,
            amount_chf=amount_chf,
            message=message,
            status=Tip.Status.PAID,
            stripe_customer_id=stripe_customer_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
    logger.info(
        "record_tip_paid: created PAID Tip pk=%s for registration pk=%s "
        "(amount_chf=%s, stripe_payment_intent_id=%s)",
        tip.pk,
        registration.pk,
        tip.amount_chf,
        stripe_payment_intent_id,
    )
    return tip, True
