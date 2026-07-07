# Tests for core model abstractions.

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.utils import timezone

from core.models import BaseModel, Notification, StateTransitionLog
from core.services import record_transition
from tests.accounts.factories import UserFactory
from tests.core.factories import NotificationFactory, StateTransitionLogFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# BaseModel
# ---------------------------------------------------------------------------


class _Concrete(BaseModel):
    """Throwaway concrete subclass that does not override to_string()."""

    class Meta:
        app_label = "core"
        managed = False


def test_base_model_requires_to_string() -> None:
    """A subclass that forgets to_string() raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        _Concrete().to_string()


# ---------------------------------------------------------------------------
# StateTransitionLog — model behaviour
# ---------------------------------------------------------------------------


def test_state_transition_log_to_string() -> None:
    """to_string returns a readable summary of content type, pk, and transition."""
    log = StateTransitionLogFactory.create(
        field_name="status",
        state_before="PROPOSED",
        state_after="ACCEPTED",
    )
    result = log.to_string()
    assert "status" in result
    assert "PROPOSED" in result
    assert "ACCEPTED" in result
    assert str(log.object_id) in result


def test_state_transition_log_str_delegates_to_to_string() -> None:
    """__str__ delegates to to_string (CLAUDE.md convention)."""
    log = StateTransitionLogFactory.create()
    assert str(log) == log.to_string()


def test_state_transition_log_default_ordering_is_newest_first() -> None:
    """Meta.ordering is -created_at so the most recent entry comes first."""
    log_a = StateTransitionLogFactory.create(state_after="ACCEPTED")
    log_b = StateTransitionLogFactory.create(state_after="DECLINED")
    qs = list(StateTransitionLog.objects.all())
    # log_b was created after log_a, so it should appear first.
    assert qs[0].pk == log_b.pk
    assert qs[1].pk == log_a.pk


def test_state_transition_log_gfk_resolves_to_target_instance() -> None:
    """The GenericForeignKey target attribute resolves to the original instance."""
    match = MatchFactory.create()
    # Supply content_type and object_id directly; do not pass _match_target so
    # the factory does not create a second, unrelated Match.
    log = StateTransitionLogFactory.create(
        content_type=ContentType.objects.get_for_model(match),
        object_id=match.pk,
    )
    log.refresh_from_db()
    # Accessing .target should resolve to the same Match row.
    assert log.target is not None
    assert log.target.pk == match.pk


def test_state_transition_log_queryset_filters() -> None:
    """StateTransitionLogQuerySet supports standard queryset operations."""
    StateTransitionLogFactory.create(field_name="status", state_after="ACCEPTED")
    StateTransitionLogFactory.create(field_name="status", state_after="DECLINED")
    assert StateTransitionLog.objects.filter(state_after="ACCEPTED").count() == 1
    assert StateTransitionLog.objects.all().count() == 2


# ---------------------------------------------------------------------------
# record_transition service
# ---------------------------------------------------------------------------


def test_record_transition_writes_one_row_with_correct_fields() -> None:
    """record_transition creates a single log row with all expected field values."""
    match = MatchFactory.create()
    before_count = StateTransitionLog.objects.count()
    before = timezone.now()

    log = record_transition(match, "status", before="PROPOSED", after="ACCEPTED")

    after = timezone.now()
    assert StateTransitionLog.objects.count() == before_count + 1
    assert log.field_name == "status"
    assert log.state_before == "PROPOSED"
    assert log.state_after == "ACCEPTED"
    assert log.object_id == match.pk
    assert log.content_type == ContentType.objects.get_for_model(match)
    assert log.created_at is not None
    assert before <= log.created_at <= after


def test_record_transition_works_with_registration_instance() -> None:
    """record_transition is model-agnostic — it works with Registration too."""
    reg = RegistrationFactory.create()

    log = record_transition(reg, "status", before="UNVERIFIED", after="VERIFIED")

    assert log.field_name == "status"
    assert log.state_before == "UNVERIFIED"
    assert log.state_after == "VERIFIED"
    assert log.object_id == reg.pk
    assert log.content_type == ContentType.objects.get_for_model(reg)


# ---------------------------------------------------------------------------
# Notification — save() sanitisation, to_string/__str__
# ---------------------------------------------------------------------------


def test_notification_save_populates_content_sanitised() -> None:
    """save() sanitises content into content_sanitised via nh3."""
    notification = NotificationFactory.create(
        content="<script>alert(1)</script><b>hello</b>"
    )
    assert "<script>" not in notification.content_sanitised
    assert "<b>hello</b>" in notification.content_sanitised


def test_notification_to_string_previews_content_and_audience() -> None:
    """to_string returns a truncated content preview plus the audience label."""
    notification = NotificationFactory.create(
        content="short message", audience=Notification.Audience.EVERYONE
    )
    result = notification.to_string()
    assert "short message" in result
    assert "Everyone" in result


def test_notification_to_string_truncates_long_content() -> None:
    """to_string truncates content over 50 characters with an ellipsis."""
    long_content = "x" * 100
    notification = NotificationFactory.create(content=long_content)
    result = notification.to_string()
    assert "..." in result
    assert len(result.split(" [")[0]) <= 50


def test_notification_str_delegates_to_to_string() -> None:
    """__str__ delegates to to_string (CLAUDE.md convention)."""
    notification = NotificationFactory.create()
    assert str(notification) == notification.to_string()


def test_notification_default_ordering_is_newest_first_within_weight() -> None:
    """Equal-weight notifications fall back to -created_at (newest first)."""
    first = NotificationFactory.create()
    second = NotificationFactory.create()
    qs = list(Notification.objects.all())
    assert qs[0].pk == second.pk
    assert qs[1].pk == first.pk


def test_notification_ordering_puts_higher_weight_first() -> None:
    """Meta.ordering is -weight first: weight=1 sorts above an earlier weight=0."""
    low_weight = NotificationFactory.create(weight=0)
    high_weight = NotificationFactory.create(weight=1)
    qs = list(Notification.objects.all())
    # high_weight was created second but outranks low_weight on weight.
    assert qs[0].pk == high_weight.pk
    assert qs[1].pk == low_weight.pk


# ---------------------------------------------------------------------------
# Notification.design / weight / enabled
# ---------------------------------------------------------------------------

_TEST_DESIGNS = {
    "INFO": mock.Mock(
        label="Info",
        description="Calm tone.",
        css_classes="cls-info",
        css_styles="color: blue;",
    ),
}


def test_notification_design_defaults_to_notice() -> None:
    """A notification defaults to the NOTICE design."""
    notification = NotificationFactory.create()
    assert notification.design == "NOTICE"


def test_notification_weight_defaults_to_zero() -> None:
    """A notification defaults to weight 0."""
    notification = NotificationFactory.create()
    assert notification.weight == 0


@override_settings(NOTIFICATION_DESIGNS=_TEST_DESIGNS)
def test_notification_design_accessors_return_configured_values() -> None:
    """design_label/description/classes/styles look design up in settings."""
    notification = NotificationFactory.create(design="INFO")
    assert notification.design_label == "Info"
    assert notification.design_description == "Calm tone."
    assert notification.design_classes == "cls-info"
    assert notification.design_styles == "color: blue;"


@override_settings(NOTIFICATION_DESIGNS=_TEST_DESIGNS)
def test_notification_design_accessors_fall_back_safely_for_unknown_key() -> None:
    """An unknown design key returns empty strings rather than raising."""
    notification = NotificationFactory.create(design="DOES-NOT-EXIST")
    assert notification.design_label == ""
    assert notification.design_description == ""
    assert notification.design_classes == ""
    assert notification.design_styles == ""


def test_notification_enabled_defaults_true() -> None:
    """A notification's kill switch is on by default."""
    notification = NotificationFactory.create()
    assert notification.enabled is True


