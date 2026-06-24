# Account self-service form.
#
# Edits the participant's own profile: their name (on User) and contact /
# preference attributes (on Account). Role is deliberately absent — it is fixed
# once registered (CLAUDE.md); changing it means deleting and re-registering.

from __future__ import annotations

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

# Design-system class applied to text-like inputs and selects. ``.input``
# (src/css/main.css) carries the height, border, radius and role-toned focus
# ring; the focus colour follows the surrounding .role-theme.
_INPUT_CLASSES = "input"


class AccountForm(forms.Form):
    """Edit the logged-in participant's own name, phone and language."""

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
    phone = forms.CharField(
        label=_("Phone"),
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASSES}),
    )
    preferred_language = forms.ChoiceField(
        label=_("Preferred language"),
        required=False,
        choices=[("", _("No preference"))] + list(settings.LANGUAGES),
        widget=forms.Select(attrs={"class": _INPUT_CLASSES}),
    )
