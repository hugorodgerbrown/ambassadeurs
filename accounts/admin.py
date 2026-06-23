"""Admin registration for the accounts app."""

from django.contrib import admin

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    """Admin for the Account profile model."""

    list_display = ["user", "phone", "preferred_language", "created_at"]
    search_fields = ["user__email", "user__username", "phone"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]
