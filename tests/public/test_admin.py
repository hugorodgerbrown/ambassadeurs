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
    assert admin_instance.has_add_permission(request=None) is False


def test_survey_response_change_permission_denied() -> None:
    """Admin change permission is denied — even a superuser must not edit responses."""
    admin_instance = SurveyResponseAdmin(SurveyResponse, None)
    assert admin_instance.has_change_permission(request=None) is False


def test_survey_response_delete_permission_denied() -> None:
    """Admin delete permission is denied — responses are research data."""
    admin_instance = SurveyResponseAdmin(SurveyResponse, None)
    assert admin_instance.has_delete_permission(request=None) is False


def test_survey_response_change_view_is_read_only_for_superuser(client: Client) -> None:
    """A superuser's GET of the change view renders read-only (200), consistent
    with all fields being readonly_fields — Django's has_view_or_change_permission
    check allows a superuser to view even with has_change_permission() = False.

    The actual denial is enforced on POST (see the next test).
    """
    response_row = SurveyResponseFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:public_surveyresponse_change", args=[response_row.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert b'name="max_deposit"' not in response.content


def test_survey_response_change_post_forbidden_for_superuser(client: Client) -> None:
    """A superuser's POST attempting to save changes is denied (403) —
    has_change_permission() = False blocks the actual write, even though GET
    renders the read-only page."""
    response_row = SurveyResponseFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:public_surveyresponse_change", args=[response_row.pk])
    response = client.post(url, {"max_deposit": SurveyResponse.MaxDeposit.CHF_20})
    assert response.status_code == 403


def test_survey_response_add_view_forbidden_for_superuser(client: Client) -> None:
    """A superuser cannot reach the SurveyResponse add view (403)."""
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:public_surveyresponse_add")
    response = client.get(url)
    assert response.status_code == 403


def test_survey_response_fields_are_readonly() -> None:
    """All domain fields are readonly — responses are never hand-edited."""
    readonly = SurveyResponseAdmin.readonly_fields
    for field in (
        "registration",
        "max_deposit",
        "created_at",
        "updated_at",
    ):
        assert field in readonly
