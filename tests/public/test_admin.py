# Tests for public app admin classes.

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from public.admin import SurveyResponseAdmin
from public.models import SurveyResponse
from tests.accounts.factories import UserFactory
from tests.public.factories import SurveyResponseFactory

pytestmark = pytest.mark.django_db


def make_staff_user() -> User:
    """Create and return a superuser for admin access in tests."""
    user = UserFactory.create(
        username="public_staff_admin",
        is_staff=True,
        is_superuser=True,
    )
    user.set_password("password")
    user.save()
    return user


def test_survey_response_changelist_returns_200(client: Client) -> None:
    """GET the SurveyResponse changelist as a staff user returns HTTP 200."""
    SurveyResponseFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:public_surveyresponse_changelist")
    response = client.get(url)
    assert response.status_code == 200


def test_survey_response_add_permission_denied() -> None:
    """Admin add permission is denied — responses must come from a real submission."""
    admin_instance = SurveyResponseAdmin(SurveyResponse, None)
    assert admin_instance.has_add_permission(request=object()) is False


def test_survey_response_fields_are_readonly() -> None:
    """All domain fields are readonly — responses are never hand-edited."""
    readonly = SurveyResponseAdmin.readonly_fields
    for field in (
        "registration",
        "price_chf_shown",
        "framing_shown",
        "q1_answer",
        "q2_answer",
        "created_at",
        "updated_at",
    ):
        assert field in readonly
