# Registration form for the combined single-step flow (VERB-24).
#
# The form is role-parameterised. prior_pass is role-aware: ambassadors choose
# from SEASONAL / ANNUAL / MONT4; referees have no select and it resolves to
# NONE. Account/User/Registration creation happens in services.register_participant,
# never in the form.
#
# RegistrationEmailForm has been removed (was VERB-9 step-2 capture); the
# email field is now part of RegistrationForm itself.

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django_countries import countries

from core.emails import normalise_email

from .models import Registration, Resort

# Design-system class applied to text-like inputs. ``.input`` (src/css/main.css)
# carries the height, border, radius and role-toned focus ring; the focus colour
# follows the surrounding .role-theme.
_INPUT_CLASSES = "input"

# Design-system class applied to native ``<select>`` widgets. ``.select`` matches
# the ``.input`` box but bakes in a token-stroked chevron via ``appearance:none``
# (so the platform arrow is replaced by one that matches the design) and dims
# that chevron when the control is disabled.
_SELECT_CLASSES = "select"

# Classes applied to the confirmation checkboxes. ``mt-1`` nudges the 13px box
# down so its centre lines up with the first line of the (line-height: 20px)
# label text under the ``items-start`` flex label; ``shrink-0`` stops the box
# being squashed when the label wraps to a second line.
_CHECKBOX_CLASSES = "mt-1 shrink-0"

# prior_pass choices available to ambassadors (must hold one of these).
_AMBASSADOR_PRIOR_PASS_CHOICES = [
    (Registration.PriorPass.SEASONAL, Registration.PriorPass.SEASONAL.label),
    (Registration.PriorPass.ANNUAL, Registration.PriorPass.ANNUAL.label),
    (Registration.PriorPass.MONT4, Registration.PriorPass.MONT4.label),
]


class RegistrationForm(forms.Form):
    """Collect the participant details needed to enrol them in the pool.

    Built with ``role`` (Registration.Role). Two required checkboxes are
    rendered at submission:

    - ``prior_pass_attestation``: a role-specific eligibility declaration
      (ambassador or referee wording, set in ``__init__``).
    - ``terms_accepted``: acceptance of the Terms of Use.

    For ambassadors, a ``prior_pass`` select is rendered (SEASONAL / ANNUAL /
    MONT4). For referees the field is hidden and resolves to NONE in ``clean``.

    ``accepted_statements()`` returns the ordered list of resolved consent
    statement texts, ready to be persisted on ``Registration.accepted_terms``.
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
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )
    preferred_language = forms.ChoiceField(
        label=_("Preferred language"),
        required=False,
        choices=[("", _("No preference"))] + list(settings.LANGUAGES),
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )
    nationality = forms.ChoiceField(
        label=_("Nationality"),
        required=False,
        # choices are set per-instance in __init__ (see below).
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )
    prior_pass = forms.ChoiceField(
        label=_("Prior-season pass type"),
        choices=_AMBASSADOR_PRIOR_PASS_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": _SELECT_CLASSES}),
    )
    prior_pass_attestation = forms.BooleanField(
        # Label is role-specific; set in __init__.
        required=True,
        widget=forms.CheckboxInput(attrs={"class": _CHECKBOX_CLASSES}),
    )
    terms_accepted = forms.BooleanField(
        # Label displayed in the template via {% blocktranslate %} with an inline link.
        label=_("I have read and agree to the Terms of Use"),
        required=True,
        widget=forms.CheckboxInput(attrs={"class": _CHECKBOX_CLASSES}),
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

        # Populate nationality choices per instance so django-countries resolves
        # country names under the active request locale (not the import-time
        # default). list(countries) calls countries.__iter__, which translates
        # each name to the current language — freezing it in the class body
        # would lock names to English for all users.
        cast(forms.ChoiceField, self.fields["nationality"]).choices = [
            ("", _("Prefer not to say")),
            *list(countries),
        ]

        # Set the role-specific eligibility declaration label.
        if role == Registration.Role.AMBASSADOR:
            self.fields["prior_pass_attestation"].label = _(
                "I purchased a season or annual pass in 2024/25 or 2025/26."
            )
        else:
            self.fields["prior_pass_attestation"].label = _(
                "I did not purchase a season or annual pass in 2024/25 or 2025/26."
            )

        if role != Registration.Role.AMBASSADOR:
            # Referees do not choose a prior pass; it resolves to NONE in clean.
            del self.fields["prior_pass"]

        if user is not None:
            del self.fields["email"]
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name

    def accepted_statements(self) -> list[str]:
        """Return the ordered list of consent statement texts the participant accepted.

        Resolves lazy translation strings to plain ``str`` under the active language.
        The eligibility declaration comes first; the T&C acceptance comes second.
        The labels are set in ``__init__`` and do not depend on ``cleaned_data``,
        so this is safe to call at any point after construction.
        """
        eligibility_label = str(self.fields["prior_pass_attestation"].label)
        terms_label = str(self.fields["terms_accepted"].label)
        return [eligibility_label, terms_label]

    def clean_email(self) -> str:
        """Normalise the email address (CLAUDE.md invariant 5)."""
        email: str = self.cleaned_data["email"]
        return normalise_email(email)

    def clean(self) -> dict[str, Any]:
        """Validate the form.

        1. Resolve prior_pass for referees to NONE.
        2. Reject a second registration by the same participant.
        """
        cleaned = super().clean() or {}

        # For referees, prior_pass is always NONE (they have no prior pass field).
        if self.role != Registration.Role.AMBASSADOR:
            cleaned["prior_pass"] = Registration.PriorPass.NONE

        # Duplicate-registration guard: one non-UNVERIFIED registration per user.
        # UNVERIFIED rows are excluded so that a re-submit for the same email is
        # handled by the view (resend the confirmation link) rather than
        # surfacing a validation error here.
        already_registered = False
        if self.user is not None:
            already_registered = (
                Registration.objects.filter(user=self.user)
                .exclude(status=Registration.Status.UNVERIFIED)
                .exists()
            )
        else:
            email = cleaned.get("email")
            already_registered = bool(email) and (
                Registration.objects.filter(user__email=email)
                .exclude(status=Registration.Status.UNVERIFIED)
                .exists()
            )
        if already_registered:
            raise forms.ValidationError(
                _("You are already registered for the current season.")
            )

        return cleaned
