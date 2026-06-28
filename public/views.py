# Public-facing views: the landing page, the single-step registration flow
# (VERB-24), and the match accept/decline/report-no-show flow (VERB-19/VERB-21).
#
# Registration flow (VERB-24): the homepage role buttons open a combined form
# directly — no login required. The form includes an email field. On submit,
# a Registration is created with status UNVERIFIED and a signed confirmation
# link is emailed. Clicking the link transitions UNVERIFIED → VERIFIED, triggers
# matching, logs the user in, and redirects to register_done. Facebook-login
# references have been removed from the UI (VERB-24 P2); the allauth backend
# and URL mount remain untouched in config/.
#
# Match flow (VERB-19): a signed email link carries the participant to
# /match/<token>/ where they can accept or decline. No @login_required — the
# signed token IS the authentication. HTMX partial views for accept/decline are
# guarded with require_htmx (Invariant 7). Contact PII is revealed ONLY when
# match.status == ACCEPTED (Invariant 1).
#
# Withdraw acceptance (VERB-43 / ADR 0010): while a match is PENDING (one side
# accepted), the side that already accepted may retract via match_withdraw
# (@require_htmx) — a clean no-penalty un-accept (PENDING → PROPOSED) that
# returns them to the actionable proposed view.
#
# No-show reporting (VERB-21): once a match is ACCEPTED, either party may
# report the other as a post-accept no-show via
# match_report_no_show (@require_htmx) or the no-JS POST fallback in
# match_detail. The report transitions the match to CANCELLED, suspends the
# accused, and re-queues the reporter to the front of the pool.
#
# Wrong-user journey (VERB-32): match_detail GET branches on the auth state of
# the viewer. Anonymous → token auth (existing behaviour). Authenticated
# participant → own-side view (via match.side_of). Authenticated
# non-participant → 403 with match_forbidden.html. The shared
# _render_match_page helper is importable by accounts.views so that the
# tokenless accounts:match route can render the identical match.html.

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
from django.views.decorators.http import require_POST

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
    queue_position,
    register_participant,
    report_no_show,
    total_accepted_matches,
    withdraw_acceptance,
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
        # After is_authenticated, Django stubs narrow request.user to User.
        anon_user: User | None = request.user if request.user.is_authenticated else None
        already_registered = _authenticated_registration(request)
        if already_registered is not None:
            # Lock the form to the role they actually registered with, ignoring
            # the ?role= they arrived on (e.g. an already-registered ambassador
            # clicking "I'm a Referee" on the homepage). The form is read-only,
            # so it must reflect their record, not the link they followed.
            role_value = Registration.Role(already_registered.role)
        # Derive the display slug from the validated role value so an unknown
        # ?role= param falls back gracefully to ambassador.
        role_slug = SLUG_BY_ROLE[role_value]
        form = RegistrationForm(role=role_value, user=anon_user)
        if already_registered is not None:
            for field in form.fields.values():
                field.disabled = True
        return render(
            request,
            "public/register_details.html",
            {
                "form": form,
                "role": role_slug,
                "role_value": role_value,
                "already_registered": already_registered,
            },
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

    # Anonymous path: validate, create UNVERIFIED or resend.
    form = RegistrationForm(role=role_value, data=request.POST)
    if not form.is_valid():
        return render(
            request,
            "public/register_details.html",
            {"form": form, "role": role_slug, "role_value": role_value},
        )

    data = form.cleaned_data
    email: str = data["email"]

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
                    accepted_terms=form.accepted_statements(),
                    status=Registration.Status.UNVERIFIED,
                )
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

    if registration.status != Registration.Status.UNVERIFIED:
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
    """Render the post-registration "what happens next" confirmation page.

    Resolves the authenticated user's registration (if any) and, when the
    registration is in WAITING status, adds ``queue_position`` and
    ``total_accepted_matches`` to the context so the template can display the
    participant's position in the pool.
    """
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")

    registration = _authenticated_registration(request)
    position: int | None = None
    accepted_count: int = 0
    if registration is not None and registration.status == Registration.Status.VERIFIED:
        position = queue_position(registration)
        accepted_count = total_accepted_matches()

    return render(
        request,
        "public/register_done.html",
        {
            "role": role,
            "role_value": role_value,
            "queue_position": position,
            "total_accepted_matches": accepted_count,
        },
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
    already_registered = _authenticated_registration(request)
    if already_registered is not None:
        # Already registered: the locked surface must show their actual role,
        # not whichever role this swap requested (the picker is disabled, but a
        # crafted request must not re-theme the form).
        role_value = Registration.Role(already_registered.role)
        role = SLUG_BY_ROLE[role_value]
    form = RegistrationForm(role=role_value, user=htmx_user)
    if already_registered is not None:
        for field in form.fields.values():
            field.disabled = True
    return render(
        request,
        "public/partials/register_surface.html",
        {
            "form": form,
            "role": role,
            "role_value": role_value,
            "is_htmx": True,
            "already_registered": already_registered,
        },
    )


# ---------------------------------------------------------------------------
# Match accept / decline flow (VERB-19)
# ---------------------------------------------------------------------------

# Display states for the match page — computed from match status + window.
_STATE_ACTIONABLE = "actionable"  # PROPOSED, within window, this side not yet responded
_STATE_WAITING = "waiting"  # PENDING and this side already accepted
_STATE_TERMINAL = (
    "terminal"  # ACCEPTED / DECLINED / EXPIRED / CANCELLED, or window lapsed
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

    PROPOSED and PENDING are both "active" states — neither is terminal. A
    PENDING match has one acceptance already recorded; if this side accepted, the
    viewer is in the _STATE_WAITING sub-state (waiting on the partner). If the
    *other* side accepted (and match is PENDING), this side hasn't responded yet
    so the viewer is still _STATE_ACTIONABLE.
    """
    if match.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
        return _STATE_TERMINAL
    if timezone.now() > match.expires_at:
        return _STATE_TERMINAL
    # Active match within the window. Check if this side has accepted.
    if side == Match.Side.AMBASSADOR and match.ambassador_accepted_at is not None:
        return _STATE_WAITING
    if side == Match.Side.REFEREE and match.referee_accepted_at is not None:
        return _STATE_WAITING
    return _STATE_ACTIONABLE


def _side_accepted(match: Match, side: Match.Side) -> bool:
    """Return whether the given side has recorded an acceptance on the match."""
    if side == Match.Side.AMBASSADOR:
        return match.ambassador_accepted_at is not None
    return match.referee_accepted_at is not None


def _other_side(side: Match.Side) -> Match.Side:
    """Return the opposite side of a match."""
    if side == Match.Side.AMBASSADOR:
        return Match.Side.REFEREE
    return Match.Side.AMBASSADOR


def _match_view(match: Match, side: Match.Side) -> str:
    """Return the design view key for the match page, from the viewer's side.

    One of ``proposed``, ``you_accepted``, ``partner_accepted``, ``confirmed``,
    ``declined_you``, ``declined_partner``, ``expired``, ``abandoned_you``,
    ``abandoned_partner``. A PROPOSED or PENDING match whose contact window has
    lapsed is presented as ``expired`` — both parties re-queue, the same outcome
    as a swept expiry — so the page need not distinguish the two.

    This drives only presentation (header copy + outcome block); the action
    guards use ``_compute_match_display_state``.
    """
    status = match.status
    if status == Match.Status.ACCEPTED:
        return "confirmed"
    if status == Match.Status.DECLINED:
        return "declined_you" if match.declined_by == side else "declined_partner"
    if status == Match.Status.EXPIRED:
        return "expired"
    if status == Match.Status.CANCELLED:
        return (
            "abandoned_you"
            if match.no_show_reported_by == side
            else "abandoned_partner"
        )
    # PROPOSED or PENDING — distinguish by window and per-side acceptance.
    if timezone.now() > match.expires_at:
        return "expired"
    if _side_accepted(match, side):
        return "you_accepted"
    if _side_accepted(match, _other_side(side)):
        return "partner_accepted"
    return "proposed"


def _side_status_key(match: Match, side: Match.Side) -> str:
    """Return the roster pill key for one side.

    One of ``accepted`` / ``declined`` / ``no_response`` / ``pending``. Derived
    from the match alone (viewer-independent) so both roster rows read true.
    """
    if match.status == Match.Status.ACCEPTED or _side_accepted(match, side):
        return "accepted"
    if match.declined_by == side:
        return "declined"
    if match.status == Match.Status.EXPIRED or (
        match.status in (Match.Status.PROPOSED, Match.Status.PENDING)
        and timezone.now() > match.expires_at
    ):
        return "no_response"
    return "pending"


def _roster_row(
    registration: Registration | None,
    role_side: Match.Side,
    viewer_side: Match.Side,
    match: Match,
) -> dict[str, object]:
    """Build one roster row's display data.

    Reveals the party's first name and initials (the match redesign shows these
    from the proposed state; contact PII — email and phone — stays hidden until
    mutual accept, see Invariant 1). ``registration`` is ``None`` when the party
    declined and their account was deleted, in which case the template falls back
    to a generic label.
    """
    name = ""
    initials = ""
    if registration is not None:
        first = registration.user.first_name or ""
        last = registration.user.last_name or ""
        name = first
        initials = (first[:1] + last[:1]).upper()
    return {
        "side": role_side,
        "name": name,
        "initials": initials,
        "exists": registration is not None,
        "is_you": role_side == viewer_side,
        "status": _side_status_key(match, role_side),
    }


def _related_registration(match: Match, attr: str) -> Registration | None:
    """Return ``match.<attr>`` (a Registration FK), or None if the row is gone.

    A decline deletes the decliner's Registration and SET_NULLs the FK. A
    freshly-loaded match returns None for the FK, but an in-memory match handed
    back by the decline service still holds the stale FK id, so the lazy load
    raises ``Registration.DoesNotExist``; treat that as None.
    """
    try:
        related: Registration | None = getattr(match, attr)
    except Registration.DoesNotExist:
        return None
    return related


def _match_context(
    match: Match,
    registration: Registration,
    side: Match.Side,
    *,
    token: str | None = None,
) -> dict[str, object]:
    """Build the shared context for the match page and its action partial.

    Used by ``_render_match_page`` (full page) and the HTMX action endpoints
    (which render only ``public/partials/match_actions.html``). When ``token`` is
    provided the HTMX action URLs are included; otherwise they are omitted (the
    tokenless account route does not expose the action forms).

    The counterpart's contact details (email, phone) are included ONLY when
    ``match.status == ACCEPTED`` (Invariant 1); the first name is revealed
    earlier via ``partner_name`` and the roster.
    """
    # Resolve both sides defensively: after a decline the decliner's User and
    # Registration are deleted and the match FK is SET_NULL, but an in-memory
    # match returned by the service still carries the stale FK id, so a lazy load
    # raises DoesNotExist. A freshly-loaded match returns None cleanly.
    ambassador_reg = _related_registration(match, "ambassador_registration")
    referee_reg = _related_registration(match, "referee_registration")
    counterpart = referee_reg if side == Match.Side.AMBASSADOR else ambassador_reg
    view = _match_view(match, side)
    context: dict[str, object] = {
        "match": match,
        "registration": registration,
        "side": side,
        "view": view,
        "roster": [
            _roster_row(ambassador_reg, Match.Side.AMBASSADOR, side, match),
            _roster_row(referee_reg, Match.Side.REFEREE, side, match),
        ],
        "partner_name": (
            counterpart.user.first_name if counterpart is not None else ""
        ),
        "show_deadline": view in ("proposed", "you_accepted", "partner_accepted"),
    }
    if token is not None:
        context["accept_url"] = reverse("public:match_accept", args=[token])
        context["decline_url"] = reverse("public:match_decline", args=[token])
        context["withdraw_url"] = reverse("public:match_withdraw", args=[token])
        context["report_no_show_url"] = reverse(
            "public:match_report_no_show", args=[token]
        )
    # Reveal counterpart PII ONLY when both parties have accepted (Invariant 1).
    if match.status == Match.Status.ACCEPTED:
        context["counterpart"] = counterpart
    return context


def _render_match_page(
    request: HttpRequest,
    match: Match,
    registration: Registration,
    side: Match.Side,
    *,
    token: str | None = None,
) -> HttpResponse:
    """Build the match-page context and return the rendered ``public/match.html``.

    Shared by ``match_detail`` (token route) and ``accounts.views.account_match``
    (tokenless, login-required route). When ``token`` is provided the HTMX action
    URLs are included; when it is ``None`` they are omitted (the tokenless route
    does not expose action partials).
    """
    context = _match_context(match, registration, side, token=token)
    return render(request, "public/match.html", context)


def match_detail(request: HttpRequest, token: str) -> HttpResponse:
    """Render the match page (GET) or handle the no-JS POST fallback.

    No ``@login_required`` — the signed token authenticates the viewer. Token
    read failure or an unrelated registration → 400 with the invalid template.

    GET: render the full ``public/match.html`` page with display state and,
    only when ``match.status == ACCEPTED``, the counterpart's contact details.
    The auth branch (VERB-32) applies:
    - Anonymous → token side (existing behaviour).
    - Authenticated participant (one of the two parties) → own side via
      ``match.side_of(registration)`` so they always see their own perspective.
    - Authenticated non-participant → 403 with ``match_forbidden.html``.

    POST (no-JS fallback): ``action=accept|decline`` → call the relevant service
    → PRG redirect back to this view. The window and status are re-checked before
    calling the service to avoid a double-submit 500.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return render(request, "public/match_invalid.html", status=400)

    match, token_registration, token_side = resolved

    if request.method == "POST":
        # POST always uses token authentication (the no-JS form carries the token
        # in the URL). The display_state check is against the token side.
        display_state = _compute_match_display_state(match, token_side)
        action = request.POST.get("action")
        if action in ("accept", "decline") and display_state == _STATE_ACTIONABLE:
            try:
                if action == "accept":
                    accept_match(match, token_registration)
                else:
                    decline_match(match, token_registration)
                    # After a successful decline the decliner's User and
                    # Registration are deleted. Redirecting would re-resolve
                    # the token, find the FK NULL, and return 400. Render the
                    # removed page directly instead (no PRG for this terminal
                    # path).
                    return render(request, "public/match_removed.html")
            except ValueError:
                # Match status changed between read and action; fall through
                # to PRG redirect so the updated state is displayed.
                pass
        elif (
            action == "report_no_show"
            and match.status == Match.Status.ACCEPTED
            and not match.no_show_reported_by
        ):
            try:
                report_no_show(match, token_registration)
            except ValueError:
                # Match status changed or already reported; fall through.
                pass
        # PRG: redirect back to the match page after POST.
        return redirect(reverse("public:match", args=[token]))

    # GET — auth branch (VERB-32).
    auth_registration = _authenticated_registration(request)
    if not request.user.is_authenticated:
        # Anonymous: render from the token's side (existing behaviour).
        return _render_match_page(
            request, match, token_registration, token_side, token=token
        )

    # Authenticated. Check whether this user is a party on the match.
    if auth_registration is not None and auth_registration.pk in (
        match.ambassador_registration_id,
        match.referee_registration_id,
    ):
        # Authenticated participant: render from their own side.
        own_side = match.side_of(auth_registration)
        return _render_match_page(
            request, match, auth_registration, own_side, token=token
        )

    # Authenticated non-participant (wrong user or no registration).
    return render(request, "public/match_forbidden.html", status=403)


@require_htmx
@require_POST
def match_accept(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: accept the match and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — a GET,
    even with the HX header, must not trigger the accept transition. Re-validates
    the token, confirms the match is still PROPOSED and within the window, then
    calls ``accept_match``. Renders ``public/partials/match_actions.html``
    reflecting the resulting state.
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

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)


@require_htmx
@require_POST
def match_withdraw(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: withdraw this side's acceptance and return the actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — a GET,
    even with the HX header, must not retract an acceptance. Re-validates the
    token, confirms this side is in the WAITING (already-accepted) display state,
    then calls ``withdraw_acceptance``. The side returns to the actionable
    ``proposed`` view. Renders ``public/partials/match_actions.html`` reflecting
    the resulting state.

    A POST once the state is no longer WAITING (e.g. the partner accepted and the
    match is now ACCEPTED) is a safe no-op: the guard skips the service call and
    the partial re-renders with the current state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    if display_state == _STATE_WAITING:
        try:
            match = withdraw_acceptance(match, registration)
        except ValueError:
            # Status changed between resolution and action; re-render current state.
            pass

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)


@require_htmx
@require_POST
def match_decline(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: decline the match and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — decline
    is destructive (deletes the decliner's User) so a GET, even with the HX
    header, must not trigger it. Re-validates the token, confirms the match is
    still PROPOSED and within the window, then calls ``decline_match``. Renders
    ``public/partials/match_actions.html`` reflecting the resulting state.
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

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)


@require_htmx
@require_POST
def match_report_no_show(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: report a post-accept no-show and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — the
    action is irreversible (suspends the accused's registration) so a GET, even
    with the HX header, must not trigger it.

    Re-validates the token, confirms the match is still ACCEPTED with no
    existing report, then calls ``report_no_show``. Renders
    ``public/partials/match_actions.html`` reflecting the resulting ABANDONED
    state.

    A second POST on a match that is already CANCELLED (or otherwise
    non-ACCEPTED) is a safe no-op: the service raises ``ValueError``, which is
    caught and silently swallowed, and the partial is re-rendered with the
    current state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved

    if match.status == Match.Status.ACCEPTED and not match.no_show_reported_by:
        try:
            match = report_no_show(match, registration)
        except ValueError:
            # Status changed or already reported between resolution and action;
            # re-render current state.
            match.refresh_from_db()

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)
