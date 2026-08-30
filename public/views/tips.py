# Tip (voluntary contribution) flow (VERB-110, mounted by VERB-112).
#
# The panel's real home is the confirmed-match page (VERB-112) — see
# public.views.match._match_context; tip_page here remains a standalone,
# login-required page that exercises billing.services.tips in isolation.
# Audience is gated server-side to free-tier registrants only
# (registration.is_free_tier); a tip never touches matching state.
# payments.stripe_webhook dispatches on the "purpose" session metadata key set
# by create_tip_checkout_session, falling through to the existing deposit path
# (which carries no "purpose" key) unchanged.
#
# A mount tells the flow where it came from with a "return_to" origin key
# (_RETURN_TO_ROUTES). It is a fixed allow-list, not a free-text "next"
# parameter, so a value arriving from a form field can never become an open
# redirect. A recognised origin means a match already exists, which changes
# two things: an abandoned Checkout returns to that page instead of the
# generic cancelled page, and the panel shows the no-refund note in place of
# the registration-time refund disclaimer.

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


# Origin keys a mount may post as ``return_to``, mapped to the route the flow
# returns to. Fixed allow-list — see the module header.
_RETURN_TO_ROUTES = {"match": "accounts:match"}


def _free_tier_registration_or_404(request: HttpRequest) -> Registration:
    """Return the caller's free-tier registration, or raise Http404.

    The tip flow is gated to free-tier registrants only
    (``registration.is_free_tier``) — enforced identically in ``tip_page``
    and ``tip_start`` so neither view can be reached directly by a paid-tier
    registrant.
    """
    registration = _authenticated_registration(request)
    if registration is None or not registration.is_free_tier:
        raise Http404("No free-tier registration for this account.")
    return registration


def _validated_return_to(request: HttpRequest) -> str:
    """Return the POSTed ``return_to`` origin key if recognised, else "".

    Anything not in ``_RETURN_TO_ROUTES`` is discarded rather than rejected —
    an unknown origin simply falls back to the standalone flow's own pages.
    """
    return_to = request.POST.get("return_to", "")
    return return_to if return_to in _RETURN_TO_ROUTES else ""


def _tip_panel_context(
    request: HttpRequest, form: TipForm, *, return_to: str
) -> dict[str, object]:
    """Build the ``public/tip.html`` context for ``form``.

    ``return_to`` is an already-validated origin key: "" for the standalone
    page, otherwise a key in ``_RETURN_TO_ROUTES``. A recognised origin means
    a match already exists, so the panel swaps the registration-time refund
    disclaimer for the no-refund note and sends "No thanks" back to the page
    the reader came from.
    """
    if return_to:
        return {
            "form": form,
            "skip_url": reverse(_RETURN_TO_ROUTES[return_to]),
            "show_refund_disclaimer": False,
            "show_no_refund_note": True,
            "return_to": return_to,
        }
    return {
        "form": form,
        "skip_url": reverse("accounts:detail"),
        "show_refund_disclaimer": request.GET.get("disclaimer", "1") != "0",
        "show_no_refund_note": False,
        "return_to": "",
    }


@login_required
def tip_page(request: HttpRequest) -> HttpResponse:
    """Render the standalone tip (voluntary contribution) page.

    Login-required; free-tier registrants only (Http404 otherwise). Not
    linked from any nav or journey page — the panel's real mount is the
    confirmed-match page (VERB-112); this route keeps the component
    exercisable on its own.
    """
    _free_tier_registration_or_404(request)
    return render(
        request,
        "public/tip.html",
        _tip_panel_context(request, TipForm(), return_to=""),
    )


@login_required
@require_POST
def tip_start(request: HttpRequest) -> HttpResponse:
    """Validate the tip form and redirect to a fresh Stripe Checkout session.

    Same free-tier gate as ``tip_page``. On an invalid form, re-renders
    ``tip_page`` with errors rather than redirecting.
    """
    registration = _free_tier_registration_or_404(request)
    return_to = _validated_return_to(request)
    form = TipForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "public/tip.html",
            _tip_panel_context(request, form, return_to=return_to),
        )

    success_url, cancel_url = _checkout_return_urls(
        request,
        return_route="public:tip_return",
        cancel_route=_RETURN_TO_ROUTES.get(return_to, "public:tip_cancelled"),
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
