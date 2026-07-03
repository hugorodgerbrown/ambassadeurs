# The Payment model: an audit row for the prepaid registration deposit
# collected via Stripe (ADR 0014, VERB-85).
#
# Because the deposit is charged synchronously at registration time (SCA/TWINT
# constraints make charging a saved method weeks later unreliable — ADR 0014),
# HELD means "funds already collected, outcome pending" rather than
# "authorised but not captured". CAPTURED (successful match) and FORFEITED
# (post-accept no-show) are therefore pure state transitions with no further
# Stripe call; only REFUNDED calls Stripe (see billing/services/payments.py).
#
# The Tip model: an audit row for a voluntary contribution (VERB-110), a
# separate money flow from the prepaid deposit above with no match-outcome
# lifecycle — see billing/services/tips.py.
#
# Fixed choice values are TextChoices with UPPER_CASE values (CLAUDE.md).

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, BaseQuerySet
from matching.models import Registration


class PaymentQuerySet(BaseQuerySet):
    """Queryset for Payment."""

    def held(self) -> PaymentQuerySet:
        """Return payments still in the HELD state (outcome pending)."""
        return self.filter(status=Payment.Status.HELD)

    def for_registration(self, registration: Registration) -> PaymentQuerySet:
        """Return payments belonging to ``registration``."""
        return self.filter(registration=registration)


class Payment(BaseModel):
    """An audit row for one prepaid registration deposit.

    ``registration`` uses ``on_delete=SET_NULL`` (rather than ``CASCADE``) so
    the payment row — and its Stripe identifiers — survive account deletion
    (VERB-88); there is no unique constraint, mirroring ``Match``: terminal
    rows accumulate as history rather than being reused.

    Only Stripe identifiers are stored (``stripe_customer_id``,
    ``stripe_payment_intent_id``, ``stripe_refund_id``) — never raw card data.
    """

    class Status(models.TextChoices):
        """Deposit lifecycle. UPPER_CASE values (CLAUDE.md).

        HELD: funds collected at registration time; outcome pending.
        CAPTURED: the match succeeded (mutual accept) — deposit is kept. No
            Stripe call; the money was already collected into HELD.
        REFUNDED: the season ended without a match, or a good-faith cancel —
            the only transition that calls Stripe (a Refund).
        FORFEITED: a post-accept no-show — deposit is kept, no refund. No
            Stripe call.
        """

        HELD = "HELD", _("Held")
        CAPTURED = "CAPTURED", _("Captured")
        REFUNDED = "REFUNDED", _("Refunded")
        FORFEITED = "FORFEITED", _("Forfeited")

    class Reason(models.TextChoices):
        """Why a payment reached its terminal state. UPPER_CASE values.

        Set only on the terminal transition (capture/refund/forfeit); blank
        while the payment is HELD.
        """

        SUCCESSFUL_MATCH = "SUCCESSFUL_MATCH", _("Successful match")
        USER_CANCELLED = "USER_CANCELLED", _("User cancelled")
        SEASON_END_NO_MATCH = "SEASON_END_NO_MATCH", _("Season end, no match")
        POST_ACCEPT_NOSHOW = "POST_ACCEPT_NOSHOW", _("Post-accept no-show")

    registration = models.ForeignKey(
        Registration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        help_text=(
            "The registration this deposit belongs to. SET_NULL so the audit "
            "row survives account deletion (VERB-88)."
        ),
    )
    amount_chf = models.PositiveIntegerField(
        help_text="Whole-CHF deposit amount (mirrors Registration.fee_chf).",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.HELD,
    )
    reason = models.CharField(
        max_length=32,
        choices=Reason.choices,
        blank=True,
        help_text="Set only on the terminal transition; blank while HELD.",
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    stripe_refund_id = models.CharField(max_length=255, blank=True)

    objects = PaymentQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_terminal(self) -> bool:
        """Return True if this payment has left the HELD state."""
        return self.status != Payment.Status.HELD

    def to_string(self) -> str:
        """Return a human-readable label for the payment."""
        return f"Payment {self.pk}: {self.amount_chf} CHF [{self.get_status_display()}]"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()


class TipQuerySet(BaseQuerySet):
    """Queryset for Tip."""

    def paid(self) -> TipQuerySet:
        """Return tips in the PAID state."""
        return self.filter(status=Tip.Status.PAID)

    def for_registration(self, registration: Registration) -> TipQuerySet:
        """Return tips belonging to ``registration``."""
        return self.filter(registration=registration)


class Tip(BaseModel):
    """An audit row for one voluntary contribution ("tip"), collected via Stripe.

    A tip is a separate money flow from the prepaid registration deposit
    (``Payment``) — it has no match-outcome lifecycle, so unlike ``Payment``
    it is captured as ``PAID`` immediately on completed payment; there is no
    HELD/pending-outcome state. ``registration`` uses ``on_delete=SET_NULL``
    (mirroring ``Payment``) so the audit row survives account deletion.
    """

    class Status(models.TextChoices):
        """Tip lifecycle. UPPER_CASE values (CLAUDE.md).

        PAID: the default — money collected via Stripe Checkout. A Tip row
            is only ever created once payment is confirmed, so there is no
            pending/HELD equivalent.
        REFUNDED: staff-initiated via the Stripe dashboard. No in-app
            transition exists for this ticket (VERB-110).
        """

        PAID = "PAID", _("Paid")
        REFUNDED = "REFUNDED", _("Refunded")

    registration = models.ForeignKey(
        Registration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tips",
        help_text=(
            "The registration this tip belongs to. SET_NULL so the audit "
            "row survives account deletion."
        ),
    )
    amount_chf = models.PositiveIntegerField(
        help_text="Whole-CHF tip amount (domain never sees centimes).",
    )
    message = models.CharField(
        max_length=280,
        blank=True,
        help_text="Optional 'say something nice' message from the tipper. Staff-only.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PAID,
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    stripe_refund_id = models.CharField(max_length=255, blank=True)

    objects = TipQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Enforces record_tip_paid's idempotency at the database level: a
            # concurrent webhook retry racing tip_return could otherwise both
            # pass the check-then-create's SELECT and insert two Tip rows for
            # one payment intent (unlike the deposit flow, a Tip has no outer
            # select_for_update() lock to serialise on). The condition excludes
            # the blank default (stripe_payment_intent_id="") so multiple
            # never-completed rows — which this model never actually creates,
            # since a Tip is only ever inserted with a real payment intent id
            # — could never collide on the empty string either way.
            models.UniqueConstraint(
                fields=["stripe_payment_intent_id"],
                condition=~models.Q(stripe_payment_intent_id=""),
                name="unique_tip_stripe_payment_intent_id",
            ),
        ]

    def to_string(self) -> str:
        """Return a human-readable label for the tip."""
        return f"Tip {self.pk}: {self.amount_chf} CHF [{self.get_status_display()}]"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()
