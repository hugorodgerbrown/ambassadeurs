# Shared model abstractions.
#
# BaseModel is abstract (no concrete table). Every concrete model in the
# project inherits it and ships the full kit described in CLAUDE.md "Models":
# an explicit admin class, a to_string() method, an explicit Meta.ordering, a
# custom queryset, a factory, and tests.
#
# StateTransitionLog is the first concrete table in core: a generic, append-only
# audit log of state-field transitions across any model instance. It is recorded
# inline from transition services — never via Django signals (CLAUDE.md).
#
# Notification (VERB-109) is a staff-authored, site-wide announcement banner
# rendered above page content via a context processor and template partial.
# It is unrelated to accounts.partials.match_status or the matching app's
# match-notification emails, which share the word "notification" only by
# coincidence.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser


class BaseQuerySet(models.QuerySet):  # type: ignore[type-arg]
    """Base queryset for project models.

    Concrete models subclass this to add their own query methods, keeping
    query logic on the queryset rather than scattered across services/views.
    """


class BaseModel(models.Model):
    """Abstract base model providing timezone-aware audit timestamps.

    Subclasses must implement ``to_string()``; ``__str__`` delegates to it so
    every model has a single, explicit human-readable representation.
    """

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return the model's human-readable representation."""
        return self.to_string()

    def to_string(self) -> str:
        """Return a human-readable representation; subclasses must override."""
        raise NotImplementedError(f"{type(self).__name__} must implement to_string().")


class StateTransitionLogQuerySet(BaseQuerySet):
    """Queryset for StateTransitionLog."""


class StateTransitionLog(BaseModel):
    """Append-only audit record of a single state-field transition.

    Created inline by ``core.services.record_transition`` after a field change,
    inside the same ``transaction.atomic()`` block as the change itself. This
    means admin edits and direct ``.update()`` calls that bypass the service
    layer are visibly unlogged rather than silently misrecorded.

    ``BaseModel.created_at`` serves as the transition timestamp; no separate
    field is added.

    The ``target`` GenericForeignKey lets the log record transitions on any
    model (``Match.status``, ``Registration.status``, etc.) with a single table.
    ``object_id`` is ``PositiveBigIntegerField`` to match the ``BigAutoField``
    PKs used on every concrete model in the project.
    """

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        help_text="Django content type of the transitioned model instance.",
    )
    object_id = models.PositiveBigIntegerField(
        help_text="Primary key of the transitioned model instance.",
    )
    target = GenericForeignKey("content_type", "object_id")

    field_name = models.CharField(
        max_length=64,
        help_text="Name of the field that transitioned (e.g. 'status').",
    )
    state_before = models.CharField(
        max_length=64,
        help_text="Value of the field immediately before the transition.",
    )
    state_after = models.CharField(
        max_length=64,
        help_text="Value of the field immediately after the transition.",
    )

    objects = StateTransitionLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()

    def to_string(self) -> str:
        """Return a human-readable label for the log entry."""
        return (
            f"{self.content_type} #{self.object_id}: "
            f"{self.field_name} {self.state_before!r} → {self.state_after!r}"
        )


class NotificationQuerySet(BaseQuerySet):
    """Queryset for Notification."""

    def active(self, now: datetime) -> NotificationQuerySet:
        """Return notifications whose display window contains ``now``.

        A notification is active when ``starts_at`` is null or in the past
        AND ``ends_at`` is null or in the future — either or both bounds may
        be unset, meaning "always" on that side. ``now`` is an injected
        argument (rather than read internally via ``timezone.now()``) so the
        queryset stays a pure function of its arguments, matching the
        inversion-of-control convention used elsewhere in the project (e.g.
        ``matching.MatchQuerySet.lapsed``).

        Args:
            now: The tz-aware instant to test the window against.
        """
        return self.filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now),
        )


