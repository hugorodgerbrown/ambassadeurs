# ADR 0030 — Passkeys alongside the magic link, ported from the Titan template

**Status:** Accepted
**Date:** 2026-10-23
**Ticket:** none yet (requested in the project thread)

---

## Context

Sign-in has been a magic link only since [ADR 0012](0012-magic-link-login.md).
That works, but every return visit means waiting for an email and switching to
a mail app — on a phone, often out of the browser the person started in. The
Titan template (`hugorodgerbrown/django-titan-template`) solved the same
problem with passkeys next to its emailed link, and has run that code in
several apps. Two smaller gaps showed up at the same time:

- the login email was a bare URL, with no obvious call to action;
- the confirmation page showed the full email address, to anyone holding (or
  shoulder-surfing) the link.

## Decision

1. **Mirror Titan's passkeys rather than design new ones.** `accounts/passkeys.py`
   is Titan's `apps/accounts/passkeys.py` with only the user naming adapted
   (this app keeps the address, so password managers list the passkey under
   it); `static/js/webauthn_json.js` is copied verbatim and `passkeys.js` is
   Titan's with its copy moved into translated `data-msg-*` attributes, because
   this UI is bilingual. Library: py_webauthn (`webauthn>=3,<4`), as in Titan.
2. **Discoverable credentials, user verification required.** A passkey alone
   signs someone in, so it must be the person (Face ID, fingerprint, PIN), not
   just their device. "Sign in with a passkey" asks for no email; nothing is
   requested until the button is pressed (no conditional-mediation prompt on
   page load).
3. **Passkeys are added, never required.** Every account still starts with the
   magic link, which remains the recovery path on a new device. Passkeys are
   added and removed in the Security card on the account page. Staff can view
   and delete them in the admin, never add one.
4. **Challenges live in the session and are used once.** The session keeps the
   last five sign-in challenges so two tabs each verify against their own.
5. **WebAuthn user id is a SHA-256 of the user pk**, never the address — the
   spec forbids personal data there.
6. **The confirmation page shows a masked address** (`a•••@example.com`,
   `core.emails.mask_email`, Titan's format). It needs to say *which* account
   the link opens, not reveal the address.
7. **Sign-in emails get a bulletproof button plus the raw link**
   (`templates/email/_button.html`, `_raw_link.html`), applied to both
   magic-link emails (`login`, `already_registered`).

## Consequences

- New `accounts.Passkey` table (the accounts app's first model since
  `Account` was removed).
- `WEBAUTHN_RP_ID` / `WEBAUTHN_ORIGINS` default from `BASE_URL`. A passkey is
  bound to its host: if the site moves domain, or answers on both the apex and
  `www`, set `WEBAUTHN_ORIGINS` (and expect users to add new passkeys after a
  move).
- JSON endpoints under `/account/login/passkey/…` and `/account/passkeys/…`.
  They are POST-only, CSRF-protected and rate-limited (30 per IP per 10
  minutes, Titan's limit). Signed-out calls to the registration endpoints get
  a 401 JSON (`core.decorators.login_required_json`), not a login redirect.
- No CSP change: the scripts are same-origin and `fetch` stays on `'self'`.
- Staff impersonation is read-only, so a staff user "viewing as" someone
  cannot add or remove that person's passkeys.
- New msgids (passkey copy, "Or copy and paste this link…") render in English
  for French users until the next catalogue rebuild (ADR 0016).
