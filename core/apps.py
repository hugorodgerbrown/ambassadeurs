"""App configuration for the core app."""

from django.apps import AppConfig
from django.db.models.signals import pre_save


class CoreConfig(AppConfig):
    """Configuration for the shared-abstractions app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        """Connect the CSP rule guard (Invariant 10, SKI-171).

        The project's only signal receiver, and deliberately so: it validates a
        third-party model written by third-party code, so there is no service
        function of ours to put it in. See core.csp for the full rationale.
        """
        from csp.models import CspRule

        from .csp import reject_unsafe_csp_rule

        pre_save.connect(
            reject_unsafe_csp_rule,
            sender=CspRule,
            dispatch_uid="core.reject_unsafe_csp_rule",
        )
