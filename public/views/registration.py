# Registration flow (VERB-24, split into a chooser + role-hardwired forms by
# VERB-131): registration is a two-step journey. Step one, register_role, is a
# two-question matrix chooser — "Did you have a 4 Vallées season pass in
# 2024/25?" / "… in 2025/26?" — with a live "You will be registering as …"
# statement (HTMX-refreshed via register_role_derive) and an explicit Continue
# button. The two answers are transient, used only to derive the role
# server-side (_derive_role_from_seasons: any yes → Ambassador, both no →
# Referee); they are never persisted. Continue POSTs both answers back to
# register_role, which re-derives and redirects to the matching role's form —
# no login required, and the client never posts a role directly. Step two,
# register_form, is a role-hardwired form (deep-linkable at /register/<role>/,
# no in-page role toggle) that carries a hidden role field; the view treats
# the URL's role kwarg as authoritative and 404s on any mismatch with the
# posted value. The form includes an email field. On submit, a Registration is
# created with status UNVERIFIED and a signed confirmation link is emailed.
# Clicking the link transitions UNVERIFIED → VERIFIED, triggers matching, logs
# the user in, and redirects to register_done. allauth has been removed
# (VERB-46); login uses Django's ModelBackend directly. The bare register view
# is kept, under its original name, purely as a redirect that preserves every
# existing {% url 'public:register' %} link: it forwards a recognised ?role=
# straight to that role's form, and anything else to the chooser.
#
# Paid-tier deposit flow (VERB-86, ADR 0014): register_confirm branches on
# Registration.fee_chf — free (0) confirms immediately as before; paid (>0)
# logs the user in but leaves the registration UNVERIFIED and redirects to
# register_payment_start (payments.py), which creates a Stripe hosted
# Checkout session and redirects the browser to it. An UNVERIFIED paid-tier
# registration is never matched — pool entry is gated on both email
# confirmation AND payment (Invariant 2's spirit).
#
# Willingness-to-pay survey (VERB-111): register_done shows a short,
# skippable, single-question survey to VERIFIED, free-tier (fee_chf == 0)
# registrants who have not already responded, asking directly for the
# highest refundable deposit they would have been happy to pay to register.
# survey.register_survey_submit validates and creates the SurveyResponse row,
# returning the thanks fragment in place.
#
# Page access permissions (VERB-115): an already-registered, logged-in user
# hitting either enrolment surface — register_role (GET) or register_form
# (GET/POST) — receives a plain 403 (register_forbidden.html) instead of a
# locked/disabled form. This replaces the earlier locked-form and defensive-
# authenticated-POST behaviour.
#
# PostHog analytics (VERB-124): the anonymous registration success path
# (orchestrated by public.services.register_or_resend_participant, called from
# register_form) calls alias_identities so pre-registration page-views
# (anonymous hash) merge into the resulting user in PostHog.
#
# Signup orchestration (VERB-142): register_form's POST path validates the
# form, then delegates the enrol-or-resend decision to
# public.services.register_or_resend_participant, which is request-coupled
# (absolute URLs, PostHog cookies, session) and so lives in public/services.py
# rather than a matching/ domain service.

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from accounts.tokens import read_registration_confirmation_token
from core.decorators import require_htmx
from core.ratelimit import rate_limited_response
from matching.forms import RegistrationForm
from matching.models import Registration
from matching.selectors import match_status_context, status_pill_for
from matching.services import confirm_registration, is_registration_open
from public.forms import SurveyResponseForm
from public.models import SurveyResponse
from public.services import register_or_resend_participant

from ._shared import _authenticated_registration

logger = logging.getLogger(__name__)

# Map the public URL slug to the stored Role value. Defining the valid slugs
# here keeps unknown roles out of the view (404) and out of the templates.
ROLE_BY_SLUG = {
    "ambassador": Registration.Role.AMBASSADOR,
    "referee": Registration.Role.REFEREE,
}

# Reverse map: stored Role value → URL slug, for confirm-redirect construction.
SLUG_BY_ROLE = {v: k for k, v in ROLE_BY_SLUG.items()}


def register(request: HttpRequest) -> HttpResponse:
    """Back-compat redirect preserving every existing ``public:register`` link.

    A recognised ``?role=`` forwards straight to that role's hardwired form
    (``register_form``); anything else — including no ``?role=`` at all —
    redirects to the chooser (``register_role``). Carries no form logic of its
    own; the two destination views apply their own already-registered guard.
    """
    role_slug = request.GET.get("role")
    role_value = ROLE_BY_SLUG.get(role_slug) if role_slug is not None else None
    if role_value is not None:
        return redirect("public:register_form", role=role_slug)
    return redirect("public:register_role")


