# allauth adapters.
#
# Enforce CLAUDE.md invariant 5: email addresses are normalised to lowercase at
# every entry point before storage and lookup.

from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """Account adapter that lowercases email addresses on the way in."""

    def clean_email(self, email: str) -> str:
        """Return the email lowercased after the default validation."""
        cleaned: str = super().clean_email(email)
        return cleaned.lower()
