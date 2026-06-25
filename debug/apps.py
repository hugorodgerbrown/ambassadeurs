"""App configuration for the debug app.

This app provides DEBUG-only test-data helper views (create a counterpart
registration, force accept/decline, log in as the counterpart). Every view is
guarded by ``require_debug``; the app itself is always installed but is inert
in production because all routes return 404 when ``settings.DEBUG`` is false.
"""

from django.apps import AppConfig


class DebugConfig(AppConfig):
    """Configuration for the debug app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "debug"
