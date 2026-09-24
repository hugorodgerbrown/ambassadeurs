# Tests for staff impersonation (django-impersonate, ADR 0028): the URL helper,
# the configured access rules, the read-only guard, the banner and the audit log.

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse
from impersonate.models import ImpersonationLog

from core.impersonation import impersonate_start_url
from tests.accounts.factories import UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def _superuser() -> User:
    """Create and return a superuser."""
    return UserFactory.create(username="boss", is_staff=True, is_superuser=True)


def _participant() -> User:
    """Create and return a user with a registration."""
    return RegistrationFactory.create().user


# ---------------------------------------------------------------------------
# impersonate_start_url
# ---------------------------------------------------------------------------


@override_settings(ADMIN_HOST="")
def test_start_url_is_relative_on_a_single_host() -> None:
    """With no admin host the link is a site-relative path."""
    assert impersonate_start_url(42) == "/impersonate/42/"


@override_settings(ADMIN_HOST="admin.example.com", BASE_URL="https://example.com")
def test_start_url_is_absolute_on_the_public_host_when_admin_is_split() -> None:
    """With a separate admin host the link points at the public site."""
    assert impersonate_start_url(42) == "https://example.com/impersonate/42/"


# ---------------------------------------------------------------------------
# Access rules
# ---------------------------------------------------------------------------


def test_superuser_sees_the_participant_account_page(client: Client) -> None:
    """Starting impersonation lands on the participant's account page."""
    participant = _participant()
    client.force_login(_superuser())

    response = client.get(impersonate_start_url(participant.pk), follow=True)

    assert response.wsgi_request.user == participant
    assert participant.email in response.content.decode()


def test_non_superuser_staff_cannot_impersonate(client: Client) -> None:
    """REQUIRE_SUPERUSER: plain staff are redirected without impersonating."""
    participant = _participant()
    client.force_login(UserFactory.create(is_staff=True))

    client.get(impersonate_start_url(participant.pk))

    assert "_impersonate" not in client.session


def test_participant_cannot_impersonate(client: Client) -> None:
    """A participant cannot impersonate another participant."""
    participant = _participant()
    client.force_login(_participant())

    client.get(impersonate_start_url(participant.pk))

    assert "_impersonate" not in client.session


def test_superuser_cannot_be_impersonated(client: Client) -> None:
    """ALLOW_SUPERUSER is off: another superuser is not a valid target."""
    target = UserFactory.create(is_staff=True, is_superuser=True)
    client.force_login(_superuser())

    client.get(impersonate_start_url(target.pk))

    assert "_impersonate" not in client.session


# ---------------------------------------------------------------------------
# Read-only guard and banner
# ---------------------------------------------------------------------------


def test_post_is_refused_while_impersonating(client: Client) -> None:
    """READ_ONLY: a form submission as the participant returns 405."""
    participant = _participant()
    client.force_login(_superuser())
    client.get(impersonate_start_url(participant.pk))

    response = client.post(reverse("accounts:logout"))

    assert response.status_code == 405


def test_banner_shows_while_impersonating(client: Client) -> None:
    """Pages rendered while impersonating carry the stop link."""
    participant = _participant()
    client.force_login(_superuser())
    client.get(impersonate_start_url(participant.pk))

    response = client.get(reverse("accounts:detail"))

    assert reverse("impersonate-stop") in response.content.decode()


def test_banner_absent_for_a_normal_session(client: Client) -> None:
    """A participant's own session has no impersonation banner."""
    client.force_login(_participant())

    response = client.get(reverse("accounts:detail"))

    assert reverse("impersonate-stop") not in response.content.decode()


# ---------------------------------------------------------------------------
# Stop and audit log
# ---------------------------------------------------------------------------


def test_stop_restores_the_staff_user_and_closes_the_log(client: Client) -> None:
    """Stopping returns to the superuser and records the session end."""
    participant = _participant()
    superuser = _superuser()
    client.force_login(superuser)
    client.get(impersonate_start_url(participant.pk))

    response = client.get(reverse("impersonate-stop"), follow=True)

    assert response.wsgi_request.user == superuser
    log = ImpersonationLog.objects.get()
    assert log.impersonator == superuser
    assert log.impersonating == participant
    assert log.session_ended_at is not None
