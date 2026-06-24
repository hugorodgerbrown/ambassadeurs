"""Admin registration for the public app."""

from django.contrib import admin

from .models import FormDownload


@admin.register(FormDownload)
class FormDownloadAdmin(admin.ModelAdmin):
    """Admin for FormDownload.

    Browsable by day via date_hierarchy so programme staff can see the
    download-rate trend over the registration period.
    """

    list_display = ["__str__", "created_at"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "created_at"