def _derive_role_from_seasons(a: str, b: str) -> str | None:
    """Derive the registration role slug from the two season pass answers.

    ``a`` and ``b`` are each expected to be ``"yes"`` or ``"no"`` (one per
    recent season). Returns ``"ambassador"`` if either is ``"yes"``,
    ``"referee"`` if both are ``"no"``, or ``None`` if either argument is
    missing or holds an unrecognised value. This is the single source of the
    routing rule, shared by ``register_role`` (POST) and
    ``register_role_derive`` (the live-statement HTMX partial).
    """
    valid = {"yes", "no"}
    if a not in valid or b not in valid:
        return None
    if a == "yes" or b == "yes":
        return "ambassador"
    return "referee"


def register_role(request: HttpRequest) -> HttpResponse:
    """Render or process the role chooser — step one of registration (VERB-131).

    A two-question matrix ("Did you have a 4 Vallées season pass in 2024/25?"
    / "… in 2025/26?"), each answered Y/N, with a live derived-role statement
    and an explicit Continue button (no auto-navigation). The two answers are
    transient — used only to derive the role for routing, never persisted.

    GET: render the matrix form.
    POST: read ``pass_2024_25`` and ``pass_2025_26``. If either is
        missing/invalid, re-render the form (200) with a validation message
        and no redirect. Otherwise derive the role via
        ``_derive_role_from_seasons`` and redirect to that role's
        ``register_form`` — the server derives the role; the client never
        posts one directly (invariant 2's spirit).

    An already-registered, logged-in user (any Registration status) receives
    a 403 (``register_forbidden.html``) instead of the chooser on either
    method — they have already enrolled (VERB-115).
    """
    if not is_registration_open():
        return render(request, "public/register_closed.html")

    already_registered = _authenticated_registration(request)
    if already_registered is not None:
        return render(request, "public/register_forbidden.html", status=403)

    if request.method == "POST":
        pass_2024_25 = request.POST.get("pass_2024_25", "")
        pass_2025_26 = request.POST.get("pass_2025_26", "")
        role_slug = _derive_role_from_seasons(pass_2024_25, pass_2025_26)
        if role_slug is None:
            return render(
                request,
                "public/register_role.html",
                {
                    "error": True,
                    "pass_2024_25": pass_2024_25,
                    "pass_2025_26": pass_2025_26,
                    # Derived from the (incomplete) answers, so the server render
                    # matches the re-checked radios rather than hard-coding the
                    # prompt. With both answers required this is None, but keeping
                    # it derive-consistent avoids a misleading statement if the
                    # rule ever relaxes.
                    "derived_role": role_slug,
                },
            )
        return redirect("public:register_form", role=role_slug)

    return render(request, "public/register_role.html")


@require_htmx
def register_role_derive(request: HttpRequest) -> HttpResponse:
    """HTMX GET: render the live "You will be registering as …" statement.

    Guarded by ``@require_htmx`` (Invariant 7). Reads ``pass_2024_25`` and
    ``pass_2025_26`` from the query string, derives the role via
    ``_derive_role_from_seasons``, and renders
    ``public/partials/register_role_derived.html``. No side effects, no DB
    writes — purely a presentational re-render of the chooser's live
    statement as the visitor answers each question.
    """
    pass_2024_25 = request.GET.get("pass_2024_25", "")
    pass_2025_26 = request.GET.get("pass_2025_26", "")
    derived_role = _derive_role_from_seasons(pass_2024_25, pass_2025_26)
    return render(
        request,
        "public/partials/register_role_derived.html",
        {"derived_role": derived_role},
    )


