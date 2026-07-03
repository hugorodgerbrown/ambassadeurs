# Tests for public app models.

import datetime

import pytest

from public.models import (
    SURVEY_PRICE_POINTS_CHF,
    FormDownload,
    FormDownloadQuerySet,
    SurveyResponse,
    SurveyResponseQuerySet,
    survey_framing_for,
    survey_price_for,
)
from tests.matching.factories import RegistrationFactory
from tests.public.factories import FormDownloadFactory, SurveyResponseFactory

pytestmark = pytest.mark.django_db


def test_form_download_to_string_format() -> None:
    """FormDownload.to_string returns the expected date-prefixed label."""
    fd = FormDownloadFactory.create()
    s = str(fd)
    assert s.startswith("Form download · ")
    # The date portion must be present and non-empty.
    assert fd.created_at.strftime("%Y-%m-%d") in s


def test_form_download_default_ordering_newest_first() -> None:
    """FormDownload rows are ordered newest-first by default.

    Use queryset .update() to assign explicit distinct timestamps, bypassing
    auto_now_add — the two .create() calls can otherwise land in the same
    microsecond under SQLite, making the ordering non-deterministic.
    """
    first = FormDownloadFactory.create()
    second = FormDownloadFactory.create()
    # Assign well-separated tz-aware timestamps so ordering is deterministic.
    FormDownload.objects.filter(pk=first.pk).update(
        created_at=datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    )
    FormDownload.objects.filter(pk=second.pk).update(
        created_at=datetime.datetime(2024, 1, 1, 13, 0, 0, tzinfo=datetime.UTC)
    )
    first.refresh_from_db()
    second.refresh_from_db()
    rows = list(FormDownload.objects.all())
    # The more recently created row (second, 13:00) must come first.
    assert rows[0] == second
    assert rows[1] == first


def test_form_download_manager_is_custom_queryset() -> None:
    """The default manager produces FormDownloadQuerySet instances."""
    assert isinstance(FormDownload.objects.all(), FormDownloadQuerySet)


# ---------------------------------------------------------------------------
# SurveyResponse (VERB-111)
# ---------------------------------------------------------------------------


def test_survey_response_to_string_format() -> None:
    """SurveyResponse.to_string includes price, framing, and q1 answer."""
    response = SurveyResponseFactory.create(
        price_chf_shown=10,
        framing_shown=SurveyResponse.Framing.FEE,
        q1_answer=SurveyResponse.Q1Answer.DEFINITELY,
    )
    s = str(response)
    assert s.startswith("Survey response · CHF 10")
    assert "Fee" in s
    assert "Definitely" in s


def test_survey_response_manager_is_custom_queryset() -> None:
    """The default manager produces SurveyResponseQuerySet instances."""
    assert isinstance(SurveyResponse.objects.all(), SurveyResponseQuerySet)


def test_survey_response_default_ordering_newest_first() -> None:
    """SurveyResponse rows are ordered newest-first by default."""
    first = SurveyResponseFactory.create()
    second = SurveyResponseFactory.create()
    SurveyResponse.objects.filter(pk=first.pk).update(
        created_at=datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    )
    SurveyResponse.objects.filter(pk=second.pk).update(
        created_at=datetime.datetime(2024, 1, 1, 13, 0, 0, tzinfo=datetime.UTC)
    )
    rows = list(SurveyResponse.objects.all())
    assert rows[0].pk == second.pk
    assert rows[1].pk == first.pk


def test_survey_response_registration_set_null_on_delete() -> None:
    """Deleting the registration preserves the SurveyResponse row (SET_NULL)."""
    registration = RegistrationFactory.create(fee_chf=0)
    response = SurveyResponseFactory.create(registration=registration)
    registration.delete()
    response.refresh_from_db()
    assert response.registration_id is None


def test_survey_price_for_is_in_range() -> None:
    """survey_price_for always returns one of the configured price points."""
    registration = RegistrationFactory.create(fee_chf=0)
    assert survey_price_for(registration) in SURVEY_PRICE_POINTS_CHF


def test_survey_price_for_is_deterministic() -> None:
    """survey_price_for returns the same value across repeated calls."""
    registration = RegistrationFactory.create(fee_chf=0)
    first = survey_price_for(registration)
    second = survey_price_for(registration)
    assert first == second


def test_survey_price_for_varies_by_pk() -> None:
    """Across a run of pks, all three configured price points are reachable."""
    registrations = [RegistrationFactory.create(fee_chf=0) for _ in range(6)]
    prices = {survey_price_for(reg) for reg in registrations}
    assert prices == set(SURVEY_PRICE_POINTS_CHF)


def test_survey_framing_for_is_deterministic() -> None:
    """survey_framing_for returns the same value across repeated calls."""
    registration = RegistrationFactory.create(fee_chf=0)
    first = survey_framing_for(registration)
    second = survey_framing_for(registration)
    assert first == second


def test_survey_framing_for_reaches_both_variants() -> None:
    """Across a run of pks, both DEPOSIT and FEE framings are reachable."""
    registrations = [RegistrationFactory.create(fee_chf=0) for _ in range(12)]
    framings = {survey_framing_for(reg) for reg in registrations}
    assert framings == {SurveyResponse.Framing.DEPOSIT, SurveyResponse.Framing.FEE}
