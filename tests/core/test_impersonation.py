# Tests for staff impersonation (django-impersonate, ADR 0028): the URL helpers,
# the signed admin -> public hand-off, the configured access rules, the
# read-only guard, the banner and the audit log.

from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from impersonate.models import ImpersonationLog

from core.impersonation import (
    HANDOFF_MAX_AGE,
    impersonate_handoff_url,
    impersonate_start_url,
    make_handoff_token,
    read_handoff_token,
)
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
# Hand-off token
# ---------------------------------------------------------------------------


def test_handoff_token_round_trips() -> None:
    """A fresh token yields the superuser and the target pk."""
    superuser = _superuser()
    assert read_handoff_token(make_handoff_token(superuser, 42)) == (superuser, 42)


def test_handoff_token_rejects_garbage() -> None:
    """A tampered or malformed token is refused."""
    assert read_handoff_token("not-a-token") is None


def test_handoff_token_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token older than HANDOFF_MAX_AGE is refused."""
    superuser = _superuser()
    token = make_handoff_token(superuser, 42)
    later = timezone.now() + timedelta(seconds=HANDOFF_MAX_AGE + 5)
    monkeypatch.setattr("time.time", later.timestamp)
    assert read_handoff_token(token) is None


def test_handoff_token_is_spent_once_the_superuser_logs_in() -> None:
    """Binding to last_login makes the token single-use."""
    superuser = _superuser()
    token = make_handoff_token(superuser, 42)
    superuser.last_login = timezone.now()
    superuser.save()
    assert read_handoff_token(token) is None


def test_handoff_token_refused_for_a_demoted_superuser() -> None:
    """A token minted by someone no longer a superuser is refused."""
    superuser = _superuser()
    token = make_handoff_token(superuser, 42)
    superuser.is_superuser = False
    superuser.save()
    assert read_handoff_token(token) is None


@override_settings(ADMIN_HOST="admin.example.com", BASE_URL="https://example.com")
def test_handoff_url_is_absolute_on_the_public_host_when_admin_is_split() -> None:
    """With a separate admin host the hand-off points at the public site."""
    url = impersonate_handoff_url(_superuser(), 42)
    assert url.startswith("https://example.com/impersonate/handoff/")


# ---------------------------------------------------------------------------
# Hand-off view
# ---------------------------------------------------------------------------


def test_handoff_signs_in_and_impersonates_without_a_public_session(
    client: Client,
) -> None:
    """Regression: "View as" no longer lands a signed-out browser on login."""
    participant = _participant()
    url = impersonate_handoff_url(_superuser(), participant.pk)

    response = client.get(url, follow=True)

    assert response.wsgi_request.user == participant
    assert participant.email in response.content.decode()
    assert ImpersonationLog.objects.get().impersonating == participant


def test_handoff_token_cannot_be_replayed(client: Client) -> None:
    """A redeemed token is refused on a second use."""
    url = impersonate_handoff_url(_superuser(), _participant().pk)
    client.get(url)

    response = Client().get(url)

    assert response.status_code == 400


def test_handoff_rejects_an_invalid_token(client: Client) -> None:
    """An invalid token renders a 400 and signs nobody in."""
    response = client.get(reverse("impersonate-handoff", args=["bogus"]))

    assert response.status_code == 400
    assert not response.wsgi_request.user.is_authenticated


def test_handoff_switches_from_a_running_impersonation(client: Client) -> None:
    """Viewing as B while viewing as A ends A's session and starts B's."""
    superuser = _superuser()
    first, second = _participant(), _participant()
    client.get(impersonate_handoff_url(superuser, first.pk), follow=True)
    superuser.refresh_from_db()

    response = client.get(impersonate_handoff_url(superuser, second.pk), follow=True)

    assert response.wsgi_request.user == second
    first_log = ImpersonationLog.objects.get(impersonating=first)
    assert first_log.session_ended_at is not None


@override_settings(
    ADMIN_HOST="admin.example.test",
    BASE_URL="http://public.example.test",
    ALLOWED_HOSTS=["admin.example.test", "public.example.test"],
)
def test_view_as_from_the_admin_host_reaches_the_participant_page() -> None:
    """End to end on a split host: admin click -> public participant page."""
    participant = _participant()
    admin_client = Client(HTTP_HOST="admin.example.test")
    admin_client.force_login(_superuser())
    registration = participant.registration

    response = admin_client.get(
        reverse(
            "admin:matching_registration_view_as",
            args=[registration.pk],
            urlconf="config.urls_admin",
        )
    )
    location = urlsplit(response["Location"])
    assert location.netloc == "public.example.test"

    public_client = Client(HTTP_HOST="public.example.test")
    response = public_client.get(location.path, follow=True)

    assert response.wsgi_request.user == participant


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
