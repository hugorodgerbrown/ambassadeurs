# Willingness-to-pay survey (VERB-111).
#
# registration.register_done shows a short, skippable, single-question survey
# to VERIFIED, free-tier (fee_chf == 0) registrants who have not already
# responded, asking directly for the highest refundable deposit they would
# have been happy to pay to register. register_survey_submit (@require_htmx)
# validates and creates the SurveyResponse row, returning the thanks fragment
# in place.

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.decorators import require_htmx
from public.forms import SurveyResponseForm
from public.models import SurveyResponse
from public.services import record_survey_response

from ._shared import _authenticated_registration

logger = logging.getLogger(__name__)


@require_htmx
@require_POST
def register_survey_submit(request: HttpRequest) -> HttpResponse:
    """HTMX POST: record a willingness-to-pay survey response, in place.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST``.
    Resolves the caller's own registration — 400 if there is none. Requires
    the registration to be free-tier (``fee_chf == 0``); a paid-tier or
    already-responded caller gets the thanks fragment back idempotently
    without creating a second row (the ``IntegrityError`` from the OneToOne
    constraint is a race backstop for the "already responded" check).

    Skip (``"skip" in request.POST``, from the survey form's second submit
    button) is checked before any validation and returns an empty 200 body,
    creating no row — the ``hx-swap="innerHTML"`` then clears ``#wtp-survey``.
    Because nothing is persisted, the survey re-appears on a later GET of
    ``register_done`` (skip is a genuine no-op, not a dismissal that is
    remembered). This avoids ``hx-on:click`` — HTMX's ``hx-on:*`` executes via
    ``new Function()``, which the production CSP's ``script-src`` (no
    ``unsafe-eval``) blocks.

    An invalid submission (missing ``max_deposit``) re-renders the survey
    fragment with form errors and creates no row.
    """
    registration = _authenticated_registration(request)
    if registration is None or registration.fee_chf != 0:
        return HttpResponse(status=400)

    if "skip" in request.POST:
        return HttpResponse(status=200)

    already_responded = SurveyResponse.objects.filter(
        registration=registration
    ).exists()
    if already_responded:
        return render(request, "public/partials/wtp_survey_thanks.html")

    form = SurveyResponseForm(data=request.POST)
    if not form.is_valid():
        return render(
            request,
            "public/partials/wtp_survey.html",
            {"survey_form": form},
        )

    record_survey_response(registration, max_deposit=form.cleaned_data["max_deposit"])

    return render(request, "public/partials/wtp_survey_thanks.html")
