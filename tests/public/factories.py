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

    Defaults to a CHF 10 max_deposit response.
    """

    class Meta:
        model = SurveyResponse

    registration = factory.SubFactory(RegistrationFactory, fee_chf=0)
    max_deposit = SurveyResponse.MaxDeposit.CHF_10
