# Tests for the core notifications context processor.
#
# Verifies that notifications() returns only Notification instances that are
# both within their display window (.active()) and visible to the request's
# user (is_visible_to()) — VERB-109.

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
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
    """Multiple active notifications are returned newest first."""
    first = NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    second = NotificationFactory.create(audience=Notification.Audience.EVERYONE)
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    result = notifications(request)
    assert [n.pk for n in result["active_notifications"]] == [second.pk, first.pk]
