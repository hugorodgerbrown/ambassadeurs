# Shared private helpers used across the ``public.views`` package.
#
# Kept intentionally tiny — anything used by more than one sibling module
# lives here rather than being duplicated or imported cross-module, so the
# dependency direction stays one-way (siblings import from ``_shared``,
# never from each other for these two helpers). Its role has grown beyond
# authenticated-registration lookup to cover the Stripe Checkout session
# handling shared by the deposit (``payments.py``) and tip (``tips.py``)
# flows — building redirect URLs, narrowing session fields to plain id
# strings, and verifying a return-view session belongs to the caller.
#
# _stripe_metadata_get and _session_customer_and_intent (VERB-142) are
# Stripe-generic helpers whose canonical home is now
# billing.services.checkout (billing must not import from public) — they are
# re-exported here so the view layer keeps importing them from ``_shared``.

from __future__ import annotations

import logging

import stripe
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from billing.services.checkout import (
    _session_customer_and_intent as _session_customer_and_intent,
)
from billing.services.checkout import (
    _stripe_metadata_get as _stripe_metadata_get,
)
from billing.services.checkout import (
    retrieve_checkout_session,
)
from matching.models import Registration

logger = logging.getLogger(__name__)


def _authenticated_registration(request: HttpRequest) -> Registration | None:
    """Return the Registration for the currently authenticated user, or None.

    Mirrors the ``DoesNotExist`` guard used in ``accounts/views.py``. Returns
    ``None`` for anonymous requests and for authenticated users who have no
    Registration (e.g. staff-only admin users).
    """
    if not request.user.is_authenticated:
        return None
    try:
        return Registration.objects.get(user=request.user)
    except Registration.DoesNotExist:
        return None


def _checkout_return_urls(
    request: HttpRequest, *, return_route: str, cancel_route: str
) -> tuple[str, str]:
    """Build the (success_url, cancel_url) pair for a Stripe Checkout redirect.

    success_url carries Stripe's literal {CHECKOUT_SESSION_ID} placeholder,
    which must NOT be URL-encoded, so it is not built via urlencode.
    """
    return_url = request.build_absolute_uri(reverse(return_route))
    success_url = f"{return_url}?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = request.build_absolute_uri(reverse(cancel_route))
    return success_url, cancel_url


def _redirect_to_checkout(
    request: HttpRequest,
    session: stripe.checkout.Session,
    registration: Registration,
    *,
    cancel_template: str,
) -> HttpResponse:
    """Redirect to the hosted Checkout page, or render `cancel_template` if no url.

    Stripe only omits `.url` for a non-hosted-page session, which these flows
    never create; treat the defensive case as a cancelled attempt rather than
    crashing the redirect.
    """
    if not session.url:
        logger.error(
            "checkout redirect: session id=%s for registration pk=%s has no url",
            session.id,
            registration.pk,
        )
        return render(request, cancel_template)
    return redirect(session.url)


def _verify_return_session(
    request: HttpRequest, *, purpose: str | None, on_incomplete: str
) -> tuple[Registration, stripe.checkout.Session, str, str] | HttpResponse:
    """Verify a Stripe return session for the caller.

    Returns (registration, session, customer_id, payment_intent_id) when the
    session is confirmed paid and belongs to the caller's own registration
    (Invariant: never finalise a session that is not this caller's). Returns a
    rendered `on_incomplete` response for any not-yet-complete or mismatched
    condition, which the caller returns directly. Raises Http404 when the caller
    has no registration. When `purpose` is not None the session's
    metadata.purpose must equal it (the tip flow passes "tip"; the deposit flow
    passes None to skip the check).
    """
    registration = _authenticated_registration(request)
    if registration is None:
        raise Http404("No registration for this account.")

    session_id = request.GET.get("session_id", "")
    if not session_id:
        return render(request, on_incomplete)

    session = retrieve_checkout_session(session_id)

    # Defence in depth: confirm this session was created for this caller's
    # own registration (and, where relevant, is the expected purpose) before
    # ever finalising anything from it.
    metadata_pk = _stripe_metadata_get(session, "registration_pk")
    purpose_mismatch = (
        purpose is not None and _stripe_metadata_get(session, "purpose") != purpose
    )
    if purpose_mismatch or metadata_pk != str(registration.pk):
        logger.warning(
            "_verify_return_session: session id=%s metadata purpose/"
            "registration_pk=%r does not match caller's registration pk=%s",
            session_id,
            metadata_pk,
            registration.pk,
        )
        return render(request, on_incomplete)

    if session.payment_status != "paid":
        return render(request, on_incomplete)

    customer_id, payment_intent_id = _session_customer_and_intent(session)
    if not payment_intent_id:
        logger.error(
            "_verify_return_session: session id=%s is paid but has no "
            "payment_intent id",
            session_id,
        )
        return render(request, on_incomplete)

    return registration, session, customer_id, payment_intent_id
