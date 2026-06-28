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
        status=Registration.Status.VERIFIED,
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
def test_create_counterpart_unverified_creates_no_match() -> None:
    """UNVERIFIED state creates the registration but proposes no match."""
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )

    client = _authenticated_client(user)
    response = client.post(
        reverse("debug:create_counterpart"),
        {"state": "UNVERIFIED"},
    )

    assert response.status_code == 302

    # An UNVERIFIED referee registration exists.
    unverified = Registration.objects.filter(
        role=Registration.Role.REFEREE,
        status=Registration.Status.UNVERIFIED,
    ).first()
    assert unverified is not None

    # No match created.
    assert not Match.objects.exists()


@override_settings(DEBUG=True)
def test_create_counterpart_unverified_stashes_verify_url_in_session() -> None:
    """UNVERIFIED state stashes the confirm URL in the session."""
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )

    client = _authenticated_client(user)
    client.post(
        reverse("debug:create_counterpart"),
        {"state": "UNVERIFIED"},
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
        status=Registration.Status.VERIFIED,
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
    )
    counterpart_reg = RegistrationFactory.create(role=Registration.Role.REFEREE)
    match = MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_accept"))

    assert response.status_code == 302
    match.refresh_from_db()
    # The counterpart (referee) has accepted; match is now PENDING.
    assert match.referee_accepted_at is not None
    assert match.status == Match.Status.PENDING


@override_settings(DEBUG=True)
def test_counterpart_accept_then_user_accept_reaches_accepted() -> None:
    """Both sides accepting transitions the match to ACCEPTED."""
    from matching.services import accept_match

    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
    )
    counterpart_reg = RegistrationFactory.create(role=Registration.Role.REFEREE)
    match = MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    # Counterpart accepts via the debug view — match goes PROPOSED → PENDING.
    client.post(reverse("debug:counterpart_accept"))
    match.refresh_from_db()
    assert match.status == Match.Status.PENDING

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
    my_reg = RegistrationFactory.create(user=user, role=Registration.Role.AMBASSADOR)
    counterpart_reg = RegistrationFactory.create(role=Registration.Role.REFEREE)
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
def test_counterpart_decline_deletes_counterpart_and_requeues_user() -> None:
    """counterpart_decline deletes the counterpart and re-queues the user.

    The counterpart (decliner) has their User and Registration deleted; the
    matched user (kept-faith party) is re-queued to the front of the pool.
    """
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        priority=0,
    )
    counterpart_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        priority=0,
    )
    counterpart_user_pk = counterpart_reg.user.pk
    MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    client.post(reverse("debug:counterpart_decline"))

    from django.contrib.auth.models import User as DjangoUser

    # The counterpart (decliner) is deleted.
    assert not DjangoUser.objects.filter(pk=counterpart_user_pk).exists()
    assert not Registration.objects.filter(pk=counterpart_reg.pk).exists()

    # The kept-faith user is re-queued to the front (priority +1).
    my_reg.refresh_from_db()
    assert my_reg.status == Registration.Status.VERIFIED
    assert my_reg.priority == 1


# ---------------------------------------------------------------------------
# counterpart_login
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_counterpart_login_switches_session_user() -> None:
    """counterpart_login logs out the current user and logs in as the counterpart."""
    user = UserFactory.create()
    my_reg = RegistrationFactory.create(user=user, role=Registration.Role.AMBASSADOR)
    counterpart_reg = RegistrationFactory.create(role=Registration.Role.REFEREE)
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
    my_reg = RegistrationFactory.create(user=user, role=Registration.Role.AMBASSADOR)
    counterpart_reg = RegistrationFactory.create(role=Registration.Role.REFEREE)
    MatchFactory.create(
        ambassador_registration=my_reg,
        referee_registration=counterpart_reg,
        status=Match.Status.PROPOSED,
    )

    client = _authenticated_client(user)
    response = client.post(reverse("debug:counterpart_login"))

    assert response["Location"] == reverse("accounts:match")


# ---------------------------------------------------------------------------
# match_preview (forced-state visual QA of the match page)
# ---------------------------------------------------------------------------

_PREVIEW_VIEW_KEYS = [
    "proposed",
    "you_accepted",
    "partner_accepted",
    "confirmed",
    "declined_you",
    "declined_partner",
    "expired",
    "abandoned_you",
    "abandoned_partner",
]


@override_settings(DEBUG=False)
def test_match_preview_404_when_not_debug() -> None:
    """match_preview returns 404 in production (require_debug guard)."""
    response = Client().get(reverse("debug:match_preview"))
    assert response.status_code == 404


@override_settings(DEBUG=True)
@pytest.mark.parametrize("view_key", _PREVIEW_VIEW_KEYS)
def test_match_preview_renders_each_state(view_key: str) -> None:
    """Every forced state renders the real match page with the derived view."""
    response = Client().get(reverse("debug:match_preview"), {"view": view_key})
    assert response.status_code == 200
    assert "public/match.html" in [t.name for t in response.templates]
    assert response.context["view"] == view_key


@override_settings(DEBUG=True)
def test_match_preview_unknown_view_defaults_to_proposed() -> None:
    """An unknown ?view value falls back to the proposed state."""
    response = Client().get(reverse("debug:match_preview"), {"view": "nope"})
    assert response.status_code == 200
    assert response.context["view"] == "proposed"


@override_settings(DEBUG=True)
def test_match_preview_renders_state_switcher() -> None:
    """The preview page shows the debug-only state switcher."""
    response = Client().get(reverse("debug:match_preview"))
    assert b"Preview state" in response.content


@override_settings(DEBUG=True)
def test_match_preview_reveals_contact_only_when_confirmed() -> None:
    """Email/phone appear in the confirmed contact card but not in proposed.

    Guards against the preview leaking PII before mutual accept — the same
    Invariant 1 boundary the real page enforces. The partner's first name is
    shown in both (the redesign reveals it early; see ADR 0009)."""
    confirmed = (
        Client()
        .get(reverse("debug:match_preview"), {"view": "confirmed"})
        .content.decode()
    )
    assert "lea.maret@example.com" in confirmed
    assert "+41 79 482 16 03" in confirmed

    proposed = (
        Client()
        .get(reverse("debug:match_preview"), {"view": "proposed"})
        .content.decode()
    )
    assert "lea.maret@example.com" not in proposed
    assert "+41 79 482 16 03" not in proposed
    assert "Léa" in proposed


# ---------------------------------------------------------------------------
# components (account Match status panel gallery)
# ---------------------------------------------------------------------------


@override_settings(DEBUG=False)
def test_components_404_when_not_debug() -> None:
    """The component gallery returns 404 in production (require_debug guard)."""
    response = Client().get(reverse("debug:components"))
    assert response.status_code == 404


@override_settings(DEBUG=True)
def test_components_renders_every_pill_combination() -> None:
    """The gallery renders each status pill tone and label, plus the partner name."""
    content = Client().get(reverse("debug:components")).content.decode()
    # One pill per scenario, tone-coded.
    for label in (
        "No match",
        "Email unconfirmed",
        "In the queue",
        "Match pending",
        "Match confirmed",
        "Withdrawn",
        "Suspended",
    ):
        assert label in content
    for tone in ("tag-status--muted", "tag-status--wait", "tag-status--done"):
        assert tone in content
    # Proposed / pending / accepted match scenarios name the partner.
    assert "Bernard" in content
    # Both VERIFIED variants are present (with and without a queue position).
    assert "You are in the queue" in content
    assert "You are number 3 in the queue" in content
