# Payment lifecycle services (VERB-85, ADR 0014).
#
# The deposit is charged synchronously at registration time (SCA/TWINT
# constraints — ADR 0014), so HELD already means "funds collected, outcome
# pending". capture() and forfeit() are therefore pure state transitions with
# no Stripe call; refund() is the only transition that hits the Stripe API.
#
# Every transition runs inside transaction.atomic() and calls
# core.services.record_transition inline for the audit log — never via Django
# signals (CLAUDE.md "No Django signals for side effects").
#
# _idempotency_key gives every Stripe-calling transition a stable key derived
# from (payment pk, action), so a double-submit or a later sweep (VERB-88)
# can never double-refund the same payment.
#
# to_centimes is the single CHF-to-minor-unit conversion boundary — the rest
# of the domain reasons in whole CHF (Registration.fee_chf, Payment.amount_chf)
# and only converts here, at the point a Stripe API call is made.

from __future__ import annotations

import logging

import stripe
from django.conf import settings
from django.db import transaction

from core.services import record_transition

from ..models import Payment

logger = logging.getLogger(__name__)


class InvalidPaymentTransition(Exception):
    """Raised when a Payment transition is attempted from a non-HELD state.

    Payments are immutable once terminal (CAPTURED, REFUNDED, or FORFEITED) —
    every transition function must guard on ``status == HELD`` and raise this
    instead of silently double-applying an outcome.
    """


def _configure_stripe() -> None:
    """Set the Stripe SDK API key from settings, read lazily at call time.

    Reading ``settings.STRIPE_SECRET_KEY`` inside the function (rather than at
    module import time) means ``@override_settings`` works in tests.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _idempotency_key(payment: Payment, action: str) -> str:
    """Return a stable Stripe idempotency key for ``action`` on ``payment``.

    Stable per (payment pk, action) — a double-submit or a later sweep can
    never double-fire the same Stripe call for the same payment (shared with
    VERB-88's close_season refund sweep).
    """
    return f"{action}-payment-{payment.pk}"


def to_centimes(amount_chf: int) -> int:
    """Convert a whole-CHF amount to Stripe's minor unit (centimes).

    The single CHF→minor-unit conversion boundary (ADR 0014) — the domain
    never stores or reasons about centimes anywhere else.

    Args:
        amount_chf: A non-negative whole-CHF amount.

    Returns:
        The equivalent amount in centimes.
    """
    return amount_chf * 100


def capture(
    payment: Payment,
    *,
    reason: Payment.Reason = Payment.Reason.SUCCESSFUL_MATCH,
) -> Payment:
    """Transition ``payment`` HELD → CAPTURED.

    The deposit was already collected at registration time, so this is a pure
    state transition — no Stripe call. Used when the match reaches
    ``Match.Status.ACCEPTED`` (mutual accept).

    Args:
        payment: The payment to capture. Must be HELD.
        reason: The Payment.Reason to record; defaults to SUCCESSFUL_MATCH.

    Returns:
        The updated Payment instance.

    Raises:
        InvalidPaymentTransition: if ``payment.status != HELD``.
    """
    with transaction.atomic():
        locked: Payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.HELD:
            raise InvalidPaymentTransition(
                f"Cannot capture payment pk={payment.pk}: status is "
                f"{locked.status!r}, expected HELD."
            )
        status_before = locked.status
        locked.status = Payment.Status.CAPTURED
        locked.reason = reason
        locked.save(update_fields=["status", "reason", "updated_at"])
        record_transition(
            locked,
            "status",
            before=status_before,
            after=locked.status,
        )
    logger.info("Payment pk=%s captured (reason=%s)", payment.pk, reason)
    return locked


def forfeit(
    payment: Payment,
    *,
    reason: Payment.Reason = Payment.Reason.POST_ACCEPT_NOSHOW,
) -> Payment:
    """Transition ``payment`` HELD → FORFEITED.

    A pure state transition — no Stripe call. Used when the registration is
    suspended for a post-accept no-show.

    Args:
        payment: The payment to forfeit. Must be HELD.
        reason: The Payment.Reason to record; defaults to POST_ACCEPT_NOSHOW.

    Returns:
        The updated Payment instance.

    Raises:
        InvalidPaymentTransition: if ``payment.status != HELD``.
    """
    with transaction.atomic():
        locked: Payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.HELD:
            raise InvalidPaymentTransition(
                f"Cannot forfeit payment pk={payment.pk}: status is "
                f"{locked.status!r}, expected HELD."
            )
        status_before = locked.status
        locked.status = Payment.Status.FORFEITED
        locked.reason = reason
        locked.save(update_fields=["status", "reason", "updated_at"])
        record_transition(
            locked,
            "status",
            before=status_before,
            after=locked.status,
        )
    logger.info("Payment pk=%s forfeited (reason=%s)", payment.pk, reason)
    return locked


def refund(payment: Payment, *, reason: Payment.Reason) -> Payment:
    """Transition ``payment`` HELD → REFUNDED via a Stripe Refund.

    The only transition that calls Stripe. Used when the season ends without
    a match, or on a good-faith cancel before matching (ADR 0014).

    Args:
        payment: The payment to refund. Must be HELD.
        reason: The Payment.Reason to record (e.g. SEASON_END_NO_MATCH or
            USER_CANCELLED).

    Returns:
        The updated Payment instance, with ``stripe_refund_id`` set.

    Raises:
        InvalidPaymentTransition: if ``payment.status != HELD``.
    """
    with transaction.atomic():
        locked: Payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != Payment.Status.HELD:
            raise InvalidPaymentTransition(
                f"Cannot refund payment pk={payment.pk}: status is "
                f"{locked.status!r}, expected HELD."
            )

        _configure_stripe()
        # No amount= — this refunds the full payment intent (the whole deposit
        # is always returned; there is no partial-refund path).
        #
        # NOTE (VERB-87): this Stripe HTTP call runs inside the atomic block
        # while holding the select_for_update row lock, which is fine for a
        # single user-triggered refund. The close_season batch sweep must NOT
        # call refund() in a tight loop that holds a DB connection across each
        # Stripe round-trip (connection-pool exhaustion) — it should refund
        # outside a long-held transaction / throttle. See VERB-87 scope.
        stripe_refund = stripe.Refund.create(
            payment_intent=locked.stripe_payment_intent_id,
            idempotency_key=_idempotency_key(locked, "refund"),
        )

        status_before = locked.status
        locked.status = Payment.Status.REFUNDED
        locked.reason = reason
        locked.stripe_refund_id = stripe_refund.id
        locked.save(
            update_fields=["status", "reason", "stripe_refund_id", "updated_at"]
        )
        record_transition(
            locked,
            "status",
            before=status_before,
            after=locked.status,
        )
    logger.info(
        "Payment pk=%s refunded (reason=%s, stripe_refund_id=%s)",
        payment.pk,
        reason,
        locked.stripe_refund_id,
    )
    return locked
