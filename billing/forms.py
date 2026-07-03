# Form for the standalone tip (voluntary contribution) page, VERB-110.
#
# The template renders the preset amount options (CHF 5 / 10 / 20 plus a
# free-amount input); this form stays a plain amount+message validator so it
# is agnostic to how the amount was chosen in the UI.

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

# Design-system classes, mirroring matching/forms.py's conventions.
_INPUT_CLASSES = "input"
_TEXTAREA_CLASSES = "input"


class TipForm(forms.Form):
    """Validate a voluntary-contribution amount and optional message.

    ``amount_chf`` is bounded 1-500 (whole CHF); ``message`` is an optional
    "say something nice" note, capped at 280 characters and shown to staff
    only (never to the tipper's counterpart).
    """

    amount_chf = forms.IntegerField(
        label=_("Amount (CHF)"),
        min_value=1,
        max_value=500,
        widget=forms.NumberInput(attrs={"class": _INPUT_CLASSES}),
    )
    message = forms.CharField(
        label=_("Say something nice (optional)"),
        max_length=280,
        required=False,
        widget=forms.Textarea(attrs={"class": _TEXTAREA_CLASSES, "rows": 3}),
    )
