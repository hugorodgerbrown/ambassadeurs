# Public-facing views: the landing page, the single-step registration flow
# (VERB-24), and the match accept/decline flow (VERB-19).
#
# Registration flow (VERB-24): the homepage role buttons open a combined form
# directly — no login required. The form includes an email field. On submit,
# a Registration is created with status PENDING and a signed confirmation link
# is emailed. Clicking the link transitions PENDING → WAITING, triggers
# matching, logs the user in, and redirects to register_done. Facebook-login
# references have been removed from the UI (VERB-24 P2); the allauth backend
# and URL mount remain untouched in config/.
#
# Match flow (VERB-19): a signed email link carries the participant to
# /match/<token>/ where they can accept or decline. No @login_required — the
# signed token IS the authentication. HTMX partial views for accept/decline are
# guarded with require_htmx (Invariant 7). Contact PII is revealed ONLY when
# match.status == ACCEPTED (Invariant 1).

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.services import mark_email_verified, send_confirmation_email
from accounts.tokens import (
    read_match_access_token,
    read_registration_confirmation_token,
)
from core.decorators import require_htmx
from matching.forms import RegistrationForm
from matching.models import Match, Registration
from matching.services import (
    accept_match,
    confirm_registration,
    decline_match,
    is_registration_open,
    register_participant,
)
from public.models import FormDownload

logger = logging.getLogger(__name__)

# Map the public URL slug to the stored Role value. Defining the valid slugs
# here keeps unknown roles out of the view (404) and out of the templates.
ROLE_BY_SLUG = {
    "ambassador": Registration.Role.AMBASSADOR,
    "referee": Registration.Role.REFEREE,
}

# Reverse map: stored Role value → URL slug, for confirm-redirect construction.
SLUG_BY_ROLE = {v: k for k, v in ROLE_BY_SLUG.items()}

# The legal documents, keyed by URL slug. Validating against this set keeps
# unknown pages out of the view (404) and out of template lookups.
LEGAL_PAGES = {"privacy", "cookies", "terms"}


def home(request: HttpRequest) -> HttpResponse:
    """Render the public landing page with the two role calls-to-action."""
    return render(
        request,
        "public/home.html",
        {"registration_open": is_registration_open()},
    )


def legal_page(request: HttpRequest, page: str) -> HttpResponse:
    """Render a static legal document (privacy / cookies / terms)."""
    if page not in LEGAL_PAGES:
        raise Http404("Unknown legal page.")
    return render(request, f"public/legal/{page}.html")


def how_it_works(request: HttpRequest) -> HttpResponse:
    """Render the 'How it works' informational page (no queries)."""
    return render(request, "public/how_it_works.html")


def download_application_form(request: HttpRequest) -> HttpResponse:
    """Record a form download and redirect to the application-form PDF.

    Creates one FormDownload row per request (the conversion metric) then
    issues a redirect to the externally-hosted PDF (``APPLICATION_FORM_URL``).
    No PII is stored.
    """
    FormDownload.objects.create()
    return redirect(settings.APPLICATION_FORM_URL)


# A no-op service worker served at the origin root so browsers stop 404-ing on
# /sw.js. We intentionally register no fetch/cache handlers (VERB-7).
_SERVICE_WORKER_BODY = "/* 4 Vallées Ambassadors — intentionally minimal. */\n"


def service_worker(request: HttpRequest) -> HttpResponse:
    """Serve a minimal no-op service worker at /sw.js."""
    return HttpResponse(_SERVICE_WORKER_BODY, content_type="application/javascript")


