# Tip (voluntary contribution) flow — standalone, unmounted (VERB-110).
#
# A standalone, login-required page (tip_page) that exercises
# billing.services.tips in isolation — it is not mounted in any journey yet
# (a follow-up ticket wires it into the confirmed-match page). Audience is
# gated server-side to free-tier registrants only (registration.fee_chf == 0);
# a tip never touches matching state. payments.stripe_webhook dispatches on
# the "purpose" session metadata key set by create_tip_checkout_session,
# falling through to the existing deposit path (which carries no "purpose"
# key) unchanged.

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from billing.forms import TipForm
from billing.services.tips import (
    _parse_tip_amount_chf,
    create_tip_checkout_session,
    record_tip_paid,
)
from matching.models import Registration

from ._shared import (
    _authenticated_registration,
    _checkout_return_urls,
    _redirect_to_checkout,
    _stripe_metadata_get,
    _verify_return_session,
)

logger = logging.getLogger(__name__)


def _free_tier_registration_or_404(request: HttpRequest) -> Registration:
    """Return the caller's free-tier registration, or raise Http404.

    The tip flow is gated to free-tier registrants only
    (``registration.fee_chf == 0``) — enforced identically in ``tip_page``
    and ``tip_start`` so neither view can be reached directly by a paid-tier
    registrant.
    """
    registration = _authenticated_registration(request)
    if registration is None or registration.fee_chf > 0:
        raise Http404("No free-tier registration for this account.")
    return registration


@login_required
def tip_page(request: HttpRequest) -> HttpResponse:
    """Render the standalone tip (voluntary contribution) page.

    Login-required; free-tier registrants only (Http404 otherwise). Not
    linked from any nav or journey page — this ticket (VERB-110) builds the
    component in isolation; a follow-up ticket mounts it on the
    confirmed-match page.
    """
    _free_tier_registration_or_404(request)
    show_refund_disclaimer = request.GET.get("disclaimer", "1") != "0"
    return render(
        request,
        "public/tip.html",
        {
            "form": TipForm(),
            "skip_url": reverse("accounts:detail"),
            "show_refund_disclaimer": show_refund_disclaimer,
        },
    )


@login_required
@require_POST
def tip_start(request: HttpRequest) -> HttpResponse:
    """Validate the tip form and redirect to a fresh Stripe Checkout session.

    Same free-tier gate as ``tip_page``. On an invalid form, re-renders
    ``tip_page`` with errors rather than redirecting.
    """
    registration = _free_tier_registration_or_404(request)
    form = TipForm(request.POST)
    if not form.is_valid():
        show_refund_disclaimer = request.GET.get("disclaimer", "1") != "0"
        return render(
            request,
            "public/tip.html",
            {
                "form": form,
                "skip_url": reverse("accounts:detail"),
                "show_refund_disclaimer": show_refund_disclaimer,
            },
        )

    success_url, cancel_url = _checkout_return_urls(
        request,
        return_route="public:tip_return",
        cancel_route="public:tip_cancelled",
    )

    session = create_tip_checkout_session(
        registration,
        amount_chf=form.cleaned_data["amount_chf"],
        message=form.cleaned_data["message"],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return _redirect_to_checkout(
        request, session, registration, cancel_template="public/tip_cancelled.html"
    )


@login_required
def tip_return(request: HttpRequest) -> HttpResponse:
    """Stripe's ``success_url`` target for the tip flow: verify and record.

    Mirrors ``register_payment_return``: retrieves the Checkout session
    named by ``?session_id=``, checks the session belongs to the caller's
    own registration and is paid, then calls ``record_tip_paid`` and renders
    the thank-you page.
    """
    result = _verify_return_session(
        request, purpose="tip", on_incomplete="public/tip_cancelled.html"
    )
    if isinstance(result, HttpResponse):
        return result
    registration, session, customer_id, payment_intent_id = result

    amount_chf = _parse_tip_amount_chf(_stripe_metadata_get(session, "amount_chf"))
    if amount_chf is None:
        logger.error(
            "tip_return: session id=%s has unusable amount_chf metadata",
            session.id,
        )
        return render(request, "public/tip_cancelled.html")

    message = _stripe_metadata_get(session, "message") or ""
    record_tip_paid(
        registration=registration,
        amount_chf=amount_chf,
        message=message,
        stripe_customer_id=customer_id,
        stripe_payment_intent_id=payment_intent_id,
    )
    return render(request, "public/tip_thanks.html")


def tip_cancelled(request: HttpRequest) -> HttpResponse:
    """Stripe's ``cancel_url`` target for the tip flow: a friendly no-thanks page."""
    return render(request, "public/tip_cancelled.html")
