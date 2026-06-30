# Tests for matching admin classes and actions.

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_staff_user() -> User:
    """Create and return a superuser for admin access in tests."""
    user = UserFactory.create(
        username="staff_admin",
        is_staff=True,
        is_superuser=True,
    )
    user.set_password("password")
    user.save()
    return user


# ---------------------------------------------------------------------------
# Changelist smoke tests
# ---------------------------------------------------------------------------


def test_registration_changelist_returns_200(client: Client) -> None:
    """GET the Registration changelist as a staff user returns HTTP 200."""
    RegistrationFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:matching_registration_changelist")
    response = client.get(url)
    assert response.status_code == 200


def test_match_changelist_returns_200(client: Client) -> None:
    """GET the Match changelist as a staff user returns HTTP 200."""
    MatchFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:matching_match_changelist")
    response = client.get(url)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# export_abandoned_as_csv action
# ---------------------------------------------------------------------------


def _post_action(client: Client, pks: list[int]) -> HttpResponse:
    """POST the export_cancelled_as_csv action for the given Match PKs."""
    url = reverse("admin:matching_match_changelist")
    return client.post(
        url,
        {
            "action": "export_cancelled_as_csv",
            "_selected_action": [str(pk) for pk in pks],
        },
    )


def test_csv_export_returns_200_and_correct_content_type(client: Client) -> None:
    """The export action returns 200 with a text/csv content type."""
    match = MatchFactory.create(cancelled=True)
    staff = make_staff_user()
    client.force_login(staff)
    response = _post_action(client, [match.pk])
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")


def test_csv_export_header_row_present(client: Client) -> None:
    """The CSV always contains the expected header row."""
    match = MatchFactory.create(cancelled=True)
    staff = make_staff_user()
    client.force_login(staff)
    response = _post_action(client, [match.pk])
    content = response.content.decode()
    assert "match_id" in content
    assert "ambassador_email" in content
    assert "referee_email" in content


def test_csv_export_contains_cancelled_match(client: Client) -> None:
    """The CSV body includes a row for each CANCELLED match selected."""
    match = MatchFactory.create(cancelled=True)
    staff = make_staff_user()
    client.force_login(staff)
    response = _post_action(client, [match.pk])
    content = response.content.decode()
    # Primary key and both emails must appear in the CSV body.
    assert str(match.pk) in content
    assert match.ambassador_registration.user.email in content
    assert match.referee_registration.user.email in content


def test_csv_export_excludes_non_cancelled_matches(client: Client) -> None:
    """The CSV body must not include matches that are not CANCELLED."""
    cancelled = MatchFactory.create(cancelled=True)
    proposed = MatchFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    response = _post_action(client, [cancelled.pk, proposed.pk])
    content = response.content.decode()
    # The proposed match's ambassador email must not appear.
    proposed_email = proposed.ambassador_registration.user.email
    cancelled_email = cancelled.ambassador_registration.user.email
    assert cancelled_email in content
    # Factories create distinct users, so these emails differ.
    assert proposed_email != cancelled_email
    assert proposed_email not in content


def test_csv_export_empty_when_no_cancelled_selected(client: Client) -> None:
    """Selecting only non-CANCELLED matches produces a header-only CSV."""
    match = MatchFactory.create()  # PROPOSED by default
    staff = make_staff_user()
    client.force_login(staff)
    response = _post_action(client, [match.pk])
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    lines = [line for line in response.content.decode().splitlines() if line.strip()]
    # Only the header row; no data rows.
    assert len(lines) == 1
    assert "match_id" in lines[0]
