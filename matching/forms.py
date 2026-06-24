# Registration form shared by the ambassador and referee flows.
#
# The form is role-parameterised. prior_pass is role-aware: ambassadors choose
# from SEASONAL / ANNUAL / MONT4; referees have no select and it resolves to
# NONE. Account/User/Registration creation happens in services.register_participant,
# never in the form.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import Registration, Resort

# Tailwind utility classes applied to text-like inputs and selects.
_INPUT_CLASSES = (
    "mt-1 block w-full rounded-md border border-line bg-surface px-3 py-2 "
    "text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
)

# prior_pass choices available to ambassadors (must hold one of these).
_AMBASSADOR_PRIOR_PASS_CHOICES = [
    (Registration.PriorPass.SEASONAL, Registration.PriorPass.SEASONAL.label),
    (Registration.PriorPass.ANNUAL, Registration.PriorPass.ANNUAL.label),
    (Registration.PriorPass.MONT4, Registration.PriorPass.MONT4.label),
]


class RegistrationEmailForm(forms.Form):
    """Capture the email to verify before registration begins (VERB-9 step 2)."""

    email = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(
            attrs={"class": _INPUT_CLASSES, "autocomplete": "email"}
        ),
    )

    def clean_email(self) -> str:
        """Normalise the email to lowercase (CLAUDE.md invariant 5)."""
        email: str = self.cleaned_data["email"]
        return email.lower()


class RegistrationForm(forms.Form):
    """Collect the participant details needed to enrol them in the pool.

    Built with ``role`` (Registration.Role). ``attestation`` is the mandatory
    prior-season confirmation whose wording is set per role in the template.

    For ambassadors, a ``prior_pass`` select is rendered (SEASONAL / ANNUAL /
    MONT4). For referees the field is hidden and resolves to NONE in ``clean``.
    """

    first_name = forms.CharField(
        label=_("First name"),
        max_length=150,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASSES}),
    )
    last_name = forms.CharField(
        label=_("Last name"),
        max_length=150,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASSES}),
    )
    email = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"class": _INPUT_CLASSES}),
    )
    phone = forms.CharField(
        label=_("Phone"),
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASSES}),
    )
    preferred_location = forms.ChoiceField(
        label=_("Preferred resort / ticket office"),
        required=False,
        choices=[("", _("No preference"))] + list(Resort.choices),
        widget=forms.Select(attrs={"class": _INPUT_CLASSES}),
    )
    preferred_language = forms.ChoiceField(
        label=_("Preferred language"),
        required=False,
        choices=[("", _("No preference"))] + list(settings.LANGUAGES),
        widget=forms.Select(attrs={"class": _INPUT_CLASSES}),
    )
    prior_pass = forms.ChoiceField(
        label=_("Prior-season pass type"),
        choices=_AMBASSADOR_PRIOR_PASS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLASSES}),
    )
    attestation = forms.BooleanField(
        label=_("I confirm the statement above is true"),
        required=True,
    )

    def __init__(
        self,
        *,
        role: str,
        data: Mapping[str, Any] | None = None,
        user: User | None = None,
    ) -> None:
        """Bind the form to ``role``.

        When ``user`` is given (e.g. after a Facebook login) the email field is
        dropped and the name is prefilled — the participant is already
        identified, so we only collect the role-specific fields.

        Referees do not see the prior_pass select — their value is always NONE.
        """
        self.role = role
        self.user = user
        super().__init__(data=data)

        if role != Registration.Role.AMBASSADOR:
            # Referees do not choose a prior pass; it resolves to NONE in clean.
            del self.fields["prior_pass"]

        if user is not None:
            del self.fields["email"]
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name

    def clean_email(self) -> str:
        """Normalise the email to lowercase (CLAUDE.md invariant 5)."""
        email: str = self.cleaned_data["email"]
        return email.lower()

    def clean(self) -> dict[str, Any]:
        """Validate the form.

        1. Resolve prior_pass for referees to NONE.
        2. Reject a second registration by the same participant.
        """
        cleaned = super().clean() or {}

        # For referees, prior_pass is always NONE (they have no prior pass field).
        if self.role != Registration.Role.AMBASSADOR:
            cleaned["prior_pass"] = Registration.PriorPass.NONE

        # Duplicate-registration guard: one registration per user.
        already_registered = False
        if self.user is not None:
            already_registered = Registration.objects.filter(user=self.user).exists()
        else:
            email = cleaned.get("email")
            already_registered = (
                bool(email) and Registration.objects.filter(user__email=email).exists()
            )
        if already_registered:
            raise forms.ValidationError(
                _("You are already registered for the current season.")
            )

        return cleaned
