# Registration form shared by the ambassador and referee flows.
#
# The form is role-parameterised and bound to the active season: the price
# category choices come from that season, and the attestation checkbox carries
# the role-appropriate prior-season statement. Account/User/Registration
# creation happens in services.register_participant, never in the form.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import PriceCategory, Registration, Resort, Season

# Tailwind utility classes applied to text-like inputs and selects.
_INPUT_CLASSES = (
    "mt-1 block w-full rounded-md border border-border bg-card px-3 py-2 "
    "text-text-1 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
)


class PriceCategoryChoiceField(forms.ModelChoiceField):
    """Model choice field that labels a price category with its discounted price."""

    def label_from_instance(self, obj: PriceCategory) -> str:
        """Return ``Label — CHF 999`` for the dropdown option."""
        return f"{obj.label} — CHF {obj.discounted_price:.0f}"


class RegistrationForm(forms.Form):
    """Collect the participant details needed to enrol them in a season's pool.

    Built with ``role`` (Registration.Role) and the active ``season``; the view
    passes both. ``attestation`` is the mandatory prior-season confirmation whose
    wording is set per role in the template.
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
    price_category = PriceCategoryChoiceField(
        label=_("Ticket type"),
        queryset=PriceCategory.objects.none(),
        empty_label=None,
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
        season: Season,
        data: Mapping[str, Any] | None = None,
        user: User | None = None,
    ) -> None:
        """Bind the form to ``role`` and the active ``season``.

        When ``user`` is given (e.g. after a Facebook login) the email field is
        dropped and the name is prefilled — the participant is already
        identified, so we only collect the role-specific fields.
        """
        self.role = role
        self.season = season
        self.user = user
        super().__init__(data=data)
        category_field = cast(PriceCategoryChoiceField, self.fields["price_category"])
        category_field.queryset = PriceCategory.objects.for_season(season).order_by(
            "order"
        )
        if user is not None:
            del self.fields["email"]
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name

    def clean_email(self) -> str:
        """Normalise the email to lowercase (CLAUDE.md invariant 5)."""
        email: str = self.cleaned_data["email"]
        return email.lower()

    def clean(self) -> dict[str, Any]:
        """Reject a second registration by the same participant in this season."""
        cleaned = super().clean() or {}
        in_season = Registration.objects.for_season(self.season)
        already_registered = False
        if self.user is not None:
            already_registered = in_season.filter(account__user=self.user).exists()
        else:
            email = cleaned.get("email")
            already_registered = (
                bool(email) and in_season.filter(account__user__email=email).exists()
            )
        if already_registered:
            raise forms.ValidationError(
                _("You are already registered for the current season.")
            )
        return cleaned
