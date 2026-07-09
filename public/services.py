# Service layer for the public web app.
#
# Orchestrates across the accounts/matching/billing domain services and core
# helpers on behalf of ``public.views`` — anything that is more than a thin
# HTTP-layer concern lives here rather than inline in a view. Some functions
# below take the request directly (rather than only domain objects) because
# the work they do is genuinely request-coupled — building absolute URLs,
# reading PostHog cookies, writing to the session — which is also why this
# module lives in ``public/`` rather than a ``matching/`` domain service.

from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest

from accounts.services import send_already_registered_email, send_confirmation_email
from core.geo import geolocate, get_client_ip
from core.observability import alias_identities
from matching.forms import RegistrationForm
from matching.models import Registration
from matching.services import register_participant
from public.models import SurveyResponse

logger = logging.getLogger(__name__)


def record_survey_response(registration: Registration, *, max_deposit: int) -> None:
    """Idempotently record a willingness-to-pay survey response (VERB-111).

    Creates the SurveyResponse row; a concurrent duplicate submission races on
    the OneToOne constraint and raises IntegrityError, which is swallowed (the
    row already exists) so the caller can render the thanks fragment either way.
    """
    try:
        SurveyResponse.objects.create(
            registration=registration,
            max_deposit=max_deposit,
        )
    except IntegrityError:
        # Race backstop: a concurrent submission already created the row.
        logger.info(
            "register_survey_submit: IntegrityError for registration pk=%s — "
            "already responded",
            registration.pk,
        )


def register_or_resend_participant(
    request: HttpRequest, *, role_value: str, form: RegistrationForm
) -> None:
    """Resolve the signup for a validated registration form: enrol, or resend.

    The request-coupled orchestration behind register_form's POST path. Given a
    VALID form, it either (a) emails an existing non-UNVERIFIED enrollee a
    sign-in link (non-enumerating guard, VERB-72), (b) resends the confirmation
    link for an existing UNVERIFIED registration, or (c) creates a new
    UNVERIFIED registration, aliases the anonymous PostHog identity, and emails
    the confirmation link. Geolocation is resolved here from the request. All
    outcomes leave the caller to redirect to register_email_sent.
    """
    # Resolve geolocation once, before the anon path, so the register_participant
    # call receives the caller's country and region. The raw IP is discarded
    # after the lookup — it is NEVER persisted (data minimisation).
    _client_ip = get_client_ip(request)
    _geo_country, _geo_region = geolocate(_client_ip) if _client_ip else ("", "")

    data = form.cleaned_data
    email: str = data["email"]

    # Non-enumerating enrolment guard (VERB-72): if this email already has a
    # non-UNVERIFIED registration, do not reveal that on the form (that would let
    # an attacker enumerate who is enrolled). Email the owner a sign-in link and
    # fall through to the same generic "check your email" response shown to a
    # brand-new registrant.
    enrolled = (
        Registration.objects.filter(user__email=email)
        .exclude(status=Registration.Status.UNVERIFIED)
        .select_related("user")
        .first()
    )
    if enrolled is not None:
        login_url = send_already_registered_email(request, enrolled.user)
        if settings.DEBUG:
            request.session["debug_verify_url"] = login_url
        return

    # Check for an existing UNVERIFIED registration for this email. If one
    # exists, resend the confirmation link without creating a second row.
    #
    # The lookup and create run inside a single atomic block to guard against a
    # TOCTOU race: if a concurrent request confirms the registration between the
    # DoesNotExist branch and the register_participant call, the OneToOne
    # constraint would raise IntegrityError. We catch that and fall back to
    # resending for whatever row now exists for that email.
    try:
        with transaction.atomic():
            try:
                pending_reg = Registration.objects.select_for_update().get(
                    user__email=email, status=Registration.Status.UNVERIFIED
                )
                confirm_url = send_confirmation_email(request, pending_reg)
            except Registration.DoesNotExist:
                registration = register_participant(
                    role=role_value,
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    email=email,
                    prior_pass=data["prior_pass"],
                    phone=data.get("phone", ""),
                    preferred_location=data.get("preferred_location", ""),
                    preferred_language=data.get("preferred_language", ""),
                    nationality=data.get("nationality", ""),
                    accepted_terms=form.accepted_statements(),
                    status=Registration.Status.UNVERIFIED,
                    registration_country=_geo_country,
                    registration_region=_geo_region,
                )
                # Stitch the anonymous visitor's pre-registration page-views
                # onto the new user in PostHog (VERB-124). Only on this
                # brand-new-registration path — not the already-enrolled or
                # resend paths above/below.
                alias_identities(request, registration.user)
                confirm_url = send_confirmation_email(request, registration)
    except IntegrityError:
        # A concurrent request created/confirmed a registration for this email
        # between our DoesNotExist branch and our create attempt. Resend for
        # whichever row now exists with an UNVERIFIED status; if none exists (it
        # was already confirmed), fall through to a generic resend.
        logger.warning(
            "IntegrityError on registration create for %s — resending for existing row",
            email,
        )
        try:
            existing = Registration.objects.get(
                user__email=email, status=Registration.Status.UNVERIFIED
            )
            confirm_url = send_confirmation_email(request, existing)
        except Registration.DoesNotExist:
            # The race winner already confirmed: nothing to resend.
            return

    if settings.DEBUG:
        request.session["debug_verify_url"] = confirm_url
