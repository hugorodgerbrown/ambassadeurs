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
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from billing.forms import TipForm
from billing.services.checkout import retrieve_checkout_session
from billing.services.tips import create_tip_checkout_session, record_tip_paid
from matching.models import Registration

from ._shared import _authenticated_registration, _stripe_metadata_get

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


def _parse_tip_amount_chf(raw: str | None) -> int | None:
    """Parse the ``amount_chf`` session metadata value, or None if unusable.

    Metadata is attacker-influenced-adjacent (round-tripped through Stripe,
    but ultimately sourced from whatever ``create_tip_checkout_session`` was
    called with) — never trust it to be a clean integer string. Returns None
    rather than raising so both callers (``tip_return``, ``stripe_webhook``)
    can degrade gracefully instead of a user-facing 500 / an unhandled
    exception in the always-200 webhook.
    """
    if raw is None:
        return None
    try:
        amount_chf = int(raw)
    except ValueError:
        return None
    # A non-positive amount would fail Tip.amount_chf's Postgres CHECK
    # constraint inside record_tip_paid, where the IntegrityError would be
    # misread as an idempotency race — reject it here instead.
    if amount_chf < 1:
        return None
    return amount_chf


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

    return_url = request.build_absolute_uri(reverse("public:tip_return"))
    # Stripe substitutes this literal placeholder with the real session id —
    # it must not be URL-encoded, so it is not built via urlencode.
    success_url = f"{return_url}?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = request.build_absolute_uri(reverse("public:tip_cancelled"))

    session = create_tip_checkout_session(
        registration,
        amount_chf=form.cleaned_data["amount_chf"],
        message=form.cleaned_data["message"],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    if not session.url:
        # Defensive: Stripe only omits `.url` for a non-hosted-page session,
        # which this flow never creates. Log and treat as a cancelled attempt
        # rather than crashing the redirect.
        logger.error(
            "tip_start: Checkout session id=%s for registration pk=%s has no url",
            session.id,
            registration.pk,
        )
        return render(request, "public/tip_cancelled.html")
    return redirect(session.url)


@login_required
def tip_return(request: HttpRequest) -> HttpResponse:
    """Stripe's ``success_url`` target for the tip flow: verify and record.

    Mirrors ``register_payment_return``: retrieves the Checkout session
    named by ``?session_id=``, checks the session belongs to the caller's
    own registration and is paid, then calls ``record_tip_paid`` and renders
    the thank-you page.
    """
    registration = _authenticated_registration(request)
    if registration is None:
        raise Http404("No registration for this account.")

    session_id = request.GET.get("session_id", "")
    if not session_id:
        return render(request, "public/tip_cancelled.html")

    session = retrieve_checkout_session(session_id)

    # Defence in depth: confirm this session was created for this caller's
    # own registration, and is actually a tip session, before recording it.
    metadata_pk = _stripe_metadata_get(session, "registration_pk")
    if _stripe_metadata_get(session, "purpose") != "tip" or metadata_pk != str(
        registration.pk
    ):
        logger.warning(
            "tip_return: session id=%s metadata purpose/registration_pk "
            "does not match caller's registration pk=%s",
            session_id,
            registration.pk,
        )
        return render(request, "public/tip_cancelled.html")

    if session.payment_status != "paid":
        return render(request, "public/tip_cancelled.html")

    customer_id = session.customer if isinstance(session.customer, str) else ""
    payment_intent_id = (
        session.payment_intent if isinstance(session.payment_intent, str) else ""
    )
    if not payment_intent_id:
        logger.error(
            "tip_return: session id=%s is paid but has no payment_intent id",
            session_id,
        )
        return render(request, "public/tip_cancelled.html")

    amount_chf = _parse_tip_amount_chf(_stripe_metadata_get(session, "amount_chf"))
    if amount_chf is None:
        logger.error(
            "tip_return: session id=%s has unusable amount_chf metadata",
            session_id,
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
