# Tests for the debug context processor.
#
# Verifies that debug_panel() returns {} when DEBUG is False, and populates
# debug_registration / debug_match / debug_counterpart correctly when DEBUG
# is True.

import pytest
from django.test import RequestFactory, override_settings

from debug.context_processors import debug_panel
from matching.models import Match, Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


@override_settings(DEBUG=False)
def test_debug_panel_returns_empty_dict_in_production() -> None:
    """In production (DEBUG=False) the processor returns an empty dict."""
    request = RequestFactory().get("/")
    request.user = UserFactory.create()
    result = debug_panel(request)
    assert result == {}


@override_settings(DEBUG=True)
def test_debug_panel_returns_none_values_for_anonymous_user() -> None:
    """For an anonymous user all debug context values are None."""
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = debug_panel(request)
    assert result["debug_registration"] is None
    assert result["debug_match"] is None
    assert result["debug_counterpart"] is None


@override_settings(DEBUG=True)
def test_debug_panel_returns_none_values_for_user_without_registration() -> None:
    """For a logged-in user with no Registration all debug values are None."""
    request = RequestFactory().get("/")
    request.user = UserFactory.create()
    result = debug_panel(request)
    assert result["debug_registration"] is None
    assert result["debug_match"] is None
    assert result["debug_counterpart"] is None


@override_settings(DEBUG=True)
def test_debug_panel_populates_registration_without_match() -> None:
    """With a registration but no proposed match, debug_match/counterpart are None."""
    user = UserFactory.create()
    reg = RegistrationFactory.create(user=user, status=Registration.Status.WAITING)

    request = RequestFactory().get("/")
    request.user = user
    result = debug_panel(request)

    assert result["debug_registration"] == reg
    assert result["debug_match"] is None
    assert result["debug_counterpart"] is None


@override_settings(DEBUG=True)
def test_debug_panel_populates_match_and_counterpart() -> None:
    """With a proposed match, debug_match and debug_counterpart are both populated."""
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

    request = RequestFactory().get("/")
    request.user = user
    result = debug_panel(request)

    assert result["debug_registration"] == my_reg
    assert result["debug_match"] == match
    assert result["debug_counterpart"] == counterpart_reg


@override_settings(DEBUG=True)
def test_debug_panel_populates_counterpart_for_referee_side() -> None:
    """The counterpart is the ambassador when the logged-in user is a referee."""
    ambassador_user = UserFactory.create()
    ambassador_reg = RegistrationFactory.create(
        user=ambassador_user,
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.MATCHED,
    )

    referee_user = UserFactory.create()
    referee_reg = RegistrationFactory.create(
        user=referee_user,
        role=Registration.Role.REFEREE,
        status=Registration.Status.MATCHED,
    )
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PROPOSED,
    )

    request = RequestFactory().get("/")
    request.user = referee_user
    result = debug_panel(request)

    assert result["debug_registration"] == referee_reg
    assert result["debug_counterpart"] == ambassador_reg
