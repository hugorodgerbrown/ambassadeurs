"""Split settings package.

Select the active module with ``DJANGO_SETTINGS_MODULE`` —
``config.settings.development`` (default) or ``config.settings.production``.
Both import everything from ``base`` and override the environment-specific
pieces. See CLAUDE.md "Conventions".
"""
