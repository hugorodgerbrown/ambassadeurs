# Tests for core admin classes.

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse

from core.models import StateTransitionLog
from tests.accounts.factories import UserFactory
from tests.core.factories import StateTransitionLogFactory
from tests.matching.factories import MatchFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_staff_user():
    """Create and return a superuser for admin access in tests."""
    user = UserFactory.create(
        username="core_admin_staff",
        is_staff=True,
        is_superuser=True,
    )
    user.set_password("password")
    user.save()
    return user


# ---------------------------------------------------------------------------
# Changelist smoke test
# ---------------------------------------------------------------------------


def test_state_transition_log_changelist_returns_200(client: Client) -> None:
    """GET the StateTransitionLog changelist as a staff user returns HTTP 200."""
    StateTransitionLogFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:core_statetransitionlog_changelist")
    response = client.get(url)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# target_link method
# ---------------------------------------------------------------------------


def test_target_link_renders_admin_change_url(client: Client) -> None:
    """target_link returns an anchor pointing to the target's admin change page."""
    match = MatchFactory.create()
    StateTransitionLogFactory.create(
        content_type=ContentType.objects.get_for_model(match),
        object_id=match.pk,
    )
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:core_statetransitionlog_changelist")
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    # The changelist should contain a link to the match's admin change page.
    expected_url = reverse("admin:matching_match_change", args=[match.pk])
    assert expected_url in content


def test_target_link_returns_dash_for_unregistered_content_type() -> None:
    """target_link returns an em-dash when no admin change view is registered."""
    from django.contrib import admin as django_admin

    from core.admin import StateTransitionLogAdmin

    admin_instance = StateTransitionLogAdmin(StateTransitionLog, django_admin.site)

    # Use a fake content type that points at a non-existent model/app so that
    # NoReverseMatch is raised inside target_link.
    class _FakeContentType:
        app_label = "nonexistent_app"
        model = "nonexistentmodel"

    class _FakeLog:
        content_type = _FakeContentType()
        object_id = 999

    result = admin_instance.target_link(_FakeLog())  # type: ignore[arg-type]
    assert result == "—"
