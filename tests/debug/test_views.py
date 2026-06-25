# Tests for debug app views.
#
# Covers: create_counterpart (WAITING/PENDING), counterpart_accept,
# counterpart_decline, counterpart_login, and the require_debug guard
# (DEBUG=False → 404 on every endpoint).

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from matching.models import Match, Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _authenticated_client(user: object) -> Client:
    """Return a Client logged in as ``user``."""
    client = Client()
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# require_debug guard
# ---------------------------------------------------------------------------


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_create_counterpart_404_when_not_debug() -> None:
    """POST to create_counterpart returns 404 in production."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user)
    client = _authenticated_client(user)
    response = client.post(reverse("debug:create_counterpart"), {"state": "WAITING"})
    assert response.status_code == 404


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_counterpart_accept_404_when_not_debug() -> None:
    """POST to counterpart_accept returns 404 in production."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user)
    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_accept"))
    assert response.status_code == 404


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_counterpart_decline_404_when_not_debug() -> None:
    """POST to counterpart_decline returns 404 in production."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user)
    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_decline"))
    assert response.status_code == 404


@override_settings(DEBUG=False)
@pytest.mark.django_db
def test_counterpart_login_404_when_not_debug() -> None:
    """POST to counterpart_login returns 404 in production."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user)
    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_login"))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# create_counterpart
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_create_counterpart_waiting_creates_registration_and_match() -> None:
    """WAITING state creates the opposite-role registration and proposes a match."""
    # Create an ambassador waiting in the pool.
    user = UserFactory.create()
    ambassador_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )

    client = _authenticated_client(user)
    response = client.post(
        reverse("debug:create_counterpart"),
        {"state": "WAITING"},
    )

    assert response.status_code == 302

    # A referee registration must have been created.
    assert Registration.objects.filter(role=Registration.Role.REFEREE).exists()

    # A PROPOSED match must link them.
    match = Match.objects.filter(
        ambassador_registration=ambassador_reg,
        status=Match.Status.PROPOSED,
    ).first()
    assert match is not None
    assert match.referee_registration.role == Registration.Role.REFEREE


@override_settings(DEBUG=True)
def test_create_counterpart_pending_creates_no_match() -> None:
    """PENDING state creates the registration but proposes no match."""
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )

    client = _authenticated_client(user)
    response = client.post(
        reverse("debug:create_counterpart"),
        {"state": "PENDING"},
    )

    assert response.status_code == 302

    # A PENDING referee registration exists.
    pending = Registration.objects.filter(
        role=Registration.Role.REFEREE,
        status=Registration.Status.PENDING,
    ).first()
    assert pending is not None

    # No match created.
    assert not Match.objects.exists()


@override_settings(DEBUG=True)
def test_create_counterpart_pending_stashes_verify_url_in_session() -> None:
    """PENDING state stashes the confirm URL in the session."""
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )

    client = _authenticated_client(user)
    client.post(
        reverse("debug:create_counterpart"),
        {"state": "PENDING"},
    )

    assert "debug_verify_url" in client.session


@override_settings(DEBUG=True)
def test_create_counterpart_referee_creates_ambassador_counterpart() -> None:
    """A referee user gets an ambassador counterpart created."""
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.WAITING,
    )

    client = _authenticated_client(user)
    client.post(
        reverse("debug:create_counterpart"),
        {"state": "WAITING"},
    )

    assert Registration.objects.filter(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    ).exists()


# ---------------------------------------------------------------------------
# counterpart_accept
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_counterpart_accept_sets_counterpart_accepted_at() -> None:
    """counterpart_accept records the counterpart's acceptance timestamp."""
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
    )
    match = MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_accept"))

    assert response.status_code == 302
    match.refresh_from_db()
    # The counterpart (referee) has accepted; ambassador has not yet.
    assert match.referee_accepted_at is not None


@override_settings(DEBUG=True)
def test_counterpart_accept_then_user_accept_reaches_accepted() -> None:
    """Both sides accepting transitions the match to ACCEPTED."""
    from matching.services import accept_match

    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
    )
    match = MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    # Counterpart accepts via the debug view.
    client.post(reverse("debug:counterpart_accept"))
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED  # still proposed; one-sided

    # Now the user (ambassador) accepts directly via the service.
    accept_match(match, my_reg)
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED


# ---------------------------------------------------------------------------
# counterpart_decline
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_counterpart_decline_transitions_match_to_declined() -> None:
    """counterpart_decline transitions the match to DECLINED."""
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
    )
    match = MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_decline"))

    assert response.status_code == 302
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED


@override_settings(DEBUG=True)
def test_counterpart_decline_requeues_registrations() -> None:
    """counterpart_decline re-queues both registrations with asymmetric priority."""
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
        priority=0,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
        priority=0,
    )
    MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    client.post(reverse("debug:counterpart_decline"))

    my_reg.refresh_from_db()
    counterpart_reg.refresh_from_db()

    # The counterpart (decliner) goes to the back; the user goes to the front.
    assert counterpart_reg.priority < my_reg.priority


# ---------------------------------------------------------------------------
# counterpart_login
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_counterpart_login_switches_session_user() -> None:
    """counterpart_login logs out the current user and logs in as the counterpart."""
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
    )
    MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_login"))

    assert response.status_code == 302
    # The session now belongs to the counterpart user.
    from django.contrib.auth import SESSION_KEY

    assert int(client.session[SESSION_KEY]) == counterpart_reg.user.pk


@override_settings(DEBUG=True)
def test_counterpart_login_redirects_to_match_when_counterpart_has_match() -> None:
    """counterpart_login redirects to accounts:match when counterpart has a match."""
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
    )
    MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_login"))

    assert response["Location"] == reverse("accounts:match")
