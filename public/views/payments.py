# Paid-tier deposit flow — Stripe hosted Checkout (VERB-86, ADR 0014).
#
# register_payment_start creates a Stripe hosted Checkout session and
# redirects the browser to it. register_payment_return is Stripe's
# success_url target (fast UX); stripe_webhook (mounted un-prefixed in
# config/urls.py) is the source of truth. Both funnel through
# billing.services.checkout.finalize_paid_registration, which is idempotent,
# so whichever of the two fires first "wins" and the other is a safe no-op.
# An UNVERIFIED paid-tier registration is never matched — pool entry is
# gated on both email confirmation AND payment (Invariant 2's spirit).
#
# stripe_webhook also dispatches on the "purpose" session metadata key set by
# tips.tip_start's create_tip_checkout_session call, falling through to the
# deposit path (which carries no "purpose" key) unchanged (VERB-110).

from __future__ import annotations

import logging

import stripe
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from billing.services.checkout import (
    create_checkout_session,
    finalize_paid_registration,
    retrieve_checkout_session,
    verify_webhook,
)
from billing.services.tips import record_tip_paid
from matching.models import Registration

from ._shared import _authenticated_registration, _stripe_metadata_get
from .registration import SLUG_BY_ROLE
from .tips import _parse_tip_amount_chf

logger = logging.getLogger(__name__)


@login_required
def register_payment_start(request: HttpRequest) -> HttpResponse:
    """Create a Stripe Checkout session for the caller's deposit and redirect.

    Requires the caller's own registration to be UNVERIFIED with
    ``fee_chf > 0`` — anything else (already paid, free tier, no
    registration) is a 404. Also reused as the retry entry point from the
    account-page CTA and the cancel page.
    """
    registration = _authenticated_registration(request)
    if (
        registration is None
        or registration.status != Registration.Status.UNVERIFIED
        or registration.fee_chf <= 0
    ):
        raise Http404("No pending paid registration for this account.")

    return_url = request.build_absolute_uri(reverse("public:register_payment_return"))
    # Stripe substitutes this literal placeholder with the real session id —
    # it must not be URL-encoded, so it is not built via urlencode.
    success_url = f"{return_url}?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = request.build_absolute_uri(
        reverse("public:register_payment_cancelled")
    )

    session = create_checkout_session(
        registration,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    if not session.url:
        # Defensive: Stripe only omits `.url` for a non-hosted-page session,
        # which this flow never creates. Log and treat as a cancelled attempt
        # rather than crashing the redirect.
        logger.error(
            "register_payment_start: Checkout session id=%s for registration "
            "pk=%s has no url",
            session.id,
            registration.pk,
        )
        return render(request, "public/register_payment_cancelled.html")
    return redirect(session.url)


@login_required
def register_payment_return(request: HttpRequest) -> HttpResponse:
    """Stripe's ``success_url`` target: verify payment and finalise, or wait.

    Retrieves the Checkout session named by ``?session_id=`` and, if Stripe
    reports it as paid, calls ``finalize_paid_registration`` (idempotent —
    safe even if the webhook already finalised it) and redirects to
    ``register_done``. Otherwise renders a "payment pending" page — the
    webhook is the source of truth and may complete the registration shortly
    after this request.
    """
    registration = _authenticated_registration(request)
    if registration is None:
        raise Http404("No registration for this account.")

    session_id = request.GET.get("session_id", "")
    if not session_id:
        return render(request, "public/register_payment_pending.html")

    session = retrieve_checkout_session(session_id)

    # Defence in depth: confirm this session was created for this caller's
    # own registration before ever finalising anything from it.
    metadata_pk = _stripe_metadata_get(session, "registration_pk")
    if metadata_pk != str(registration.pk):
        logger.warning(
            "register_payment_return: session id=%s metadata registration_pk=%r "
            "does not match caller's registration pk=%s",
            session_id,
            metadata_pk,
            registration.pk,
        )
        return render(request, "public/register_payment_pending.html")

    if session.payment_status != "paid":
        return render(request, "public/register_payment_pending.html")

    customer_id = session.customer if isinstance(session.customer, str) else ""
    payment_intent_id = (
        session.payment_intent if isinstance(session.payment_intent, str) else ""
    )
    if not payment_intent_id:
        logger.error(
            "register_payment_return: session id=%s is paid but has no "
            "payment_intent id",
            session_id,
        )
        return render(request, "public/register_payment_pending.html")

    registration = finalize_paid_registration(
        registration,
        stripe_customer_id=customer_id,
        stripe_payment_intent_id=payment_intent_id,
    )

    role_slug = SLUG_BY_ROLE.get(Registration.Role(registration.role), "ambassador")
    return redirect("public:register_done", role=role_slug)


def register_payment_cancelled(request: HttpRequest) -> HttpResponse:
    """Stripe's ``cancel_url`` target: friendly page with a retry link."""
    return render(request, "public/register_payment_cancelled.html")


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    """Stripe webhook endpoint — the source of truth for a completed deposit.

    Mounted un-prefixed in ``config/urls.py`` (outside the app's own URLconf)
    so Stripe always hits a stable path. No authentication other than the
    signature check: ``@csrf_exempt`` because Stripe cannot supply a Django
    CSRF token, ``@require_POST`` because Stripe only ever POSTs here.

    On ``checkout.session.completed`` with a resolvable
    ``metadata.registration_pk``, calls ``finalize_paid_registration``
    (idempotent — safe even if ``register_payment_return`` already finalised
    it). Any other event type is accepted and ignored. Returns 400 on a bad
    signature so Stripe's retry logic kicks in only for genuine delivery
    failures, never for a forged payload.

    Dispatches on the ``metadata.purpose`` key (VERB-110): ``"tip"`` sessions
    (set by ``create_tip_checkout_session``) call ``record_tip_paid``; any
    other session — including every existing deposit session, which carries
    no ``purpose`` key — falls through to the deposit path unchanged.
    """
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        event = verify_webhook(request.body, sig_header)
    except ValueError, stripe.error.SignatureVerificationError:
        logger.warning("stripe_webhook: signature verification failed")
        return HttpResponse(status=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        registration_pk = _stripe_metadata_get(session, "registration_pk")
        # Stripe does not create a Customer for payment-mode sessions by
        # default (and never for TWINT), so session.customer is often absent.
        # customer_id is optional (Payment.stripe_customer_id is blank); only
        # the payment_intent is required to finalise — mirrors the return view.
        customer_id = session.customer if isinstance(session.customer, str) else ""
        payment_intent_id = (
            session.payment_intent if isinstance(session.payment_intent, str) else None
        )
        if registration_pk and payment_intent_id:
            try:
                registration = Registration.objects.get(pk=registration_pk)
            except Registration.DoesNotExist, ValueError:
                logger.error(
                    "stripe_webhook: checkout.session.completed for unknown "
                    "registration pk=%r",
                    registration_pk,
                )
                return HttpResponse(status=200)
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
                    return HttpResponse(status=200)
                message = _stripe_metadata_get(session, "message") or ""
                record_tip_paid(
                    registration=registration,
                    amount_chf=amount_chf,
                    message=message,
                    stripe_customer_id=customer_id,
                    stripe_payment_intent_id=payment_intent_id,
                )
            else:
                finalize_paid_registration(
                    registration,
                    stripe_customer_id=customer_id,
                    stripe_payment_intent_id=payment_intent_id,
                )
        else:
            logger.warning(
                "stripe_webhook: checkout.session.completed missing usable "
                "metadata/payment_intent (session id=%s)",
                getattr(session, "id", "?"),
            )

    return HttpResponse(status=200)
