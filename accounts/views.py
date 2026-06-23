# Account self-service views.
#
# The authenticated participant views and edits their own profile, manages their
# Facebook connection (via allauth), and can delete their account. Role is shown
# read-only — it is fixed once registered (CLAUDE.md).

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from matching.models import Registration

from .forms import AccountForm
from .models import Account
from .services import delete_account, update_account


@login_required
def account_detail(request: HttpRequest) -> HttpResponse:
    """Show the participant's profile, registrations and security controls."""
    user = cast(User, request.user)
    account, _created = Account.objects.get_or_create(user=user)
    registrations = Registration.objects.filter(account=account).select_related(
        "season", "price_category"
    )
    return render(
        request,
        "accounts/detail.html",
        {"account": account, "registrations": registrations},
    )


@login_required
def account_edit(request: HttpRequest) -> HttpResponse:
    """Edit the participant's name, phone and preferred language."""
    user = cast(User, request.user)
    account, _created = Account.objects.get_or_create(user=user)
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
                "phone": account.phone,
                "preferred_language": account.preferred_language,
            }
        )
    return render(request, "accounts/edit.html", {"form": form})


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