def register(request: HttpRequest) -> HttpResponse:
    """Combined registration form — no login required.

    GET: render the form themed for ``?role=`` (default ambassador).
    POST (anonymous): validate, create a PENDING registration (or resend if one
        already exists for the email), send a confirmation email, redirect to
        ``register_email_sent``.
    POST (authenticated, defensive): complete the registration immediately at
        WAITING status and redirect to ``register_done``.
    """
    if not is_registration_open():
        return render(request, "public/register_closed.html")

    role_slug = request.GET.get("role", "ambassador")
    role_value = ROLE_BY_SLUG.get(role_slug, Registration.Role.AMBASSADOR)

    if request.method == "GET":
        # Derive the display slug from the validated role value so an unknown
        # ?role= param falls back gracefully to ambassador.
        role_slug = SLUG_BY_ROLE[role_value]
        # After is_authenticated, Django stubs narrow request.user to User.
        anon_user: User | None = request.user if request.user.is_authenticated else None
        form = RegistrationForm(role=role_value, user=anon_user)
        return render(
            request,
            "public/register_details.html",
            {"form": form, "role": role_slug, "role_value": role_value},
        )

    # POST path.
    role_slug = request.POST.get("role", "")
    post_role_value = ROLE_BY_SLUG.get(role_slug)
    if post_role_value is None:
        raise Http404("Unknown registration role.")
    role_value = post_role_value

    if request.user.is_authenticated:
        # Defensive authenticated path (not reachable from the standard UI but
        # handled for completeness). Create a WAITING registration immediately.
        # Django stubs narrow request.user to User after is_authenticated.
        auth_user: User = request.user
        form = RegistrationForm(role=role_value, data=request.POST, user=auth_user)
        if form.is_valid():
            data = form.cleaned_data
            register_participant(
                role=role_value,
                user=auth_user,
                first_name=data["first_name"],
                last_name=data["last_name"],
                prior_pass=data["prior_pass"],
                phone=data.get("phone", ""),
                preferred_location=data.get("preferred_location", ""),
                preferred_language=data.get("preferred_language", ""),
                accepted_terms=form.accepted_statements(),
            )
            return redirect("public:register_done", role=role_slug)
        return render(
            request,
            "public/register_details.html",
            {"form": form, "role": role_slug, "role_value": role_value},
        )

    # Anonymous path: validate, create PENDING or resend.
    form = RegistrationForm(role=role_value, data=request.POST)
    if not form.is_valid():
        return render(
            request,
            "public/register_details.html",
            {"form": form, "role": role_slug, "role_value": role_value},
        )

    data = form.cleaned_data
    email: str = data["email"]

    # Check for an existing PENDING registration for this email. If one exists,
    # resend the confirmation link without creating a second row.
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
                    user__email=email, status=Registration.Status.PENDING
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
                    accepted_terms=form.accepted_statements(),
                    status=Registration.Status.PENDING,
                )
                confirm_url = send_confirmation_email(request, registration)
    except IntegrityError:
        # A concurrent request created/confirmed a registration for this email
        # between our DoesNotExist branch and our create attempt. Resend for
        # whichever row now exists with a PENDING status; if none exists (it was
        # already confirmed), fall through to a generic resend.
        logger.warning(
            "IntegrityError on registration create for %s — resending for existing row",
            email,
        )
        try:
            existing = Registration.objects.get(
                user__email=email, status=Registration.Status.PENDING
            )
            confirm_url = send_confirmation_email(request, existing)
        except Registration.DoesNotExist:
            # The race winner already confirmed: redirect without sending so the
            # user proceeds to login normally.
            return redirect("public:register_email_sent")

    if settings.DEBUG:
        request.session["debug_verify_url"] = confirm_url

    return redirect("public:register_email_sent")


def register_email_sent(request: HttpRequest) -> HttpResponse:
    """Confirmation that the registration confirmation email has been sent.

    In development the confirm link is shown on the page (pulled from the
    session) so a tester can click through without opening the inbox.
    """
    debug_verify_url = None
    if settings.DEBUG:
        debug_verify_url = request.session.pop("debug_verify_url", None)
    return render(
        request,
        "public/register_email_sent.html",
        {"debug_verify_url": debug_verify_url},
    )


