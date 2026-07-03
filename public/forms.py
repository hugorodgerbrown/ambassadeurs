# Forms for the public app.
#
# SurveyResponseForm (VERB-111) collects the two willingness-to-pay questions
# shown on register_done. price_chf_shown / framing_shown are never form
# fields — they are re-derived server-side from the registration's pk in
# public.views.register_survey_submit and never trusted from client input.

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import SurveyResponse

# Design-system class applied to radio inputs, matching the checkbox styling
# used elsewhere (matching/forms.py _CHECKBOX_CLASSES).
_RADIO_CLASSES = "mt-1 shrink-0"


class SurveyResponseForm(forms.Form):
    """Collect the willingness-to-pay survey answers.

    ``q1_answer`` is required; ``q2_answer`` is optional (a respondent may
    skip the payment-model preference question).
    """

    q1_answer = forms.ChoiceField(
        label=_("Would a refundable deposit at this price have changed your decision?"),
        choices=SurveyResponse.Q1Answer.choices,
        required=True,
        widget=forms.RadioSelect(attrs={"class": _RADIO_CLASSES}),
    )
    q2_answer = forms.ChoiceField(
        label=_("If we did charge a deposit, how would you prefer to pay it?"),
        choices=SurveyResponse.Q2Answer.choices,
        required=False,
        widget=forms.RadioSelect(attrs={"class": _RADIO_CLASSES}),
    )
