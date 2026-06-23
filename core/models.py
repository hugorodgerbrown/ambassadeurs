# Shared model abstractions.
#
# BaseModel is abstract (no concrete table). Every concrete model in the
# project inherits it and ships the full kit described in CLAUDE.md "Models":
# an explicit admin class, a to_string() method, an explicit Meta.ordering, a
# custom queryset, a factory, and tests.

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
