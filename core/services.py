# Core service functions shared across the project.
#
# record_transition is the single entry point for writing StateTransitionLog
# rows. It is intentionally generic — it works with any concrete model instance
# and any field name — so every state machine in the project funnels through
# one place. Call it inline from transition service functions inside an existing
# transaction.atomic() block; never from Django signals (CLAUDE.md).

import logging

from django.contrib.contenttypes.models import ContentType
from django.db import models

from .models import StateTransitionLog

logger = logging.getLogger(__name__)


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
