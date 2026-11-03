# Tests for matching admin classes and actions.

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from matching.models import Match, Registration
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


def test_registration_changelist_links_to_view_as(client: Client) -> None:
    """Each Registration row carries a link that starts impersonating its user."""
    registration = RegistrationFactory.create()
    client.force_login(make_staff_user())
    response = client.get(reverse("admin:matching_registration_changelist"))
    url = reverse("admin:matching_registration_view_as", args=[registration.pk])
    assert f'href="{url}"' in response.content.decode()


def test_view_as_redirects_superuser_to_the_public_handoff(client: Client) -> None:
    """The admin "View as" view redirects to the signed public hand-off."""
    registration = RegistrationFactory.create()
    client.force_login(make_staff_user())
    url = reverse("admin:matching_registration_view_as", args=[registration.pk])
    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"].startswith("/impersonate/handoff/")


def test_view_as_is_forbidden_to_non_superuser_staff(client: Client) -> None:
    """Plain staff cannot mint a hand-off token."""
    registration = RegistrationFactory.create()
    client.force_login(UserFactory.create(is_staff=True))
    url = reverse("admin:matching_registration_view_as", args=[registration.pk])
    assert client.get(url).status_code == 403


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


# ---------------------------------------------------------------------------
# Queue position
# ---------------------------------------------------------------------------


def test_registration_changelist_shows_queue_position(client: Client) -> None:
    """A search for one name still shows that person's place in the whole queue."""
    RegistrationFactory.create(priority=1)
    subject = RegistrationFactory.create(user__first_name="Zélie")
    client.force_login(make_staff_user())

    response = client.get(
        reverse("admin:matching_registration_changelist"), {"q": "Zélie"}
    )

    assert '<td class="field-queue_position">#2</td>' in response.content.decode()
    assert response.context["cl"].result_count == 1
    assert response.context["cl"].result_list[0] == subject


def test_registration_changelist_sorts_on_queue_position(client: Client) -> None:
    """The Queue column is sortable, front of the queue first."""
    back = RegistrationFactory.create()
    front = RegistrationFactory.create(priority=5)
    client.force_login(make_staff_user())
    changelist = reverse("admin:matching_registration_changelist")
    response = client.get(changelist)
    column = response.context["cl"].list_display.index("queue_position")

    response = client.get(changelist, {"o": str(column)})

    assert list(response.context["cl"].result_list) == [front, back]


def test_registration_change_form_states_queue_position(client: Client) -> None:
    """The change form spells the position out with the queue's size."""
    subject = RegistrationFactory.create()
    client.force_login(make_staff_user())

    response = client.get(
        reverse("admin:matching_registration_change", args=[subject.pk])
    )

    assert "Number 1 of 1 in the ambassador queue." in response.content.decode()


def test_registration_change_form_links_the_blocking_match(client: Client) -> None:
    """A matched participant's form links the match that keeps them out."""
    match = MatchFactory.create(status=Match.Status.PROPOSED)
    client.force_login(make_staff_user())

    response = client.get(
        reverse(
            "admin:matching_registration_change",
            args=[match.ambassador_registration.pk],
        )
    )

    content = response.content.decode()
    assert "Not in the queue: in a proposed match." in content
    assert reverse("admin:matching_match_change", args=[match.pk]) in content


def test_registration_add_form_has_no_queue_position(client: Client) -> None:
    """The add form renders without a queue place to compute."""
    client.force_login(make_staff_user())
    response = client.get(reverse("admin:matching_registration_add"))
    assert response.status_code == 200
    assert Registration.objects.count() == 0
