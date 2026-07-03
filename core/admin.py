"""Admin registration for the core app."""

from typing import Any, cast

from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from .models import Notification, StateTransitionLog


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


class NotificationForm(forms.ModelForm):
    """Admin form for Notification, validating the CUSTOM audience pairing.

    ``custom_group_key`` is rendered as a dropdown populated from
    ``settings.CUSTOM_NOTIFICATION_GROUPS`` (plus a blank choice) rather than
    free text, since it must name a key that actually exists in code.
    """

    custom_group_key = forms.ChoiceField(
        required=False,
        choices=(),
        help_text=_("Required when audience is Custom group; ignored otherwise."),
    )

    class Meta:
        model = Notification
        fields = [
            "content",
            "priority",
            "enabled",
            "starts_at",
            "ends_at",
            "is_dismissible",
            "audience",
            "custom_group_key",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Populate custom_group_key choices from settings at form build time."""
        super().__init__(*args, **kwargs)
        group_keys = sorted(settings.CUSTOM_NOTIFICATION_GROUPS.keys())
        cast(forms.ChoiceField, self.fields["custom_group_key"]).choices = [
            ("", "—"),
            *[(key, key) for key in group_keys],
        ]

    def clean(self) -> dict[str, Any]:
        """Enforce the audience / custom_group_key pairing.

        When audience is CUSTOM, custom_group_key is required and must name a
        key in settings.CUSTOM_NOTIFICATION_GROUPS. For any other audience,
        custom_group_key is forced blank so a stale key can never silently
        apply if the audience is later changed away from CUSTOM.
        """
        cleaned_data = super().clean() or {}
        audience = cleaned_data.get("audience")
        custom_group_key = cleaned_data.get("custom_group_key", "")

        if audience == Notification.Audience.CUSTOM:
            if not custom_group_key:
                raise ValidationError(
                    {
                        "custom_group_key": _(
                            "A custom group is required when audience is Custom group."
                        )
                    }
                )
            if custom_group_key not in settings.CUSTOM_NOTIFICATION_GROUPS:
                raise ValidationError(
                    {
                        "custom_group_key": ValidationError(
                            _("%(key)s is not a configured custom group."),
                            params={"key": custom_group_key},
                        )
                    }
                )
        else:
            cleaned_data["custom_group_key"] = ""

        return cleaned_data


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Admin for the staff-authored site-wide notification strip (VERB-109)."""

    form = NotificationForm
    list_display = [
        "content_preview",
        "priority",
        "enabled",
        "starts_at",
        "ends_at",
        "is_dismissible",
        "audience",
        "is_active",
    ]
    list_editable = ["priority", "enabled"]
    list_filter = ["enabled", "audience", "priority", "is_dismissible"]
    search_fields = ["content"]

    @admin.display(description=_("Content"))
    def content_preview(self, obj: Notification) -> str:
        """Return the same truncated content preview used by to_string()."""
        preview = obj.content.strip().replace("\n", " ")
        if len(preview) > 50:
            preview = preview[:47] + "..."
        return preview

    @admin.display(description=_("Active"), boolean=True)
    def is_active(self, obj: Notification) -> bool:
        """Expose the derived is_active property as a boolean admin column."""
        return obj.is_active