class Notification(BaseModel):
    """A staff-authored, site-wide announcement banner (VERB-109).

    Rendered above page content on every page extending ``base.html`` via
    ``core.context_processors.notifications`` and
    ``templates/includes/notification_strip.html``. Targeted at an
    ``audience`` so staff can address the right visitors (everyone, anonymous
    only, authenticated only, or a named custom group).

    Content is staff-authored HTML/plain text (``content``), sanitised once at
    save time into ``content_sanitised`` via
    ``core.services.sanitise_notification_html`` (nh3 allow-list). This is the
    single, audited exception to Invariant 4 (no ``mark_safe()`` on
    user-supplied content) — the template renders ``content_sanitised|safe``
    because the value is already guaranteed safe by the time it is stored.

    Unrelated to ``accounts.partials.match_status`` or the matching app's
    match-notification emails, which share the word "notification" only by
    coincidence.
    """

    class Audience(models.TextChoices):
        """Who a notification is shown to. UPPER_CASE values (CLAUDE.md).

        CUSTOM notifications additionally name a key into
        ``settings.CUSTOM_NOTIFICATION_GROUPS`` via ``custom_group_key``.
        """

        EVERYONE = "EVERYONE", _("Everyone")
        ANONYMOUS = "ANONYMOUS", _("Anonymous visitors only")
        AUTHENTICATED = "AUTHENTICATED", _("Authenticated users only")
        CUSTOM = "CUSTOM", _("Custom group")

    content = models.TextField(
        help_text="Raw HTML/plain text as authored by staff.",
    )
    content_sanitised = models.TextField(
        blank=True,
        editable=False,
        help_text=(
            "The nh3-sanitised HTML derived from content, set on save(). "
            "Rendered with the |safe filter — never edit directly."
        ),
    )
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the notification starts showing; blank means always.",
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the notification stops showing; blank means always.",
    )
    is_dismissible = models.BooleanField(
        default=True,
        help_text=(
            "Whether visitors can dismiss the notification for their browser "
            "session. Permanent notifications show no dismiss control."
        ),
    )
    audience = models.CharField(
        max_length=16,
        choices=Audience.choices,
        default=Audience.EVERYONE,
    )
    custom_group_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Key into settings.CUSTOM_NOTIFICATION_GROUPS; used only when "
            "audience is CUSTOM."
        ),
    )

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Sanitise ``content`` into ``content_sanitised`` before saving.

        This is the single, audited exception to Invariant 4: sanitising once
        here (rather than at render time) means the template can safely
        render ``content_sanitised|safe`` without re-escaping.
        """
        from .services import sanitise_notification_html

        self.content_sanitised = sanitise_notification_html(self.content)
        super().save(*args, **kwargs)

    def to_string(self) -> str:
        """Return a truncated content preview plus audience."""
        preview = self.content.strip().replace("\n", " ")
        if len(preview) > 50:
            preview = preview[:47] + "..."
        return f"{preview} [{self.get_audience_display()}]"

    @property
    def is_active(self) -> bool:
        """True if now is within the display window (derived — a property).

        Both bounds null means always active; either bound may be unset.
        """
        now = timezone.now()
        if self.starts_at is not None and self.starts_at > now:
            return False
        if self.ends_at is not None and self.ends_at <= now:
            return False
        return True

    def is_visible_to(self, user: AbstractBaseUser | AnonymousUser) -> bool:
        """Return whether ``user`` is in this notification's audience.

        Takes an argument and, for CUSTOM audiences, hits the database via
        the configured group queryset — so this is a method, not a property
        (CLAUDE.md "Derived values are @property, not methods").

        Args:
            user: The request's user (may be ``AnonymousUser``).

        Returns:
            True if ``user`` should see this notification.
        """
        if self.audience == Notification.Audience.EVERYONE:
            return True
        if self.audience == Notification.Audience.ANONYMOUS:
            return user.is_anonymous
        if self.audience == Notification.Audience.AUTHENTICATED:
            return user.is_authenticated
        if self.audience == Notification.Audience.CUSTOM:
            if not user.is_authenticated:
                return False
            group_fn = settings.CUSTOM_NOTIFICATION_GROUPS.get(self.custom_group_key)
            if group_fn is None:
                return False
            return group_fn().filter(pk=user.pk).exists()
        return False
