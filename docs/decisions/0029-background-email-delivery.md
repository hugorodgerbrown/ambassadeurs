# ADR 0029 — Deliver transactional email on a background thread after commit

**Status:** Accepted
**Date:** 2026-09-26
**Ticket:** SKI-178 (amends [ADR 0018](0018-adopt-django-side-effects-for-notification-dispatch.md))

---

## Context

Every `POST /account/login/` that sends a magic link took 1.4–2.1 s in
production (Render request logs, 25–26 Sep 2026), against 20–70 ms for the
surrounding GETs. Two POSTs ended as 499, with the client closing the
connection at 761 ms and 2,099 ms, which matches users tapping submit again.
fivebar reported ≈1.8 s TTFB on `/fr/account/login/sent/`, because browser TTFB
for the redirect target includes the POST before it.

The cost was the SMTP send in `core.emails.send_templated_email`: connect, TLS,
authenticate, send. That send ran inside the request on every email path:

- login (`accounts.services.send_login_email`)
- registration confirmation and resend (`send_confirmation_email`)
- the already-registered sign-in link (`send_already_registered_email`)
- match notifications triggered from a request (propose on registration,
  accept, decline, no-show)

Gunicorn runs its default single sync worker, so each send also blocked every
other request for its duration. Production set no `EMAIL_TIMEOUT`, so a hung
SMTP server could hold that worker indefinitely.

ADR 0018 describes its dispatch as "same-process and synchronous relative to the
request (deferred only to `on_commit`)". `transaction.on_commit` runs the
callback when the outermost atomic block commits, or at once when there is no
atomic block. Both happen before the view returns its response. On its own,
`on_commit` protects correctness (a rolled-back write sends no email). It does
not remove any latency.

## Decision

Split rendering from delivery inside `send_templated_email`, the one email
chokepoint:

1. **Render in the caller's thread.** Subject, text and HTML are rendered as
   before, so the active or overridden language and the context do not change.
   The URL-language rule from ADR 0025 still applies.
2. **Defer delivery to `transaction.on_commit`.** This applies to every email,
   including the accounts flows that had no deferral before. A registration
   that rolls back never sends its confirmation.
3. **Deliver on a background thread when `EMAIL_SEND_IN_BACKGROUND` is true.**
   The setting defaults to false in `base.py` and to true in `production.py`.
   The thread is a plain `threading.Thread`:
   - It touches no database and no translation state, because the message is
     already rendered.
   - It is non-daemon, so a graceful gunicorn restart lets an in-flight send
     finish.
   - It catches and logs its own exceptions, with the template name only and
     never the address.
4. **Set `EMAIL_TIMEOUT`** (default 10 s) in production, so one send cannot
   hold its thread, or a worker during shutdown, for more than that.

Callers keep their signatures and return values. The DEBUG `debug_login_url` /
`debug_verify_url` shortcuts, the rate limits, the always-redirect
no-enumeration behaviour and the token salts do not change.

### Alternatives considered

- **Django 6 Tasks with a database-backed worker.** Durable, with retries, but it
  needs a new dependency, a queue table and a paid Render background-worker
  service. That is not justified for mail whose loss the user can fix: a lost
  login or confirmation link is re-requested from the same page.
- **Only setting `EMAIL_TIMEOUT`.** This caps the worst case but leaves the
  normal 1.5–2 s in every POST.
- **Making each call site asynchronous.** Scattered, and the matching handlers
  would need the same change. The chokepoint covers every path at once.

## Consequences

**Positive:**

- The login, registration and match-action POSTs return without waiting on
  SMTP, and the single worker is no longer blocked by mail.
- Every email is now `on_commit`-deferred, not only the matching ones.
- A hung SMTP server is bounded by `EMAIL_TIMEOUT`.

**Negative / trade-offs:**

- **Delivery is best-effort.** A worker killed hard (SIGKILL, OOM, or the end of
  the graceful timeout) loses any send in flight. Nothing retries it. For login
  and confirmation links the user can request again. A lost match notification
  is covered by the account page, which shows the match regardless of email.
- **SMTP errors no longer reach the request in production.** They appear as
  `Failed to send templated email name=…` log lines, not as a 500. This is
  intended: the POST was already answered, and no-enumeration means the user
  was never told whether an email went out.
- **Tests that assert on `mail.outbox`** must run the on-commit callbacks
  (`TestCase.captureOnCommitCallbacks(execute=True)`), because pytest-django
  wraps each test in a transaction. Tests, dev and e2e keep
  `EMAIL_SEND_IN_BACKGROUND` off, so delivery is synchronous once the callbacks
  run, and Playwright can read Mailpit straight after the POST.

If sends are lost in practice, the next step is the Tasks-plus-worker
alternative above. Its call site is the same `_dispatch` function.
