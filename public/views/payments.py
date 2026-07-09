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
#
# The checkout.session.completed handling itself (VERB-142) lives in
# billing.services.checkout.handle_checkout_completed — this view only
# verifies the Stripe signature and dispatches.

from __future__ import annotations

import logging

import stripe
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from billing.services.checkout import (
    create_checkout_session,
    finalize_paid_registration,
    handle_checkout_completed,
    verify_webhook,
)
from matching.models import Registration

from ._shared import (
    _authenticated_registration,
    _checkout_return_urls,
    _redirect_to_checkout,
    _verify_return_session,
)
from .registration import SLUG_BY_ROLE

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

    success_url, cancel_url = _checkout_return_urls(
        request,
        return_route="public:register_payment_return",
        cancel_route="public:register_payment_cancelled",
    )

    session = create_checkout_session(
        registration,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return _redirect_to_checkout(
        request,
        session,
        registration,
        cancel_template="public/register_payment_cancelled.html",
    )


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
    result = _verify_return_session(
        request, purpose=None, on_incomplete="public/register_payment_pending.html"
    )
    if isinstance(result, HttpResponse):
        return result
    registration, session, customer_id, payment_intent_id = result

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

    On ``checkout.session.completed``, delegates to
    ``billing.services.checkout.handle_checkout_completed`` (which resolves
    the registration and dispatches on ``metadata.purpose`` — ``"tip"``
    sessions call ``record_tip_paid``; deposit sessions call
    ``finalize_paid_registration``, idempotent — safe even if
    ``register_payment_return`` already finalised it). Any other event type
    is accepted and ignored. Returns 400 on a bad signature so Stripe's
    retry logic kicks in only for genuine delivery failures, never for a
    forged payload.
    """
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        event = verify_webhook(request.body, sig_header)
    except ValueError, stripe.error.SignatureVerificationError:
        logger.warning("stripe_webhook: signature verification failed")
        return HttpResponse(status=400)

    if event["type"] == "checkout.session.completed":
        handle_checkout_completed(event)

    return HttpResponse(status=200)