def test_notification_queryset_enabled_excludes_disabled() -> None:
    """The enabled() queryset drops notifications with the kill switch off."""
    on = NotificationFactory.create(enabled=True)
    NotificationFactory.create(enabled=False)
    assert list(Notification.objects.enabled()) == [on]


# ---------------------------------------------------------------------------
# Notification.is_active property
# ---------------------------------------------------------------------------


def test_notification_is_active_true_when_both_bounds_null() -> None:
    """Both bounds null means always active."""
    notification = NotificationFactory.create(starts_at=None, ends_at=None)
    assert notification.is_active is True


def test_notification_is_active_false_before_start() -> None:
    """A notification whose window has not started yet is not active."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=now + timedelta(hours=1), ends_at=None
    )
    assert notification.is_active is False


def test_notification_is_active_false_after_end() -> None:
    """A notification whose window has ended is not active."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=None, ends_at=now - timedelta(hours=1)
    )
    assert notification.is_active is False


def test_notification_is_active_true_within_bounds() -> None:
    """A notification within both bounds is active."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1)
    )
    assert notification.is_active is True


def test_notification_is_active_false_when_both_bounds_exclude_now() -> None:
    """A notification whose window is entirely in the past is not active."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=now - timedelta(hours=2), ends_at=now - timedelta(hours=1)
    )
    assert notification.is_active is False


