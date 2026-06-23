"""App configuration for the core app."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuration for the shared-abstractions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
