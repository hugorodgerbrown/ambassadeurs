# ADR 0012 — Replace django-allauth with a first-party magic-link login

**Status:** Accepted
**Date:** 2026-06-28
**Ticket:** VERB-46

---

## Context

Login previously ran through `django-allauth`: an email/code path and a Facebook
OAuth button. Two problems drove the replacement:

1. **Session-bound login links.** allauth's login-by-email-code completes only in
   the browser that requested it. A user who opens the email on a different device
   (phone requested, laptop received the email, or vice versa) cannot complete login.
   Cross-device login is a baseline expectation.

2. **Allauth carries significant surface area for a feature the project only used
   partially.** The `EmailAddress` model, `AccountMiddleware`, `django.contrib.sites`,
   `SITE_ID`, a custom adapter, and the Facebook `SocialApp` machinery were all live
   in production settings to support one login path and one social provider.

The project already owns a signed-token system in `accounts/tokens.py`
(`django.core.signing` with per-purpose salts, used for registration confirmation
and match-action links). A login token is a natural extension of that pattern: a
URL containing the token works on any device, in any browser, without session
state.

Facebook social login was also dropped at the same time (owner decision, VERB-46).
The product launches through the Verbier Facebook community but does not require a
Facebook account — the Facebook group is a marketing channel, not an auth
dependency.

## Decision

Remove `django-allauth` entirely. Replace it with a first-party magic-link flow
implemented in `accounts/views.py`, `accounts/tokens.py`, and
`accounts/services.py`.

### Login journey

| Step | Path | Behaviour |
|------|------|-----------|
| Request | `GET /account/login/` | Email form — one field, one button. |
| Submit | `POST /account/login/` | Redirect to link-sent page regardless of whether the address is registered (no enumeration). Email a magic link if the address matches an active user. |
| Sent | `GET /account/login/sent/` | Static confirmation. Under `DEBUG`, the link is surfaced on-page. |
| Land | `GET /account/login/<token>/` | Validate token; show "Sign in as you@example.com" + Confirm button. **Does not log in** — prefetch-safe. Invalid/expired → error page (HTTP 400). |
| Confirm | `POST /account/login/<token>/` | Re-validate token, call `django.contrib.auth.login` with `ModelBackend`, redirect to `accounts:detail`. |
| Logout | `POST /account/logout/` | Call `logout()`, redirect to `public:home`. |

### Token design

- **Salt:** `_LOGIN_SALT = "accounts.login"` — distinct from `_CONFIRM_SALT` and
  `_MATCH_SALT` so tokens cannot be replayed across purposes (Invariant 6).
- **Payload:** `{"user_pk": <int>}` only. No email or role in the token.
- **Expiry:** `LOGIN_TOKEN_MAX_AGE = 3600` (1 hour).
- **Idempotent within window.** The token is not invalidated on first use. Re-POST
  of the Confirm form logs in again rather than erroring. This is intentional: the
  window is short (1 hour) and single-use enforcement would require server-side
  state (a nonce table or cache entry), which adds complexity for negligible
  security benefit in this context.
- **Cross-device.** The token is in the URL; no session state is required. The link
  works on any device or browser that receives it.

### allauth removal

- `django-allauth` removed from `pyproject.toml`, `uv.lock`, and `tox.ini` deps.
- `accounts/adapters.py` deleted. Email lowercasing (Invariant 5) moves into the
  login request handler via `core.emails.normalise_email` and into registration form
  `clean_email` methods.
- `django.contrib.sites` and `SITE_ID` removed from settings (they were only
  present for allauth).
- `AccountMiddleware` removed from `MIDDLEWARE`.
- `AUTHENTICATION_BACKENDS` reduced to `["django.contrib.auth.backends.ModelBackend"]`.
- All `ACCOUNT_*` and `SOCIALACCOUNT_*` settings removed.
- `LOGIN_URL = "accounts:login"` and `LOGIN_REDIRECT_URL = "accounts:detail"` added
  to settings.
- `email_verified` on the account page is now derived as
  `registration is not None and registration.status != UNVERIFIED`, replacing the
  former allauth `EmailAddress.verified` check.
- Templates `templates/account/` and `templates/socialaccount/` deleted.
  New templates under `templates/accounts/` (the project-owned namespace).

## Consequences

**Positive:**

- Cross-device login works: the magic link is a URL, not a session-bound code.
- Full control of token lifetime, error copy, and UX without allauth customisation.
- Fewer installed apps, no `EmailAddress` model, no `SocialApp`, no `sites`
  framework. The Django admin is simpler.
- The token pattern is already established in the codebase; the login token slots in
  without new infrastructure.

**Negative / trade-offs:**

- Facebook social login is gone. Users who previously authenticated via Facebook
  must use the email magic-link. This is a deliberate product decision (VERB-46).
- The login token is reusable within its 1-hour window. An attacker who intercepts
  the link can use it until it expires. This is the same risk as any email-delivered
  credential; the mitigation is the short window and transport-level email security.
- ADR 0003 (Facebook registration flow) is now superseded. The Facebook button and
  `_facebook_button.html` partial are deleted.

## Migration

No database migration is needed. The allauth tables (`account_emailaddress`,
`socialaccount_*`) simply stop being created for new installs. Pre-launch — no
production DB exists — so existing data is not a concern. Local developers should
drop and recreate their development database after pulling this change.
