# Core service functions shared across the project.
#
# record_transition is the single entry point for writing StateTransitionLog
# rows. It is intentionally generic — it works with any concrete model instance
# and any field name — so every state machine in the project funnels through
# one place. Call it inline from transition service functions inside an existing
# transaction.atomic() block; never from Django signals (CLAUDE.md).
#
# sanitise_notification_html is the audited, single exception to Invariant 4
# (no mark_safe on user-supplied content): Notification.save() sanitises once
# at write time with nh3 (the maintained Rust `ammonia` bindings, not the
# EOL bleach), so the stored content_sanitised value is always safe to render
# with the `|safe` filter.

import logging

import nh3
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .models import StateTransitionLog

logger = logging.getLogger(__name__)

# Allow-list for staff-authored Notification content. Deliberately small:
# enough to link and emphasise text, nothing that can execute script or style
# injection. Tunable later; no ADR needed for launch (VERB-109 scoping).
#
# "rel" is deliberately NOT in the allow-list: nh3's default link_rel behaviour
# force-adds rel="noopener noreferrer" to every <a>, which is what stops a
# staff-authored `target="_blank"` link from tabnabbing the origin page (the
# opened page could otherwise use window.opener to navigate this tab
# elsewhere). Allowing "rel" as an attribute would let staff-authored markup
# override or strip that protection, so it stays off the list and nh3 is left
# to manage it.
_NOTIFICATION_ALLOWED_TAGS = {"a", "b", "strong", "em", "i", "br", "span", "p"}
_NOTIFICATION_ALLOWED_ATTRIBUTES = {"a": {"href", "title", "target"}}
_NOTIFICATION_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitise_notification_html(html: str) -> str:
    """Sanitise staff-authored notification HTML for safe rendering.

    This is the single, audited exception to Invariant 4 (no ``mark_safe()``
    on user-supplied content): ``Notification.save()`` calls this once, at
    write time, so the stored ``content_sanitised`` value can be rendered with
    the ``|safe`` template filter without re-escaping. Uses ``nh3`` (the
    maintained Rust ``ammonia`` bindings) rather than ``bleach``, which has
    been unmaintained/EOL since 2023.

    Args:
        html: Raw HTML/plain text as authored by staff in Django admin.

    Returns:
        The sanitised HTML string, restricted to a small allow-list of tags
        and attributes considered safe for a notification banner.
    """
    return nh3.clean(
        html,
        tags=_NOTIFICATION_ALLOWED_TAGS,
        attributes=_NOTIFICATION_ALLOWED_ATTRIBUTES,
        url_schemes=_NOTIFICATION_ALLOWED_URL_SCHEMES,
    )


def record_transition(
    instance: models.Model,
    field_name: str,
    *,
    before: str,
    after: str,
) -> StateTransitionLog:
    """Record a single state-field transition for ``instance``.

    Creates and returns one ``StateTransitionLog`` row. Safe to call inside an
    existing ``transaction.atomic()`` block — the insert is part of whatever
    transaction the caller is already in.

    Args:
        instance: The model instance whose field has just changed.
        field_name: Name of the field that transitioned (e.g. ``"status"``).
        before: The field value immediately *before* the transition.
        after: The field value immediately *after* the transition.

    Returns:
        The newly created ``StateTransitionLog`` instance.
    """
    content_type = ContentType.objects.get_for_model(instance)
    log = StateTransitionLog.objects.create(
        content_type=content_type,
        object_id=instance.pk,
        field_name=field_name,
        state_before=before,
        state_after=after,
    )
    logger.info(
        "Transition logged: %s #%s %s %r → %r (log pk=%s)",
        content_type,
        instance.pk,
        field_name,
        before,
        after,
        log.pk,
    )
    return log
