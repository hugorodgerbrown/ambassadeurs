# 0004 — Verify-first registration flow

Status: accepted (VERB-9)

Supersedes the role-first registration flow from decision 0001 / VERB-2/3/8.

## Context

Registration originally put the role in the URL and collected everything on one page,
creating the user on submit. VERB-9 requires the **email to be verified first** (via a
signed link or Facebook) before any role/details are collected.

## Decisions

- **Three steps: verify → choose role → details.**
  1. `/register/` captures an email and emails a signed link (or offers Facebook).
  2. `/register/verify/<token>/` consumes the link, creates/reuses the passwordless
     user, logs them in.
  3. `/register/details/` (`@login_required`) lets them pick a role and submit the
     role-specific fields, loaded on demand via HTMX
     (`/register/details/form/`, guarded by `require_htmx`).

- **Signed-link tokens** (`accounts/tokens.py`) use `django.core.signing` with a
  dedicated salt (single-purpose) and a 24h `max_age` (expiring) — CLAUDE.md invariant
  6. They carry only the email; the user is created when the token is consumed.

- **Email is marked verified in allauth.** `get_or_create_participant_user` records a
  verified `EmailAddress`, so the token flow and the Facebook flow leave the same
  consistent verified-email state.

- **Role is a session hint, confirmed at step 4.** The homepage CTAs pass `?role=`,
  remembered in the session and used to pre-load the matching details fragment via
  HTMX; the user can still pick the other role. This keeps the "am I referrer or
  referee?" guidance without locking the choice into the URL.

- **The authenticated path is reused.** `RegistrationForm(user=…)` and
  `register_participant(user=…)` from VERB-8 are exactly what the details step needs —
  no new model or duplicate user creation.

## Consequences

The old `/register/<role>/` one-page form and `templates/public/register.html` are
removed. Login after a token click uses Django's `ModelBackend` explicitly (multiple
backends are configured). Dev/CI email goes to the console / locmem backend; the OAuth
round-trip stays allauth's and is not re-tested.
