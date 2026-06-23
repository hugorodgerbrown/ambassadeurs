"""App configuration for the matching app."""

from django.apps import AppConfig


class MatchingConfig(AppConfig):
    """Configuration for the matchmaking domain app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "matching"
