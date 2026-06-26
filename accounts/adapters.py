# allauth adapters.
#
# Enforce CLAUDE.md invariant 5: email addresses are normalised to a canonical
# form at every entry point before storage and lookup.

from allauth.account.adapter import DefaultAccountAdapter

from core.emails import normalise_email


class AccountAdapter(DefaultAccountAdapter):
    """Account adapter that normalises email addresses on the way in."""

    def clean_email(self, email: str) -> str:
        """Return the email normalised after the default validation."""
        cleaned: str = super().clean_email(email)
        return normalise_email(cleaned)