def test_notification_is_active_false_at_exact_ends_at_boundary() -> None:
    """At ends_at == now, is_active is False (closed interval on ends_at).

    Locks in the half-open window: starts_at is inclusive (<=) but ends_at is
    exclusive (<=, i.e. inactive from the boundary instant onward), matching
    NotificationQuerySet.active()'s ends_at__gt=now.
    """
    now = timezone.now()
    notification = NotificationFactory.create(starts_at=None, ends_at=now)
    with mock.patch("core.models.timezone.now", return_value=now):
        assert notification.is_active is False


def test_notification_is_active_true_at_exact_starts_at_boundary() -> None:
    """At starts_at == now, is_active is True (starts_at is inclusive, <=)."""
    now = timezone.now()
    notification = NotificationFactory.create(starts_at=now, ends_at=None)
    with mock.patch("core.models.timezone.now", return_value=now):
        assert notification.is_active is True


# ---------------------------------------------------------------------------
# NotificationQuerySet.active()
# ---------------------------------------------------------------------------


def test_notification_queryset_active_includes_both_bounds_null() -> None:
    """.active() includes a notification with both bounds null."""
    notification = NotificationFactory.create(starts_at=None, ends_at=None)
    now = timezone.now()
    assert notification in Notification.objects.active(now)


def test_notification_queryset_active_excludes_before_start() -> None:
    """.active() excludes a notification whose window has not started."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=now + timedelta(hours=1), ends_at=None
    )
    assert notification not in Notification.objects.active(now)


def test_notification_queryset_active_excludes_after_end() -> None:
    """.active() excludes a notification whose window has ended."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=None, ends_at=now - timedelta(hours=1)
    )
    assert notification not in Notification.objects.active(now)


