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

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


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