@ratelimit(key="ip", rate="30/h", method="POST", block=False)  # type: ignore[untyped-decorator]  # django-ratelimit has no type stubs
@ratelimit(key="post:email", rate="5/h", method="POST", block=False)  # type: ignore[untyped-decorator]  # django-ratelimit has no type stubs
def register_form(request: HttpRequest, role: str) -> HttpResponse:
    """Role-hardwired registration form — no login required (VERB-131).

    GET: render the form themed for the URL's ``role`` slug. Unknown role
        404s.
    POST (anonymous): validate, create an UNVERIFIED registration (or resend if
        one already exists for the email), send a confirmation email, redirect
        to ``register_email_sent``. The form carries a hidden ``role`` field
        that mirrors the URL; the URL is authoritative — a POST whose hidden
        ``role`` does not match the URL kwarg 404s, closing the tampered-body
        path (Invariant 2's spirit).

    An already-registered, logged-in user (any Registration status) hitting
    this view — GET or POST — receives a 403 (``register_forbidden.html``)
    instead of the enrolment surface: they have already enrolled and the
    surface is not theirs to see or resubmit (VERB-115).

    Rate-limited: 30 POSTs/hour per IP and 5 POSTs/hour per email address.
    Exceeding either limit returns a 429 response. The email key is derived
    from the POST param; an absent ``email`` field is treated as an empty
    string by django-ratelimit and does not trigger the per-email limit.
    """
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")

    if not is_registration_open():
        return render(request, "public/register_closed.html")

    already_registered = _authenticated_registration(request)
    if already_registered is not None:
        return render(request, "public/register_forbidden.html", status=403)

    if request.method == "GET":
        # After is_authenticated, Django stubs narrow request.user to User.
        anon_user: User | None = request.user if request.user.is_authenticated else None
        form = RegistrationForm(role=role_value, user=anon_user)
        return render(
            request,
            "public/register_details.html",
            {
                "form": form,
                "role": role,
                "role_value": role_value,
            },
        )

    # POST path.
    if getattr(request, "limited", False):
        return rate_limited_response(request)

    posted_role_slug = request.POST.get("role", "")
    if posted_role_slug != role:
        # The URL is authoritative — a mismatched (or missing) hidden role
        # field means a tampered body, not a legitimate alternate submission.
        raise Http404("Registration role does not match the submitted form.")

    # Anonymous path: validate, create UNVERIFIED or resend.
    form = RegistrationForm(role=role_value, data=request.POST)
    if not form.is_valid():
        return render(
            request,
            "public/register_details.html",
            {"form": form, "role": role, "role_value": role_value},
        )

    register_or_resend_participant(request, role_value=role_value, form=form)
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

    Reads the token, loads the Registration, and logs the user in with
    Django's ModelBackend. Free tier (``fee_chf == 0``) then transitions
    UNVERIFIED → VERIFIED via ``confirm_registration`` and redirects to
    ``register_done``, unchanged from before VERB-86. Paid tier
    (``fee_chf > 0``) does **not** confirm here — email confirmation is only
    the first of two gates — and instead redirects to
    ``register_payment_start`` (payments.py) to collect the deposit; the
    registration stays UNVERIFIED (out of the pool) until payment completes.

    Returns 400 on a bad/expired token or a non-UNVERIFIED registration (used
    or invalid link).
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

    login(
        request,
        registration.user,
        backend="django.contrib.auth.backends.ModelBackend",
    )

    if registration.fee_chf > 0:
        # Paid tier (VERB-86): payment is the second gate. Do not confirm yet.
        return redirect("public:register_payment_start")

    registration = confirm_registration(registration)

    # Derive the slug from the registration role. SLUG_BY_ROLE keys are
    # Role enum values; cast the stored str through the enum for lookup.
    role_slug = SLUG_BY_ROLE.get(Registration.Role(registration.role), "ambassador")
    return redirect("public:register_done", role=role_slug)


def register_done(request: HttpRequest, role: str) -> HttpResponse:
    """Render the post-registration "what happens next" confirmation page.

    The full Match status card (``templates/accounts/partials/match_status.html``)
    is rendered on this page too (VERB-116), sharing its context with the
    account page via ``matching.selectors.match_status_context`` — the
    registration engine runs synchronously inside ``register_participant``, so
    a user can already hold a PROPOSED (or later) match by the time they reach
    this page, and the card reflects it rather than always showing the
    pool-standing state.

    For an anonymous request (no just-registered session — should not happen
    in the standard journey, but handled defensively) a minimal, safe context
    is built directly so the card still renders its "no registration" state.

    Also adds the willingness-to-pay survey context (VERB-111) when the
    registration is VERIFIED, free-tier (``fee_chf == 0``), and has not
    already responded — ``survey_form`` is omitted entirely otherwise, so
    the template block simply does not render.
    """
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")

    if request.user.is_authenticated:
        status_context = match_status_context(request.user)
    else:
        status_context = {
            "registration": None,
            "status_pill": status_pill_for(None, "none"),
            "match_state": "none",
            "partner_first_name": "",
            "partner_accepted": False,
            "queue_position": None,
            "can_rejoin": False,
            "can_cancel": False,
        }

    context: dict[str, object] = {
        "role": role,
        "role_value": role_value,
        **status_context,
    }

    # Willingness-to-pay survey (VERB-111): shown only to a VERIFIED,
    # free-tier registration that has not already responded.
    registration = status_context["registration"]
    if (
        isinstance(registration, Registration)
        and registration.status == Registration.Status.VERIFIED
        and registration.fee_chf == 0
        and not SurveyResponse.objects.filter(registration=registration).exists()
    ):
        context["survey_form"] = SurveyResponseForm()

    return render(request, "public/register_done.html", context)