def test_notification_queryset_active_includes_within_bounds() -> None:
    """.active() includes a notification within both bounds."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1)
    )
    assert notification in Notification.objects.active(now)


def test_notification_queryset_active_excludes_both_bounds_in_past() -> None:
    """.active() excludes a notification whose window is entirely past."""
    now = timezone.now()
    notification = NotificationFactory.create(
        starts_at=now - timedelta(hours=2), ends_at=now - timedelta(hours=1)
    )
    assert notification not in Notification.objects.active(now)


def test_notification_queryset_active_excludes_exact_ends_at_boundary() -> None:
    """.active(now) excludes a notification whose ends_at == now exactly.

    Locks in the half-open window: ends_at__gt=now, so the boundary instant
    itself is already inactive.
    """
    now = timezone.now()
    notification = NotificationFactory.create(starts_at=None, ends_at=now)
    assert notification not in Notification.objects.active(now)


def test_notification_queryset_active_includes_exact_starts_at_boundary() -> None:
    """.active(now) includes a notification whose starts_at == now exactly.

    Locks in the half-open window: starts_at__lte=now, so the boundary instant
    itself is already active.
    """
    now = timezone.now()
    notification = NotificationFactory.create(starts_at=now, ends_at=None)
    assert notification in Notification.objects.active(now)


# ---------------------------------------------------------------------------
# Notification.is_visible_to()
# ---------------------------------------------------------------------------


def test_notification_everyone_visible_to_anonymous() -> None:
    """An EVERYONE notification is visible to an anonymous visitor."""
    notification = NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    assert notification.is_visible_to(AnonymousUser()) is True


def test_notification_everyone_visible_to_authenticated() -> None:
    """An EVERYONE notification is visible to an authenticated visitor."""
    notification = NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    user = UserFactory.create()
    assert notification.is_visible_to(user) is True


def test_notification_anonymous_visible_only_to_anonymous() -> None:
    """An ANONYMOUS notification is visible only to logged-out visitors."""
    notification = NotificationFactory.create(audience=Notification.Audience.ANONYMOUS)
    user = UserFactory.create()
    assert notification.is_visible_to(AnonymousUser()) is True
    assert notification.is_visible_to(user) is False


def test_notification_authenticated_visible_only_to_authenticated() -> None:
    """An AUTHENTICATED notification is visible only to logged-in visitors."""
    notification = NotificationFactory.create(
        audience=Notification.Audience.AUTHENTICATED
    )
    user = UserFactory.create()
    assert notification.is_visible_to(user) is True
    assert notification.is_visible_to(AnonymousUser()) is False


def _ambassadors_group() -> object:
    """Return a User queryset for users with an ambassador registration.

    Mirrors the shape of settings.CUSTOM_NOTIFICATION_GROUPS callables (a
    User queryset, not a Registration queryset), so is_visible_to's
    ``.filter(pk=user.pk)`` works exactly as it would against the real
    settings value.
    """
    return get_user_model().objects.filter(registration__role="AMBASSADOR")


@override_settings(CUSTOM_NOTIFICATION_GROUPS={"ambassadors": _ambassadors_group})
def test_notification_custom_visible_to_group_member() -> None:
    """A CUSTOM notification is visible to a user in the named group."""
    ambassador_reg = RegistrationFactory.create(role="AMBASSADOR")
    notification = NotificationFactory.create(
        audience=Notification.Audience.CUSTOM, custom_group_key="ambassadors"
    )
    user = get_user_model().objects.get(pk=ambassador_reg.user_id)
    assert notification.is_visible_to(user) is True


@override_settings(CUSTOM_NOTIFICATION_GROUPS={"ambassadors": _ambassadors_group})
def test_notification_custom_not_visible_to_non_member() -> None:
    """A CUSTOM notification is not visible to a user outside the named group."""
    notification = NotificationFactory.create(
        audience=Notification.Audience.CUSTOM, custom_group_key="ambassadors"
    )
    referee_reg = RegistrationFactory.create(referee=True)
    user = get_user_model().objects.get(pk=referee_reg.user_id)
    assert notification.is_visible_to(user) is False


def test_notification_custom_not_visible_to_anonymous() -> None:
    """A CUSTOM notification is never visible to an anonymous visitor."""
    notification = NotificationFactory.create(
        audience=Notification.Audience.CUSTOM, custom_group_key="ambassadors"
    )
    assert notification.is_visible_to(AnonymousUser()) is False


def test_notification_custom_blank_key_not_visible() -> None:
    """A CUSTOM notification with a blank custom_group_key is never visible."""
    notification = NotificationFactory.create(
        audience=Notification.Audience.CUSTOM, custom_group_key=""
    )
    user = UserFactory.create()
    assert notification.is_visible_to(user) is False


def test_notification_custom_unknown_key_not_visible() -> None:
    """A CUSTOM notification naming an unconfigured key is never visible."""
    notification = NotificationFactory.create(
        audience=Notification.Audience.CUSTOM, custom_group_key="nonexistent-group"
    )
    user = UserFactory.create()
    assert notification.is_visible_to(user) is False