def register_confirm(request: HttpRequest, token: str) -> HttpResponse:
    """Consume the registration confirmation token.

    Reads the token, loads the Registration, transitions PENDING → WAITING,
    marks the email verified in allauth, logs the user in, and redirects to
    ``register_done`` for the appropriate role.

    Returns 400 on a bad/expired token or a non-PENDING registration (used or
    invalid link).
    """
    pk = read_registration_confirmation_token(token)
    if pk is None:
        return render(request, "public/register_invalid.html", status=400)

    try:
        registration = Registration.objects.select_related("user").get(pk=pk)
    except Registration.DoesNotExist:
        return render(request, "public/register_invalid.html", status=400)

    if registration.status != Registration.Status.PENDING:
        # Already confirmed or in an unexpected state — treat as invalid link.
        return render(request, "public/register_invalid.html", status=400)

    registration = confirm_registration(registration)
    mark_email_verified(registration.user)
    login(
        request,
        registration.user,
        backend="django.contrib.auth.backends.ModelBackend",
    )

    # Derive the slug from the registration role. SLUG_BY_ROLE keys are
    # Role enum values; cast the stored str through the enum for lookup.
    role_slug = SLUG_BY_ROLE.get(Registration.Role(registration.role), "ambassador")
    return redirect("public:register_done", role=role_slug)


def register_done(request: HttpRequest, role: str) -> HttpResponse:
    """Render the post-registration "what happens next" confirmation page."""
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")
    return render(
        request,
        "public/register_done.html",
        {"role": role, "role_value": role_value},
    )


@require_htmx
def register_details_form(request: HttpRequest) -> HttpResponse:
    """Return the themed registration surface for a role (HTMX, role swap).

    Drives the "Your role" dropdown: selecting a role swaps the whole
    ``#reg-surface`` so the eyebrow, lead copy, eligibility callout, form and
    submit button all re-tone to the chosen role.

    No login required: the combined form is anonymous.
    """
    if not is_registration_open():
        raise Http404("Registration is closed.")
    role = request.GET.get("role", "")
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")
    # After is_authenticated, Django stubs narrow request.user to User.
    htmx_user: User | None = request.user if request.user.is_authenticated else None
    form = RegistrationForm(role=role_value, user=htmx_user)
    return render(
        request,
        "public/partials/register_surface.html",
        {"form": form, "role": role, "role_value": role_value, "is_htmx": True},
    )


# ---------------------------------------------------------------------------
# Match accept / decline flow (VERB-19)
# ---------------------------------------------------------------------------

# Display states for the match page — computed from match status + window.
_STATE_ACTIONABLE = "actionable"  # PROPOSED, within window, this side not yet responded
_STATE_WAITING = "waiting"  # PROPOSED but this side already accepted
_STATE_TERMINAL = (
    "terminal"  # ACCEPTED / DECLINED / EXPIRED / ABANDONED, or window lapsed
)


def _resolve_match_token(
    token: str,
) -> tuple[Match, Registration, Match.Side] | None:
    """Validate a match-access token and return the relevant domain objects.

    Returns ``(match, registration, side)`` on success, or ``None`` if the
    token is invalid, expired, or the registration is not a party on the match.
    The match is loaded with its two registrations selected-related.
    """
    parsed = read_match_access_token(token)
    if parsed is None:
        return None
    match_pk, registration_pk = parsed

    try:
        match = Match.objects.select_related(
            "ambassador_registration__user",
            "referee_registration__user",
        ).get(pk=match_pk)
    except Match.DoesNotExist:
        return None

    # Confirm the token's registration_pk is one of the two parties.
    if registration_pk not in (
        match.ambassador_registration_id,
        match.referee_registration_id,
    ):
        return None

    # Identify which registration the token is for.
    if registration_pk == match.ambassador_registration_id:
        registration = match.ambassador_registration
        side = Match.Side.AMBASSADOR
    else:
        registration = match.referee_registration
        side = Match.Side.REFEREE

    return match, registration, side


def _compute_match_display_state(match: Match, side: Match.Side) -> str:
    """Return the display state for a match page, from the viewer's perspective.

    Returns one of the module-level ``_STATE_*`` constants.
    """
    if match.status != Match.Status.PROPOSED or timezone.now() > match.expires_at:
        return _STATE_TERMINAL
    # Match is PROPOSED and within the window. Check if this side has accepted.
    if side == Match.Side.AMBASSADOR and match.ambassador_accepted_at is not None:
        return _STATE_WAITING
    if side == Match.Side.REFEREE and match.referee_accepted_at is not None:
        return _STATE_WAITING
    return _STATE_ACTIONABLE


