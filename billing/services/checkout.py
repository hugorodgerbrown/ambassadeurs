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
#
# handle_checkout_completed (VERB-142) is the source-of-truth dispatch behind
# public.views.stripe_webhook: it resolves the registration from the verified
# event and routes on metadata.purpose to either the tip finaliser
# (billing.services.tips.record_tip_paid) or the deposit finaliser
# (finalize_paid_registration) below. _stripe_metadata_get and
# _session_payment_intent_id live here (rather than in public.views) because
# they are Stripe-generic helpers the dispatch itself needs; public.views
# imports them back for the return-view flows that still need to inspect a
# session directly.

from __future__ import annotations

import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from matching.models import Registration
from matching.services import confirm_registration

from ..models import Payment, Tip
from .payments import _configure_stripe, record_payment_refunded, to_centimes
from .tips import _parse_tip_amount_chf, record_tip_paid, record_tip_refunded

logger = logging.getLogger(__name__)


def _stripe_metadata_get(obj: object, key: str) -> str | None:
    """Return the string metadata value ``obj.metadata[key]``, or None.

    Stripe's ``StripeObject`` deliberately has no ``.get()`` method (calling
    it raises ``AttributeError``) — read defensively via ``getattr`` (which
    tolerates a missing ``metadata`` attribute) plus membership and subscript
    access instead of assuming metadata, or the key within it, is present.
    """
    metadata = getattr(obj, "metadata", None)
    if metadata is None or key not in metadata:
        return None
    return str(metadata[key])


def _session_payment_intent_id(session: stripe.checkout.Session) -> str:
    """Return the session's payment_intent id as a string, '' when absent.

    The payment_intent may be an expandable object rather than an id string —
    narrowed to a plain id string, or '' when absent.
    """
    return session.payment_intent if isinstance(session.payment_intent, str) else ""


