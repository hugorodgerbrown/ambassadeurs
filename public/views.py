# Public-facing views: the landing page and the single-step registration flow
# (VERB-24).
#
# The combined registration flow: the homepage role buttons open the form
# directly (no login required). The form includes an email field. On submit,
# a Registration is created with status PENDING and a signed confirmation link
# is emailed. Clicking the link transitions PENDING → WAITING, triggers
# matching, logs the user in, and shows the in-queue page.
#
# Facebook-login references have been removed from the UI (VERB-24 P2). The
# allauth backend and URL mount remain untouched in config/.

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts.services import mark_email_verified
from accounts.tokens import (
    make_registration_confirmation_token,
    read_registration_confirmation_token,
)
from core.decorators import require_htmx
from matching.forms import RegistrationForm
from matching.models import Registration
from matching.services import (
    confirm_registration,
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


def _send_confirmation_email(request: HttpRequest, registration: Registration) -> str:
    """Email a signed confirmation link for ``registration``.

    The token carries ``registration.pk`` scoped to the single-purpose salt
    ``accounts.registration-confirm`` (Invariant 6). Returns the confirm URL
    so the caller can stash it for the DEBUG shortcut.
    """
    token = make_registration_confirmation_token(registration.pk)
    confirm_url = request.build_absolute_uri(
        reverse("public:register_confirm", args=[token])
    )
    subject = _("Confirm your email to join the queue")
    body = _(
        "Click the link below to confirm your email and join the matching queue "
        "for the 4 Vallées Ambassadors Program:\n\n"
        "%(url)s\n\n"
        "This link expires in 24 hours. If you didn't request it, ignore this email."
    ) % {"url": confirm_url}
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [registration.user.email])

    # In development the email is written to the console, where the long confirm
    # URL is quoted-printable soft-wrapped and awkward to copy. Log the
    # unwrapped link on a single line for convenience. Gated on DEBUG so the
    # signed token never reaches production logs.
    if settings.DEBUG:
        logger.info(
            "Confirmation link for registration pk=%s: %s",
            registration.pk,
            confirm_url,
        )

    return confirm_url


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
    try:
        pending_reg = Registration.objects.get(
            user__email=email, status=Registration.Status.PENDING
        )
        confirm_url = _send_confirmation_email(request, pending_reg)
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
        confirm_url = _send_confirmation_email(request, registration)

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
