# 0003 — Facebook login on the registration pages

Status: accepted (VERB-8)

## Context

VERB-8 requires that people can register using their Facebook login, from the
registration pages. The allauth Facebook provider was wired in VERB-6; VERB-2/3 built
the email-based registration flow that creates a passwordless user.

## Decisions

- **One registration flow, two entry points.** Rather than a separate social-signup
  flow, the Facebook button starts allauth's OAuth with `next` pointing back to the
  same registration page. After authentication the user returns logged-in and finishes
  the *same* form — we only collect the role-specific fields (price category, location,
  attestation), not name/email again.

- **`register_participant` gains an optional `user`.** When present (the
  just-authenticated case) it reuses that user and keeps their name current; when
  absent (the email case) it creates/reuses a passwordless user as before. The
  duplicate-in-season check keys on the user when authenticated, else on the email.

- **The Facebook button is guarded.** `templates/includes/_facebook_button.html` uses
  allauth's `{% get_providers %}` and only renders when a Facebook `SocialApp` is
  configured. Without credentials (dev/CI) the button simply does not appear, so pages
  never error and `provider_login_url` is never called unconfigured.

- **The OAuth round-trip is not re-tested.** It belongs to allauth and needs real
  credentials. Tests cover what we own: the authenticated registration path (simulated
  with a logged-in client), the email field being dropped when authenticated, and the
  button being hidden with no provider.

## Consequences

Account linking/unlinking is already reachable from the account page (VERB-4 →
allauth `socialaccount_connections`). Enabling Facebook in an environment is a matter
of adding a `SocialApp` (via env/admin); no code change is needed for the button to
appear.
