# Tests for the core notifications context processor.
#
# Verifies that notifications() returns only Notification instances that are
# both within their display window (.active()) and visible to the request's
# user (is_visible_to()) — VERB-109. Also covers end-to-end rendering of the
# notification strip through a real page (public:home).

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.context_processors import notifications
from core.models import Notification
from tests.accounts.factories import UserFactory
from tests.core.factories import NotificationFactory

pytestmark = pytest.mark.django_db


def test_notifications_returns_active_visible_notification_for_anonymous() -> None:
    """An EVERYONE, always-active notification is returned for an anon visitor."""
    NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert len(result["active_notifications"]) == 1


def test_notifications_excludes_inactive_notification() -> None:
    """A notification outside its display window is excluded."""
    now = timezone.now()
    NotificationFactory.create(
        audience=Notification.Audience.EVERYONE,
        starts_at=now + timedelta(hours=1),
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert result["active_notifications"] == []


def test_notifications_excludes_notification_outside_audience() -> None:
    """An AUTHENTICATED notification is excluded for an anonymous visitor."""
    NotificationFactory.create(audience=Notification.Audience.AUTHENTICATED)
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert result["active_notifications"] == []


def test_notifications_includes_authenticated_notification_for_authenticated() -> None:
    """An AUTHENTICATED notification is included for a logged-in user."""
    NotificationFactory.create(audience=Notification.Audience.AUTHENTICATED)
    request = RequestFactory().get("/")
    request.user = UserFactory.create()
    result = notifications(request)
    assert len(result["active_notifications"]) == 1


def test_notifications_returns_newest_first() -> None:
    """Equal-priority active notifications are returned newest first."""
    first = NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    second = NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert [n.pk for n in result["active_notifications"]] == [second.pk, first.pk]


def test_notifications_orders_higher_priority_first() -> None:
    """A HIGH-priority notification is returned above an earlier NEUTRAL one."""
    neutral = NotificationFactory.create(
        audience=Notification.Audience.EVERYONE,
        priority=Notification.Priority.NEUTRAL,
    )
    high = NotificationFactory.create(
        audience=Notification.Audience.EVERYONE,
        priority=Notification.Priority.HIGH,
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert [n.pk for n in result["active_notifications"]] == [high.pk, neutral.pk]


def test_notifications_excludes_disabled_notification() -> None:
    """A disabled (kill-switch off) notification is never returned, even if active."""
    NotificationFactory.create(
        audience=Notification.Audience.EVERYONE,
        enabled=False,
    )
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert result["active_notifications"] == []


# ---------------------------------------------------------------------------
# End-to-end template rendering (public:home extends base.html)
# ---------------------------------------------------------------------------


def test_home_page_renders_active_notification_content() -> None:
    """An active notification's sanitised content renders on a real page."""
    NotificationFactory.create(
        content="<b>Registration opens July 31st</b>",
        audience=Notification.Audience.EVERYONE,
    )
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Registration opens July 31st" in content
    assert 'data-notification-id="' in content


def test_home_page_renders_no_strip_gap_without_notifications() -> None:
    """With no active notifications, the strip container is absent."""
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="notification-strip"' not in content


def test_home_page_strips_script_tag_from_notification() -> None:
    """An injected <script> never reaches the rendered page."""
    NotificationFactory.create(
        content="<script>alert(1)</script>Hello",
        audience=Notification.Audience.EVERYONE,
    )
    response = Client().get(reverse("public:home"))
    content = response.content.decode()
    assert "<script>alert(1)</script>" not in content


def test_home_page_shows_dismiss_button_for_dismissible_notification() -> None:
    """A dismissible notification renders a dismiss control."""
    NotificationFactory.create(is_dismissible=True)
    response = Client().get(reverse("public:home"))
    content = response.content.decode()
    assert "data-dismiss-notification=" in content


def test_home_page_hides_dismiss_button_for_permanent_notification() -> None:
    """A permanent (non-dismissible) notification shows no dismiss control."""
    NotificationFactory.create(is_dismissible=False)
    response = Client().get(reverse("public:home"))
    content = response.content.decode()
    assert "data-dismiss-notification=" not in content
