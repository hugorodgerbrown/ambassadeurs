# Account self-service views.
#
# The authenticated participant views and edits their own profile. Role is
# shown read-only — it is fixed once registered (CLAUDE.md). Participant
# attributes (phone, preferred_language) now live on matching.Registration
# rather than a separate Account model. If the user has no registration they
# are redirected to the registration flow.

from __future__ import annotations

from typing import cast

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from matching.models import Match, Registration
from public.views import _render_match_page

from .forms import AccountForm
from .services import delete_account, send_confirmation_email, update_account


@login_required
def account_detail(request: HttpRequest) -> HttpResponse:
    """Show the participant's profile, registration and security controls."""
    user = cast(User, request.user)
    try:
        registration: Registration | None = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        registration = None

    email_verified = EmailAddress.objects.filter(user=user, verified=True).exists()

    debug_verify_url = None
    if settings.DEBUG:
        debug_verify_url = request.session.pop("debug_verify_url", None)

    return render(
        request,
        "accounts/detail.html",
        {
            "registration": registration,
            "email_verified": email_verified,
            "debug_verify_url": debug_verify_url,
        },
    )


@login_required
def account_resend_confirmation(request: HttpRequest) -> HttpResponse:
    """Resend the confirmation email for a PENDING registration.

    POST-only. Looks up the authenticated user's PENDING registration; if found,
    resends the confirmation email and stashes the URL in the session under DEBUG.
    On any other method, redirects to the account detail page without sending.
    """
    if request.method != "POST":
        return redirect("accounts:detail")

    user = cast(User, request.user)
    try:
        registration = Registration.objects.get(
            user=user, status=Registration.Status.PENDING
        )
    except Registration.DoesNotExist:
        messages.error(
            request,
            _("No pending registration found. Your email may already be confirmed."),
        )
        return redirect("accounts:detail")

    confirm_url = send_confirmation_email(request, registration)
    messages.success(
        request,
        _("Confirmation email resent. Please check your inbox."),
    )
    if settings.DEBUG:
        request.session["debug_verify_url"] = confirm_url

    return redirect("accounts:detail")


@login_required
def account_edit(request: HttpRequest) -> HttpResponse:
    """Edit the participant's name, phone and preferred language."""
    user = cast(User, request.user)
    try:
        registration: Registration | None = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        registration = None

    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            update_account(
                user=user,
                first_name=data["first_name"],
                last_name=data["last_name"],
                phone=data["phone"],
                preferred_language=data["preferred_language"],
            )
            messages.success(request, _("Your details have been updated."))
            return redirect("accounts:detail")
    else:
        form = AccountForm(
            initial={
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": registration.phone if registration else "",
                "preferred_language": (
                    registration.preferred_language if registration else ""
                ),
            }
        )
    return render(
        request,
        "accounts/edit.html",
        {"form": form, "registration": registration},
    )


@login_required
def account_delete(request: HttpRequest) -> HttpResponse:
    """Confirm (GET) and perform (POST) deletion of the participant's account."""
    if request.method == "POST":
        user = cast(User, request.user)
        logout(request)
        delete_account(user)
        messages.success(request, _("Your account has been deleted."))
        return redirect("public:home")
    return render(request, "accounts/delete.html")


@login_required
def account_match(request: HttpRequest) -> HttpResponse:
    """Render the match page for the authenticated user's active match.

    Looks up the user's non-terminal match (PROPOSED or ACCEPTED) without
    requiring a token — the login session provides the auth. This route allows
    participants to reach their match page from the account page even after the
    emailed token link has expired (relevant for CONFIRMED/ACCEPTED matches).

    Redirects to ``accounts:detail`` if the user has no active match, so there
    is no error page for the "no match yet" case.
    """
    user = cast(User, request.user)
    match = (
        Match.objects.active()
        .filter(
            Q(ambassador_registration__user=user) | Q(referee_registration__user=user)
        )
        .select_related(
            "ambassador_registration__user",
            "referee_registration__user",
        )
        .first()
    )
    if match is None:
        return redirect("accounts:detail")

    # Determine which registration belongs to this user.
    if match.ambassador_registration.user_id == user.pk:
        registration = match.ambassador_registration
    else:
        registration = match.referee_registration

    side = match.side_of(registration)
    # No token: the tokenless route does not provide HTMX action URLs.
    return _render_match_page(request, match, registration, side)
