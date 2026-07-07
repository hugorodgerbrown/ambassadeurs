# Tests for core admin classes.

from typing import cast

import pytest
from django import forms
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, override_settings
from django.urls import reverse

from core.admin import NotificationForm
from core.models import Notification, StateTransitionLog
from tests.accounts.factories import UserFactory
from tests.core.factories import NotificationFactory, StateTransitionLogFactory
from tests.matching.factories import MatchFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_staff_user() -> User:
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


# ---------------------------------------------------------------------------
# NotificationAdmin — changelist smoke test
# ---------------------------------------------------------------------------


def test_notification_content_preview_truncates_long_content() -> None:
    """content_preview truncates content over 50 characters with an ellipsis."""
    from django.contrib import admin as django_admin

    from core.admin import NotificationAdmin

    admin_instance = NotificationAdmin(Notification, django_admin.site)
    notification = NotificationFactory.create(content="x" * 100)
    result = admin_instance.content_preview(notification)
    assert result.endswith("...")
    assert len(result) <= 50


def test_notification_changelist_returns_200(client: Client) -> None:
    """GET the Notification changelist as a staff user returns HTTP 200."""
    NotificationFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:core_notification_changelist")
    response = client.get(url)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# NotificationForm validation
# ---------------------------------------------------------------------------

_CUSTOM_GROUPS = {"ambassadors": lambda: User.objects.none()}


def _form_data(**overrides: object) -> dict[str, object]:
    """Build valid base NotificationForm data, with per-test overrides."""
    data: dict[str, object] = {
        "content": "Some announcement",
        "design": "NOTICE",
        "weight": 0,
        "enabled": True,
        "starts_at": "",
        "ends_at": "",
        "is_dismissible": True,
        "audience": Notification.Audience.EVERYONE,
        "custom_group_key": "",
    }
    data.update(overrides)
    return data


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_everyone_with_design_and_weight_is_valid() -> None:
    """A well-formed EVERYONE notification with a design and weight validates."""
    form = NotificationForm(data=_form_data(design="URGENT", weight=5, enabled=True))
    assert form.is_valid(), form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_unknown_design_is_invalid() -> None:
    """A design value absent from settings.NOTIFICATION_DESIGNS is rejected.

    The ChoiceField widget itself already rejects a key outside its
    settings-derived choices, so this exercises that first layer of defence.
    """
    form = NotificationForm(data=_form_data(design="not-a-real-design"))
    assert not form.is_valid()
    assert "design" in form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_clean_rejects_design_bypassing_widget_choices() -> None:
    """clean() itself rejects an unconfigured design even if the widget allowed it.

    Defence-in-depth: simulates a stale/tampered value that bypassed the
    ChoiceField widget (e.g. a design removed from settings after the form
    was rendered), proving core.admin.NotificationForm.clean()'s own
    membership check — not just the widget — enforces the invariant.
    """
    form = NotificationForm(data=_form_data(design="NOTICE"))
    # Widen the widget's choices after construction so "stale-design" passes
    # ChoiceField validation and clean() is left to reject it.
    cast(forms.ChoiceField, form.fields["design"]).choices = [
        ("stale-design", "stale-design"),
    ]
    form.data = form.data.copy()
    form.data["design"] = "stale-design"

    assert not form.is_valid()
    assert "design" in form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_custom_without_key_is_invalid() -> None:
    """CUSTOM audience with a blank custom_group_key fails validation."""
    form = NotificationForm(
        data=_form_data(audience=Notification.Audience.CUSTOM, custom_group_key="")
    )
    assert not form.is_valid()
    assert "custom_group_key" in form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_custom_with_unknown_key_is_invalid() -> None:
    """CUSTOM audience naming a key absent from settings fails validation.

    The ChoiceField widget itself already rejects a key outside its
    settings-derived choices, so this exercises that first layer of defence.
    """
    form = NotificationForm(
        data=_form_data(
            audience=Notification.Audience.CUSTOM,
            custom_group_key="not-a-real-group",
        )
    )
    assert not form.is_valid()
    assert "custom_group_key" in form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_clean_rejects_key_bypassing_widget_choices() -> None:
    """clean() itself rejects an unconfigured key even if the widget allowed it.

    Defence-in-depth: simulates a stale/tampered value that bypassed the
    ChoiceField widget (e.g. a key removed from settings after the form was
    rendered), proving core.admin.NotificationForm.clean()'s own membership
    check — not just the widget — enforces the invariant.
    """
    form = NotificationForm(
        data=_form_data(
            audience=Notification.Audience.CUSTOM,
            custom_group_key="ambassadors",
        )
    )
    # Widen the widget's choices after construction so "stale-group" passes
    # ChoiceField validation and clean() is left to reject it.
    cast(forms.ChoiceField, form.fields["custom_group_key"]).choices = [
        ("", "—"),
        ("stale-group", "stale-group"),
    ]
    form.data = form.data.copy()
    form.data["custom_group_key"] = "stale-group"

    assert not form.is_valid()
    assert "custom_group_key" in form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_custom_with_valid_key_is_valid() -> None:
    """CUSTOM audience naming a configured key passes validation."""
    form = NotificationForm(
        data=_form_data(
            audience=Notification.Audience.CUSTOM,
            custom_group_key="ambassadors",
        )
    )
    assert form.is_valid(), form.errors


@override_settings(CUSTOM_NOTIFICATION_GROUPS=_CUSTOM_GROUPS)
def test_notification_form_non_custom_clears_custom_group_key() -> None:
    """A non-CUSTOM audience forces custom_group_key blank, even if supplied."""
    form = NotificationForm(
        data=_form_data(
            audience=Notification.Audience.EVERYONE,
            custom_group_key="ambassadors",
        )
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["custom_group_key"] == ""
