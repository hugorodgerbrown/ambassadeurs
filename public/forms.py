# Forms for the public app.
#
# SurveyResponseForm (VERB-111) collects the single willingness-to-pay
# question shown on register_done: the highest refundable deposit the
# respondent would have been happy to pay to register.

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import SurveyResponse

# Design-system class applied to radio inputs, matching the checkbox styling
# used elsewhere (matching/forms.py _CHECKBOX_CLASSES).
_RADIO_CLASSES = "mt-1 shrink-0"


class SurveyResponseForm(forms.Form):
    """Collect the willingness-to-pay survey answer.

    ``max_deposit`` is the single required question.
    """

    max_deposit = forms.ChoiceField(
        label=_(
            "What is the highest deposit you would have been happy to pay to register?"
        ),
        choices=SurveyResponse.MaxDeposit.choices,
        required=True,
        widget=forms.RadioSelect(attrs={"class": _RADIO_CLASSES}),
    )
