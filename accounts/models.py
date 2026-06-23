# Accounts app models.
#
# Auth uses the default Django ``User`` (CLAUDE.md "Authentication"). The Account
# model has been removed — participant attributes (phone, preferred_language)
# now live directly on ``matching.Registration`` (OneToOneField to User).
# Admin-only users have a User but no Registration.
#
# This module is intentionally minimal; it exists so the app is importable.
