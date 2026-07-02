"""App configuration for the matching app."""

from django.apps import AppConfig


class MatchingConfig(AppConfig):
    """Configuration for the matchmaking domain app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "matching"

    def ready(self) -> None:
        """Import services and side_effects so their decorators register.

        django-side-effects (ADR 0018 / VERB-107) does not autodiscover —
        @has_side_effects / @is_side_effect_of only bind a label to a handler
        when the defining module has been imported. Importing both modules
        here, at app-ready time, guarantees every label is bound before the
        `side_effects.checks.check_function_signatures` system check runs.
        """
        from . import services, side_effects  # noqa: F401
