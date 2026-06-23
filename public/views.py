# Public-facing views: the landing page and the streamlined, verify-first
# registration flow (VERB-9).
#
# The flow is: capture + verify the email (signed-link or Facebook) -> choose a
# role -> fill the role-specific details (loaded on demand via HTMX) -> done.
# The Account/User/Registration creation lives in the matching app.

from __future__ import annotations

from typing import cast

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts.services import get_or_create_participant_user
from accounts.tokens import (
    make_email_verification_token,
    read_email_verification_token,
)
from core.decorators import require_htmx
from matching.forms import RegistrationEmailForm, RegistrationForm
from matching.models import Registration, Season
from matching.services import register_participant

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
        {"registration_open": Season.objects.active().exists()},
    )


def legal_page(request: HttpRequest, page: str) -> HttpResponse:
    """Render a static legal document (privacy / cookies / terms)."""
    if page not in LEGAL_PAGES:
        raise Http404("Unknown legal page.")
    return render(request, f"public/legal/{page}.html")


# A no-op service worker served at the origin root so browsers stop 404-ing on
# /sw.js. We intentionally register no fetch/cache handlers (VERB-7).
_SERVICE_WORKER_BODY = "/* 4 Vallées Ambassadors — intentionally minimal. */\n"


def service_worker(request: HttpRequest) -> HttpResponse:
    """Serve a minimal no-op service worker at /sw.js."""
    return HttpResponse(_SERVICE_WORKER_BODY, content_type="application/javascript")


def _send_verification_email(request: HttpRequest, email: str) -> None:
    """Email a single-purpose, expiring signed link that verifies ``email``."""
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


def register_start(request: HttpRequest) -> HttpResponse:
    """Step 1-3: capture the email and send a verification link (or use Facebook).

    A ``?role=`` hint from the homepage CTA is remembered in the session and
    pre-selected at the details step. An already-authenticated user skips
    straight to the details step.
    """
    role_hint = request.GET.get("role")
    if role_hint in ROLE_BY_SLUG:
        request.session["register_role"] = role_hint

    if not Season.objects.active().exists():
        return render(request, "public/register_closed.html")

    if request.user.is_authenticated:
        return redirect("public:register_details")

    if request.method == "POST":
        form = RegistrationEmailForm(request.POST)
        if form.is_valid():
            _send_verification_email(request, form.cleaned_data["email"])
            return redirect("public:register_email_sent")
    else:
        form = RegistrationEmailForm()

    return render(request, "public/register_start.html", {"form": form})


def register_email_sent(request: HttpRequest) -> HttpResponse:
    """Confirmation that the verification email has been sent."""
    return render(request, "public/register_email_sent.html")


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
    season = Season.objects.active().first()
    if season is None:
        return render(request, "public/register_closed.html")

    user = cast(User, request.user)

    if request.method == "POST":
        role = request.POST.get("role", "")
        role_value = ROLE_BY_SLUG.get(role)
        if role_value is None:
            raise Http404("Unknown registration role.")
        form = RegistrationForm(
            role=role_value, season=season, data=request.POST, user=user
        )
        if form.is_valid():
            data = form.cleaned_data
            register_participant(
                season=season,
                role=role_value,
                user=user,
                first_name=data["first_name"],
                last_name=data["last_name"],
                price_category=data["price_category"],
                preferred_location=data["preferred_location"],
                preferred_language=data["preferred_language"],
            )
            request.session.pop("register_role", None)
            return redirect("public:register_done", role=role)
        return render(
            request,
            "public/register_details.html",
            {"bound_form": form, "bound_role": role, "bound_role_value": role_value},
        )

    return render(
        request,
        "public/register_details.html",
        {"role_hint": request.session.get("register_role")},
    )


@login_required
@require_htmx
def register_details_form(request: HttpRequest) -> HttpResponse:
    """Return the role-specific details form fragment (HTMX, step 5)."""
    season = Season.objects.active().first()
    if season is None:
        raise Http404("Registration is closed.")
    role = request.GET.get("role", "")
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")
    user = cast(User, request.user)
    form = RegistrationForm(role=role_value, season=season, user=user)
    return render(
        request,
        "public/partials/register_details_form.html",
        {"form": form, "role": role, "role_value": role_value},
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
