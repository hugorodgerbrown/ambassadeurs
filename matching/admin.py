"""Admin registration for the matching app."""

from django.contrib import admin

from .models import Match, Registration


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """Admin for Registration."""

    list_display = [
        "user",
        "role",
        "prior_pass",
        "status",
        "priority",
        "preferred_location",
        "created_at",
    ]
    list_filter = ["role", "status", "prior_pass", "preferred_location"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    raw_id_fields = ["user"]
    readonly_fields = [
        "accepted_terms",
        "terms_accepted_at",
        "created_at",
        "updated_at",
    ]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Admin for Match."""

    list_display = [
        "pk",
        "ambassador_registration",
        "referee_registration",
        "status",
        "expires_at",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = [
        "ambassador_registration__user__email",
        "referee_registration__user__email",
    ]
    raw_id_fields = ["ambassador_registration", "referee_registration"]
    readonly_fields = [
        "ambassador_accepted_at",
        "referee_accepted_at",
        "declined_by",
        "declined_at",
        "no_show_reported_by",
        "no_show_reported_at",
        "created_at",
        "updated_at",
    ]
