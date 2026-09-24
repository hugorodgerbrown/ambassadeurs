# ADR 0028 — Read-only staff impersonation via django-impersonate

**Status:** Accepted
**Date:** 2026-09-24
**Ticket:** SKI-176

---

## Context

Program staff answer questions from participants ("what does my account page
say?", "why can't I see my partner's phone?") without being able to see the
page the participant sees. The match page, account page and queue position all
depend on the participant's registration and match state, so the admin's
record view is not a substitute.

## Decision

Add [django-impersonate](https://pypi.org/project/django-impersonate/) so a
superuser can browse the public site as a participant.

- **Superusers only** (`REQUIRE_SUPERUSER`). Superusers cannot be impersonated
  (`ALLOW_SUPERUSER` stays at its default, off).
- **Read-only** (`READ_ONLY`). Any non-GET request during an impersonation
  returns 405. Accept, decline, no-show report, cancel and rejoin are all
  POSTs, and each one emails or re-queues the real partner; staff seeing a
  page must not be able to act on it. Logout is also a POST, so staff leave
  through the banner's "Stop viewing" link.
- **Time-limited** (`MAX_DURATION` = 1 hour), after which the middleware ends
  the session.
- **Audited.** Every session is an `impersonate.ImpersonationLog` row
  (impersonator, target, start, end), read-only in the admin. The package
  writes these through its own Django signals; that is internal to a third-party
  app and does not breach the project's "no signals for side effects" rule,
  which governs our own code.
- **Visible.** `templates/base.html` renders a banner on every public page while
  `request.impersonator` is set.
- **Not in analytics.** `PostHogPageviewMiddleware` skips impersonated requests
  so staff browsing is not attributed to the participant. The middleware sits
  after `LeadSourceMiddleware` so a staff visit's utm params are never persisted
  against the participant.

### The split admin host

The admin is served from `ADMIN_HOST` (ADR 0022) and the session cookie is
host-only, so an impersonation started on the admin host has no effect on the
public site. The impersonate routes are therefore mounted on the **public**
URLconf (`config/urls_public.py`, unprefixed), and the admin's "View as" link
(`RegistrationAdmin.view_as`) is built by `core.impersonation.impersonate_start_url`:
relative on a single host, absolute on `BASE_URL` when `ADMIN_HOST` is set.

On a split-host deployment the superuser must therefore also be signed in on the
public site, via the magic link sent to their own email address. The login flow
does not honour `?next=`, so a first click on "View as" lands on the login page;
after signing in, click the link again.

Widening `SESSION_COOKIE_DOMAIN` to the parent domain would remove that step but
would also send the admin session cookie to the public host, undoing part of
what ADR 0022 isolates. Rejected.

## Consequences

- Invariant 1 is unaffected: staff see only what the impersonated participant
  would see, and already have all of it in the admin.
- The package's own `/impersonate/list/` and `/impersonate/search/` pages are
  reachable by superusers and are unstyled. They are not linked from anywhere.
- On a single host (local development), admin POSTs are also refused while an
  impersonation is active, because the read-only check precedes the package's
  `^admin/` URI exclusion. Stop the impersonation before editing in the admin.
