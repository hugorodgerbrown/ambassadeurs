"""Admin registration for the core app."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from .models import StateTransitionLog


@admin.register(StateTransitionLog)
class StateTransitionLogAdmin(admin.ModelAdmin):
    """Read-friendly admin for StateTransitionLog.

    All fields are read-only — log rows are append-only and must not be edited
    through the admin. The ``target_link`` method links to the admin change page
    of the log's target instance for quick navigation.
    """

    list_display = [
        "pk",
        "content_type",
        "object_id",
        "target_link",
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

    @admin.display(description=_("Target"))
    def target_link(self, obj: StateTransitionLog) -> str | SafeString:
        """Return an anchor to the target instance's admin change page.

        Uses ``content_type`` and ``object_id`` to build the admin URL — both
        are system-derived values, not user-supplied free text, so
        ``format_html`` is safe here (Invariant 4). Returns an em-dash when no
        admin change view is registered for the content type.
        """
        app_label = obj.content_type.app_label
        model = obj.content_type.model
        try:
            url = reverse(
                f"admin:{app_label}_{model}_change",
                args=[obj.object_id],
            )
        except NoReverseMatch:
            return "—"
        return format_html(
            '<a href="{}">{} #{}</a>', url, obj.content_type, obj.object_id
        )
