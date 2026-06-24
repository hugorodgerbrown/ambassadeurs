# Public-facing views: the landing page, the streamlined registration flow
# (VERB-9), and the match accept/decline flow (VERB-19).
#
# Registration flow: capture + verify the email (signed-link or Facebook) ->
# choose a role -> fill the role-specific details (loaded on demand via HTMX) ->
# done. The User/Registration creation lives in the matching app services.
#
# Match flow: a signed email link carries the participant to /match/<token>/
# where they can accept or decline. No @login_required — the signed token IS
# the authentication. HTMX partial views for accept/decline are guarded with
# require_htmx (Invariant 7). Contact PII is revealed ONLY on ACCEPTED status
# (Invariant 1).

from __future__ import annotations

import logging
from typing import cast

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.services import get_or_create_participant_user
from accounts.tokens import (
    make_email_verification_token,
    make_match_access_token,
    read_email_verification_token,
    read_match_access_token,
)
from core.decorators import require_htmx
from matching.forms import RegistrationEmailForm, RegistrationForm
from matching.models import Match, Registration
from matching.services import (
    accept_match,
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


def _send_verification_email(request: HttpRequest, email: str) -> str:
    """Email a single-purpose, expiring signed link that verifies ``email``.

    Returns the verify URL so the caller can surface it in development.
    """
    token = make_email_verification_token(email)
    verify_url = request.build_absolute_uri(
        reverse("public:register_verify", args=[token])
    )
    subject = _("Confirm your email to register")
    body = _(
        "Click the link below to confirm your email and continue registering "
        "for the 4 Vallées Ambassadors Program:\n\n"
        "%(url)s\n\n"
        "This link expires in 24 hours. If you didn't request it, ignore this email."
    ) % {"url": verify_url}
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email])

    # In development the email is written to the console, where the long verify
    # URL is quoted-printable soft-wrapped (a stray ``=`` mid-token) and so is
    # awkward to copy. Log the unwrapped link on a single line for convenience.
    # Gated on DEBUG so the sensitive signed token never reaches production logs.
    if settings.DEBUG:
        logger.info("Verification link for %s: %s", email, verify_url)

    return verify_url


def register_start(request: HttpRequest) -> HttpResponse:
    """Step 1-3: capture the email and send a verification link (or use Facebook).

    A ``?role=`` hint from the homepage CTA is remembered in the session and
    pre-selected at the details step. An already-authenticated user skips
    straight to the details step.
    """
    role_hint = request.GET.get("role")
    if role_hint in ROLE_BY_SLUG:
        request.session["register_role"] = role_hint

    if not is_registration_open():
        return render(request, "public/register_closed.html")

    if request.user.is_authenticated:
        return redirect("public:register_details")

    if request.method == "POST":
        form = RegistrationEmailForm(request.POST)
        if form.is_valid():
            verify_url = _send_verification_email(request, form.cleaned_data["email"])
            # In development, carry the link to the confirmation page so a tester
            # can click straight through without opening the console/inbox. Never
            # stashed outside DEBUG so the signed token stays out of production.
            if settings.DEBUG:
                request.session["debug_verify_url"] = verify_url
            return redirect("public:register_email_sent")
    else:
        form = RegistrationEmailForm()

    return render(request, "public/register_start.html", {"form": form})


def register_email_sent(request: HttpRequest) -> HttpResponse:
    """Confirmation that the verification email has been sent.

    In development the verify link is shown on the page (pulled from the
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


def register_verify(request: HttpRequest, token: str) -> HttpResponse:
    """Step 3a: consume the signed link, log the user in, go to the details step."""
    email = read_email_verification_token(token)
    if email is None:
        return render(request, "public/register_invalid.html", status=400)
    user = get_or_create_participant_user(email)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("public:register_details")


@login_required
def register_details(request: HttpRequest) -> HttpResponse:
    """Step 4-5: choose a role and submit the role-specific details."""
    if not is_registration_open():
        return render(request, "public/register_closed.html")

    user = cast(User, request.user)

    if request.method == "POST":
        role = request.POST.get("role", "")
        role_value = ROLE_BY_SLUG.get(role)
        if role_value is None:
            raise Http404("Unknown registration role.")
        form = RegistrationForm(role=role_value, data=request.POST, user=user)
        if form.is_valid():
            data = form.cleaned_data
            register_participant(
                role=role_value,
                user=user,
                first_name=data["first_name"],
                last_name=data["last_name"],
                prior_pass=data["prior_pass"],
                phone=data.get("phone", ""),
                preferred_location=data.get("preferred_location", ""),
                preferred_language=data.get("preferred_language", ""),
                accepted_terms=form.accepted_statements(),
            )
            request.session.pop("register_role", None)
            return redirect("public:register_done", role=role)
        return render(
            request,
            "public/register_details.html",
            {"form": form, "role": role, "role_value": role_value},
        )

    # On first load the surface is themed for the role the participant hinted at
    # on the homepage (carried in the session), defaulting to Ambassador — the
    # dropdown re-themes the surface to the other role on demand via HTMX.
    role = request.session.get("register_role") or "ambassador"
    role_value = ROLE_BY_SLUG.get(role, Registration.Role.AMBASSADOR)
    form = RegistrationForm(role=role_value, user=user)
    return render(
        request,
        "public/register_details.html",
        {"form": form, "role": role, "role_value": role_value},
    )


@login_required
@require_htmx
def register_details_form(request: HttpRequest) -> HttpResponse:
    """Return the themed registration surface for a role (HTMX, step 5).

    Drives the "Your role" dropdown: selecting a role swaps the whole
    ``#reg-surface`` so the eyebrow, lead copy, eligibility callout, form and
    submit button all re-tone to the chosen role.
    """
    if not is_registration_open():
        raise Http404("Registration is closed.")
    role = request.GET.get("role", "")
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")
    user = cast(User, request.user)
    form = RegistrationForm(role=role_value, user=user)
    return render(
        request,
        "public/partials/register_surface.html",
        {"form": form, "role": role, "role_value": role_value, "is_htmx": True},
    )


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


# ---------------------------------------------------------------------------
# Match accept / decline flow (VERB-19)
# ---------------------------------------------------------------------------

# Display states for the match page — computed from match status + window.
_STATE_ACTIONABLE = "actionable"  # PROPOSED, within window, this side not yet responded
_STATE_WAITING = "waiting"  # PROPOSED but this side already accepted
_STATE_TERMINAL = "terminal"  # ACCEPTED / DECLINED / EXPIRED / ABANDONED, or window lapsed


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


def _compute_match_display_state(
    match: Match, side: Match.Side
) -> str:
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
