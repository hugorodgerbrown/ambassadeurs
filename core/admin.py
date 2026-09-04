"""Admin registration for the core app."""

from typing import Any, cast

from csp.admin import CspReportAdmin, CspRuleAdmin
from csp.models import CspReport, CspReportQuerySet, CspRule
from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from .csp import unsafe_source
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

    ``design`` is likewise rendered as a dropdown populated from
    ``settings.NOTIFICATION_DESIGNS`` (VERB-123) — the model field itself is a
    plain ``CharField`` with no ``choices=``, since Django evaluates
    model-level choices at import time, before settings are guaranteed
    configured.
    """

    custom_group_key = forms.ChoiceField(
        required=False,
        choices=(),
        help_text=_("Required when audience is Custom group; ignored otherwise."),
    )
    design = forms.ChoiceField(
        choices=(),
        help_text=_(
            "Controls the banner's colours; see settings.NOTIFICATION_DESIGNS."
        ),
    )

    class Meta:
        model = Notification
        fields = [
            "content",
            "design",
            "weight",
            "enabled",
            "starts_at",
            "ends_at",
            "is_dismissible",
            "audience",
            "custom_group_key",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Populate custom_group_key/design choices from settings at build time."""
        super().__init__(*args, **kwargs)
        group_keys = sorted(settings.CUSTOM_NOTIFICATION_GROUPS.keys())
        cast(forms.ChoiceField, self.fields["custom_group_key"]).choices = [
            ("", "—"),
            *[(key, key) for key in group_keys],
        ]
        design_keys = sorted(settings.NOTIFICATION_DESIGNS.keys())
        cast(forms.ChoiceField, self.fields["design"]).choices = [
            (key, key) for key in design_keys
        ]

    def clean(self) -> dict[str, Any]:
        """Enforce the audience / custom_group_key pairing and design validity.

        When audience is CUSTOM, custom_group_key is required and must name a
        key in settings.CUSTOM_NOTIFICATION_GROUPS. For any other audience,
        custom_group_key is forced blank so a stale key can never silently
        apply if the audience is later changed away from CUSTOM. Separately,
        design must name a key in settings.NOTIFICATION_DESIGNS (VERB-123).
        """
        cleaned_data = super().clean() or {}
        audience = cleaned_data.get("audience")
        custom_group_key = cleaned_data.get("custom_group_key", "")
        design = cleaned_data.get("design", "")

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

        if design and design not in settings.NOTIFICATION_DESIGNS:
            raise ValidationError(
                {
                    "design": ValidationError(
                        _("%(key)s is not a configured notification design."),
                        params={"key": design},
                    )
                }
            )

        return cleaned_data


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Admin for the staff-authored site-wide notification strip (VERB-109)."""

    form = NotificationForm
    list_display = [
        "content_preview",
        "design",
        "weight",
        "enabled",
        "starts_at",
        "ends_at",
        "is_dismissible",
        "audience",
        "is_active",
    ]
    list_editable = ["design", "weight", "enabled"]
    list_filter = ["enabled", "audience", "design", "is_dismissible"]
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


# ---------------------------------------------------------------------------
# CSP rule guard (Invariant 10, SKI-171)
# ---------------------------------------------------------------------------
#
# core.csp.reject_unsafe_csp_rule is the actual guarantee — it fails closed on
# every write path. These two overrides exist so the admin *explains* itself
# instead of 500ing: a field error on the form, and a warning message on the
# report-conversion action. Both re-check rather than trusting the caller, so
# neither is load-bearing on its own.


class CspRuleForm(forms.ModelForm):
    """CspRule form that rejects the sources Invariant 10 withholds."""

    class Meta:
        model = CspRule
        # Named explicitly rather than "__all__" (ruff DJ007): the field list is
        # upstream's, so an added field should be a decision here, not a silent
        # inheritance.
        fields = ("directive", "value", "enabled")

    def clean_value(self) -> str:
        """Reject a value that normalises to an unsafe source.

        Named for the ``value`` field — unrelated to ``CspRule.clean_value``,
        the model classmethod that does the normalising this check reads.
        """
        value = cast(str, self.cleaned_data["value"])
        if banned := unsafe_source(value):
            raise ValidationError(
                _(
                    "%(value)s is not permitted (Invariant 10). Allow the "
                    "specific origin, or fix the inline style or script that "
                    "needs it."
                )
                % {"value": banned}
            )
        return value


admin.site.unregister(CspRule)
admin.site.unregister(CspReport)


@admin.register(CspRule)
class GuardedCspRuleAdmin(CspRuleAdmin):
    """The package's rule admin with Invariant 10 enforced on the form."""

    form = CspRuleForm


@admin.register(CspReport)
class GuardedCspReportAdmin(CspReportAdmin):
    """The package's report admin, minus the one-click route to an unsafe rule.

    ``convert_report`` calls ``CspRule.objects.create`` directly, so the upstream
    action would hit the pre_save guard and 500. A violation whose blocked_uri
    is the literal "inline" normalises to ``'unsafe-inline'`` — the commonest
    report there is — so this is the path most likely to be taken by accident.
    Those reports are left alone and named in a warning; the rest convert as
    usual.
    """

    @admin.action(description="Add new CSP rule for selected violations.")
    def add_rule(self, request: HttpRequest, queryset: CspReportQuerySet) -> None:
        """Convert the safe selections, and say why the others were skipped."""
        unsafe_pks = [r.pk for r in queryset if unsafe_source(r.blocked_uri)]
        if unsafe_pks:
            self.message_user(
                request,
                _(
                    "Skipped %(count)d violation(s) that would add an unsafe "
                    "source to the policy (Invariant 10). Fix the inline style "
                    "or script instead."
                )
                % {"count": len(unsafe_pks)},
                "warning",
            )
        remaining = queryset.exclude(pk__in=unsafe_pks)
        if remaining.exists():
            super().add_rule(request, remaining)
