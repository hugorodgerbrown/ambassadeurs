# Stripe hosted Checkout flow for the prepaid registration deposit (VERB-86,
# ADR 0014).
#
# The Payment row is created on payment COMPLETION, not at Checkout session
# creation — HELD means "funds collected", so it would be wrong to create one
# before Stripe confirms money moved. This keeps billing.models.Payment (and
# its VERB-85 migration) untouched.
#
# finalize_paid_registration is the single funnel both the success-redirect
# view (public.views.register_payment_return, fast UX) and the
# checkout.session.completed webhook (public.views.stripe_webhook, source of
# truth) call — record_deposit_paid is idempotent on stripe_payment_intent_id
# and matching.services.confirm_registration is idempotent on registration
# status, so calling the funnel twice for the same event is always safe.
#
# _configure_stripe and to_centimes are reused from billing.services.payments
# rather than duplicated here.

from __future__ import annotations

import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from matching.models import Registration
from matching.services import confirm_registration

from ..models import Payment
from .payments import _configure_stripe, to_centimes

logger = logging.getLogger(__name__)


def create_checkout_session(
    registration: Registration,
    *,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    """Create a Stripe hosted Checkout Session for a paid-tier deposit.

    ``mode="payment"`` with a single line item for ``registration.fee_chf``
    (converted to centimes via ``to_centimes``), offering both card and TWINT
    (ADR 0014). The idempotency key is stable per registration, so a
    double-submit (e.g. a user double-clicking "Pay") replays the same
    session rather than creating a duplicate.

    Args:
        registration: The UNVERIFIED, fee_chf > 0 registration paying the
            deposit.
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
                    "unit_amount": to_centimes(registration.fee_chf),
                    "product_data": {
                        "name": _("4 Vallées Ambassadors — registration deposit"),
                    },
                },
                "quantity": 1,
            }
        ],
        customer_email=registration.user.email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"registration_pk": str(registration.pk)},
        idempotency_key=f"checkout-registration-{registration.pk}",
    )
    logger.info(
        "Created Stripe Checkout session id=%s for registration pk=%s",
        session.id,
        registration.pk,
    )
    return session


def retrieve_checkout_session(session_id: str) -> stripe.checkout.Session:
    """Retrieve a previously-created Checkout Session by id.

    Used by ``public.views.register_payment_return`` to check whether the
    payer completed payment on the success redirect.

    Args:
        session_id: The Stripe Checkout Session id (``?session_id=`` on the
            success redirect).

    Returns:
        The retrieved ``stripe.checkout.Session``.
    """
    _configure_stripe()
    return stripe.checkout.Session.retrieve(session_id)


def record_deposit_paid(
    *,
    registration: Registration,
    stripe_customer_id: str,
    stripe_payment_intent_id: str,
) -> tuple[Payment, bool]:
    """Idempotently record a HELD Payment for a completed Stripe payment.

    Idempotent on ``stripe_payment_intent_id``: if a Payment already exists
    for this payment intent (the return view and the webhook both racing to
    record the same completion), the existing row is returned unchanged
    rather than creating a duplicate. Callers that need this to be race-safe
    across concurrent requests should hold a lock on ``registration`` first
    (``finalize_paid_registration`` does this).

    Args:
        registration: The registration the deposit belongs to.
        stripe_customer_id: The Stripe Customer id from the completed session.
        stripe_payment_intent_id: The Stripe PaymentIntent id from the
            completed session — the idempotency key for this function.

    Returns:
        A ``(payment, created)`` tuple, mirroring
        ``QuerySet.get_or_create``'s return shape.
    """
    with transaction.atomic():
        existing = Payment.objects.filter(
            stripe_payment_intent_id=stripe_payment_intent_id
        ).first()
        if existing is not None:
            logger.info(
                "record_deposit_paid: Payment already recorded for "
                "stripe_payment_intent_id=%s (pk=%s); no-op.",
                stripe_payment_intent_id,
                existing.pk,
            )
            return existing, False

        payment = Payment.objects.create(
            registration=registration,
            amount_chf=registration.fee_chf,
            status=Payment.Status.HELD,
            stripe_customer_id=stripe_customer_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
    logger.info(
        "record_deposit_paid: created HELD Payment pk=%s for registration pk=%s "
        "(amount_chf=%s, stripe_payment_intent_id=%s)",
        payment.pk,
        registration.pk,
        payment.amount_chf,
        stripe_payment_intent_id,
    )
    return payment, True


def verify_webhook(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and parse an incoming Stripe webhook request body.

    Args:
        payload: The raw request body bytes.
        sig_header: The ``Stripe-Signature`` request header.

    Returns:
        The verified ``stripe.Event``.

    Raises:
        ValueError: if the payload is not valid JSON.
        stripe.error.SignatureVerificationError: if the signature does not
            match ``settings.STRIPE_WEBHOOK_SECRET``.
    """
    return stripe.Webhook.construct_event(  # type: ignore[no-any-return,no-untyped-call]  # stripe.Webhook.construct_event has no type stub
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )


def finalize_paid_registration(
    registration: Registration,
    *,
    stripe_customer_id: str,
    stripe_payment_intent_id: str,
) -> Registration:
    """Record the deposit and confirm the registration — the single funnel.

    Both ``public.views.register_payment_return`` (fast UX, on the success
    redirect) and ``public.views.stripe_webhook``
    (``checkout.session.completed``, source of truth) call this. Locks the
    registration row for the duration of the transaction so that a near-
    simultaneous call from the other path serialises behind it rather than
    racing ``record_deposit_paid``'s check-then-create.

    Args:
        registration: The UNVERIFIED registration that has just paid.
        stripe_customer_id: The Stripe Customer id from the completed session.
        stripe_payment_intent_id: The Stripe PaymentIntent id from the
            completed session.

    Returns:
        The registration, refreshed to VERIFIED (or unchanged if it was
        already confirmed by the other path).
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        record_deposit_paid(
            registration=locked,
            stripe_customer_id=stripe_customer_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        locked = confirm_registration(locked)
    logger.info(
        "finalize_paid_registration: registration pk=%s finalized (status=%s)",
        locked.pk,
        locked.status,
    )
    return locked
