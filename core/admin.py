"""Admin registration for the core app."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import StateTransitionLog


@admin.register(StateTransitionLog)
class StateTransitionLogAdmin(admin.ModelAdmin):
    """Read-friendly admin for StateTransitionLog.

    All fields are read-only — log rows are append-only and must not be edited
    through the admin. A richer display (linking to the target instance, etc.)
    is deferred to VERB-22.
    """

    list_display = [
        "pk",
        "content_type",
        "object_id",
        "field_name",
        "state_before",
        "state_after",
        "created_at",
    ]
    list_filter = ["content_type", "field_name"]
    search_fields = ["field_name", "state_before", "state_after"]
    readonly_fields = [
        "content_type",
        "object_id",
        "field_name",
        "state_before",
        "state_after",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Prevent manual creation of log rows through admin."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Prevent editing log rows through admin."""
        return False