def create_checkout_session(
    registration: Registration,
    *,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    """Create a Stripe hosted Checkout Session for a paid-tier deposit.

    ``mode="payment"`` with a single line item for ``registration.fee_chf``
    (converted to centimes via ``to_centimes``). The idempotency key is stable
    per registration, so a double-submit (e.g. a user double-clicking "Pay")
    replays the same session rather than creating a duplicate.

    ``payment_method_types`` is deliberately NOT passed (SKI-165). Naming it
    overrides the account's dashboard configuration: Stripe then offers
    exactly the listed methods and errors if any one of them is not activated,
    so a single unapproved method takes down every payment — card included.
    Omitting it lets Stripe offer whatever is activated for the currency and
    amount, which keeps the card + TWINT intent of ADR 0014 while letting
    TWINT appear the moment its approval lands, with no deploy.

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
    return stripe.Webhook.construct_event(  # type: ignore[no-any-return]  # construct_event is untyped, so its return is Any
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )


def finalize_paid_registration(
    registration: Registration,
    *,
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
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        locked = confirm_registration(locked)
    logger.info(
        "finalize_paid_registration: registration pk=%s finalized (status=%s)",
        locked.pk,
        locked.status,
    )
    return locked


def handle_charge_refunded(event: stripe.Event) -> None:
    """Reconcile a refund issued outside the app back onto its Payment or Tip.

    Refunding from the Stripe dashboard is the only way staff can serve an
    ad-hoc request today, and until SKI-164 nothing listened for it: the row
    kept saying the money was held or paid while Stripe disagreed, with no
    alert, because the webhook returned 200 for every event it did not handle.

    Matches on ``charge.payment_intent`` against ``stripe_payment_intent_id``,
    which both models store and both populate. A charge maps to at most one of
    them — the deposit and tip flows never share a payment intent — so the
    Payment lookup is tried first and the Tip only if it misses.

    **Partial refunds are deliberately not reconciled.** Neither model can
    represent one (``Payment.refund`` is documented as all-or-nothing, and
    ``Tip`` has a single REFUNDED status), so marking a row REFUNDED off the
    back of a partial would overstate what happened. They are logged and left
    alone for a human to handle; representing them properly is a modelling
    decision, not something to infer here.

    Never raises: the webhook must return 200 or Stripe retries forever.
    """
    charge = event["data"]["object"]
    payment_intent_id = charge["payment_intent"] if "payment_intent" in charge else None
    if not payment_intent_id:
        logger.warning("handle_charge_refunded: event carries no payment_intent id")
        return

    fully_refunded = bool(charge["refunded"] if "refunded" in charge else False)
    if not fully_refunded:
        logger.warning(
            "handle_charge_refunded: partial refund on payment_intent=%s left "
            "unreconciled — neither Payment nor Tip can represent one",
            payment_intent_id,
        )
        return

    refund_id = _latest_refund_id(payment_intent_id)

    payment = Payment.objects.filter(stripe_payment_intent_id=payment_intent_id).first()
    if payment is not None:
        record_payment_refunded(payment, stripe_refund_id=refund_id)
        return

    tip = Tip.objects.filter(stripe_payment_intent_id=payment_intent_id).first()
    if tip is not None:
        record_tip_refunded(tip, stripe_refund_id=refund_id)
        return

    logger.warning(
        "handle_charge_refunded: no Payment or Tip for payment_intent=%s",
        payment_intent_id,
    )


def _latest_refund_id(payment_intent_id: str) -> str:
    """Return the newest refund id for ``payment_intent_id``, or "".

    Looked up rather than read off the event: Stripe does not include
    ``charge.refunds`` in the webhook payload, and does not return it from a
    plain ``Charge.retrieve`` either — it needs an explicit ``expand``. Reading
    it off the charge therefore always yielded nothing, which a live test-mode
    refund confirmed (SKI-166): the status reconciled but the id was stored
    empty, leaving the audit trail without the one identifier it exists to
    hold.

    The id is for the audit trail only, so any failure degrades to an empty
    string rather than aborting — the status transition matters more, and a
    webhook handler must not raise.
    """
    _configure_stripe()
    try:
        refunds = stripe.Refund.list(payment_intent=payment_intent_id, limit=1)
        return str(refunds.data[0].id) if refunds.data else ""
    except stripe.error.StripeError, KeyError, IndexError, TypeError:
        logger.exception(
            "_latest_refund_id: could not resolve a refund id for "
            "payment_intent=%s; recording the transition without one",
            payment_intent_id,
        )
        return ""


def handle_checkout_completed(event: stripe.Event) -> None:
    """Dispatch a verified checkout.session.completed event to the finaliser.

    The source-of-truth handler behind public.views.stripe_webhook, split out so
    the view only verifies the signature and returns 200. Resolves the
    registration from metadata.registration_pk, then routes on metadata.purpose:
    "tip" → record_tip_paid, anything else (deposit) → finalize_paid_registration.
    Unknown/absent registration or missing payment_intent is logged and ignored
    (the webhook must still return 200 so Stripe stops retrying).
    """
    session = event["data"]["object"]
    registration_pk = _stripe_metadata_get(session, "registration_pk")
    payment_intent_id = _session_payment_intent_id(session)
    if registration_pk and payment_intent_id:
        try:
            registration = Registration.objects.get(pk=registration_pk)
        except Registration.DoesNotExist, ValueError:
            logger.error(
                "stripe_webhook: checkout.session.completed for unknown "
                "registration pk=%r",
                registration_pk,
            )
            return
        if _stripe_metadata_get(session, "purpose") == "tip":
            amount_chf = _parse_tip_amount_chf(
                _stripe_metadata_get(session, "amount_chf")
            )
            if amount_chf is None:
                logger.error(
                    "stripe_webhook: checkout.session.completed tip session "
                    "has unusable amount_chf metadata (session id=%s)",
                    getattr(session, "id", "?"),
                )
                return
            message = _stripe_metadata_get(session, "message") or ""
            record_tip_paid(
                registration=registration,
                amount_chf=amount_chf,
                message=message,
                stripe_payment_intent_id=payment_intent_id,
            )
        else:
            finalize_paid_registration(
                registration,
                stripe_payment_intent_id=payment_intent_id,
            )
    else:
        logger.warning(
            "stripe_webhook: checkout.session.completed missing usable "
            "metadata/payment_intent (session id=%s)",
            getattr(session, "id", "?"),
        )