def match_detail(request: HttpRequest, token: str) -> HttpResponse:
    """Render the match page (GET) or handle the no-JS POST fallback.

    No ``@login_required`` — the signed token authenticates the viewer. Token
    read failure or an unrelated registration → 400 with the invalid template.

    GET: render the full ``public/match.html`` page with display state and,
    only when ``match.status == ACCEPTED``, the counterpart's contact details.

    POST (no-JS fallback): ``action=accept|decline`` → call the relevant service
    → PRG redirect back to this view. The window and status are re-checked before
    calling the service to avoid a double-submit 500.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return render(request, "public/match_invalid.html", status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    # Identify counterpart for the accepted PII reveal.
    counterpart = (
        match.referee_registration
        if side == Match.Side.AMBASSADOR
        else match.ambassador_registration
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("accept", "decline") and display_state == _STATE_ACTIONABLE:
            try:
                if action == "accept":
                    accept_match(match, registration)
                else:
                    decline_match(match, registration)
            except ValueError:
                # Match status changed between read and action; fall through
                # to re-render the updated state.
                pass
        # PRG: redirect back to the match page after POST.
        return redirect(reverse("public:match", args=[token]))

    context = {
        "match": match,
        "registration": registration,
        "side": side,
        "display_state": display_state,
        "state_actionable": _STATE_ACTIONABLE,
        "state_waiting": _STATE_WAITING,
        "state_terminal": _STATE_TERMINAL,
        "accept_url": reverse("public:match_accept", args=[token]),
        "decline_url": reverse("public:match_decline", args=[token]),
    }
    # Reveal counterpart PII ONLY when both parties have accepted (Invariant 1).
    if match.status == Match.Status.ACCEPTED:
        context["counterpart"] = counterpart

    return render(request, "public/match.html", context)


@require_htmx
def match_accept(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: accept the match and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7). Re-validates the token,
    confirms the match is still PROPOSED and within the window, then calls
    ``accept_match``. Renders ``public/partials/match_actions.html`` reflecting
    the resulting state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    if display_state == _STATE_ACTIONABLE:
        try:
            match = accept_match(match, registration)
        except ValueError:
            # Status changed between resolution and action; re-render current state.
            pass
        display_state = _compute_match_display_state(match, side)

    counterpart = (
        match.referee_registration
        if side == Match.Side.AMBASSADOR
        else match.ambassador_registration
    )

    context = {
        "match": match,
        "registration": registration,
        "side": side,
        "display_state": display_state,
        "state_actionable": _STATE_ACTIONABLE,
        "state_waiting": _STATE_WAITING,
        "state_terminal": _STATE_TERMINAL,
        "accept_url": reverse("public:match_accept", args=[token]),
        "decline_url": reverse("public:match_decline", args=[token]),
    }
    if match.status == Match.Status.ACCEPTED:
        context["counterpart"] = counterpart

    return render(request, "public/partials/match_actions.html", context)


@require_htmx
def match_decline(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: decline the match and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7). Re-validates the token,
    confirms the match is still PROPOSED and within the window, then calls
    ``decline_match``. Renders ``public/partials/match_actions.html`` reflecting
    the resulting state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    if display_state == _STATE_ACTIONABLE:
        try:
            match = decline_match(match, registration)
        except ValueError:
            # Status changed between resolution and action; re-render current state.
            pass
        display_state = _compute_match_display_state(match, side)

    counterpart = (
        match.referee_registration
        if side == Match.Side.AMBASSADOR
        else match.ambassador_registration
    )

    context = {
        "match": match,
        "registration": registration,
        "side": side,
        "display_state": display_state,
        "state_actionable": _STATE_ACTIONABLE,
        "state_waiting": _STATE_WAITING,
        "state_terminal": _STATE_TERMINAL,
        "accept_url": reverse("public:match_accept", args=[token]),
        "decline_url": reverse("public:match_decline", args=[token]),
    }
    if match.status == Match.Status.ACCEPTED:
        context["counterpart"] = counterpart

    return render(request, "public/partials/match_actions.html", context)
