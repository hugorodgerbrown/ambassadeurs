# Test factories for the public app.

import factory

from public.models import FormDownload, SurveyResponse
from tests.matching.factories import RegistrationFactory


class FormDownloadFactory(factory.django.DjangoModelFactory[FormDownload]):
    """Factory for FormDownload.

    No domain fields beyond BaseModel timestamps — call .create() with no
    arguments to record a download row.
    """

    class Meta:
        model = FormDownload


class SurveyResponseFactory(factory.django.DjangoModelFactory[SurveyResponse]):
    """Factory for SurveyResponse (VERB-111).

    Defaults to a DEPOSIT-framed CHF 5 response with q2 left blank (the
    optional payment-model preference question skipped).
    """

    class Meta:
        model = SurveyResponse

    registration = factory.SubFactory(RegistrationFactory, fee_chf=0)
    price_chf_shown = 5
    framing_shown = SurveyResponse.Framing.DEPOSIT
    q1_answer = SurveyResponse.Q1Answer.PROBABLY
    q2_answer = ""
