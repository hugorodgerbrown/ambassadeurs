"""Admin registration for the billing app."""

from django.contrib import admin

from .models import Payment, Tip


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Admin for Payment.

    Stripe identifiers, amount, and status/reason are readonly — the deposit
    lifecycle is driven exclusively through billing.services.payments, never
    by editing rows directly in the admin.
    """

    list_display = [
        "pk",
        "registration",
        "amount_chf",
        "status",
        "reason",
        "created_at",
    ]
    list_filter = ["status", "reason"]
    search_fields = [
        "registration__user__email",
        "stripe_customer_id",
        "stripe_payment_intent_id",
        "stripe_refund_id",
    ]
    raw_id_fields = ["registration"]

    def has_add_permission(self, request: object) -> bool:
        """Disallow creating Payment rows by hand.

        A Payment must correspond to a real Stripe charge created through the
        registration flow (VERB-86); an admin-created row would have no Stripe
        payment intent and confuse the refund/reconciliation paths.
        """
        return False

    readonly_fields = [
        "amount_chf",
        "status",
        "reason",
        "stripe_customer_id",
        "stripe_payment_intent_id",
        "stripe_refund_id",
        "created_at",
        "updated_at",
    ]


@admin.register(Tip)
class TipAdmin(admin.ModelAdmin):
    """Admin for Tip.

    Stripe identifiers, amount, message and status are readonly — a Tip is
    only ever created via billing.services.tips.record_tip_paid, never by
    editing rows directly in the admin. Any REFUNDED status change is
    performed staff-side via the Stripe dashboard (VERB-110).
    """

    list_display = [
        "pk",
        "registration",
        "amount_chf",
        "status",
        "message",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = [
        "registration__user__email",
        "stripe_customer_id",
        "stripe_payment_intent_id",
        "stripe_refund_id",
        "message",
    ]
    raw_id_fields = ["registration"]

    def has_add_permission(self, request: object) -> bool:
        """Disallow creating Tip rows by hand.

        A Tip must correspond to a real Stripe charge created through the
        tip flow; an admin-created row would have no Stripe payment intent.
        """
        return False

    readonly_fields = [
        "amount_chf",
        "message",
        "status",
        "stripe_customer_id",
        "stripe_payment_intent_id",
        "stripe_refund_id",
        "created_at",
        "updated_at",
    ]
