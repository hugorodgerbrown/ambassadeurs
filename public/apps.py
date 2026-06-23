"""App configuration for the public app."""

from django.apps import AppConfig


class PublicConfig(AppConfig):
    """Configuration for the public-facing site app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "public"
