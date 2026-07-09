# ADR 0022 — Voluntary tip: Stripe Checkout flow with free-tier gating

**Status:** Accepted
**Date:** 2026-07-09
**Ticket:** VERB-110

---

## Context

ADR 0014 introduced `billing.Payment`, the prepaid registration deposit: a
money flow whose outcome is slaved to the match state machine (HELD →
CAPTURED on mutual accept / REFUNDED on season end or good-faith cancel /
FORFEITED on post-accept no-show). That deposit can be zero (`fee_chf == 0`)
for the earliest registrants in the free tier.

Free-tier registrants have no financial skin in the game. There is a product
need to give them a way to make a voluntary contribution ("tip") — a
thank-you payment with no matching implications — at some point after their
match completes. This is a second, distinct Stripe money flow.

Paid-tier registrants are excluded from the tip audience: they have already
made a mandatory financial commitment, and a second payment on the same flow
would be confusing.

## Decision

**Introduce `billing.Tip`**, a separate model from `billing.Payment`.

### Model

`Tip` is an audit row for one voluntary contribution. Key design choices:

- `registration` FK uses `SET_NULL` on delete so the audit row survives
  account deletion, mirroring `Payment` (VERB-88).
- No unique constraint on `registration` — terminal rows accumulate as
  history, mirroring `Match` and `Payment`.
- Stripe identifiers only (`stripe_customer_id`, `stripe_payment_intent_id`,
  `stripe_refund_id`) — never raw card data.
- **Status is two-state** (`PAID` / `REFUNDED`) — there is no HELD/pending
  phase. A `Tip` row is only ever inserted after Stripe confirms money moved,
  so `PAID` is the correct initial state (contrast with `Payment.Status.HELD`,
  which records "funds collected, match outcome still pending").
- `REFUNDED` is staff-initiated via the Stripe dashboard; no in-app
  transition exists at this scope.
- `message` (max 280 chars, optional) holds the tipper's free-text "say
  something nice" note. Staff-only — never shown to the counterpart or in
  user-facing output.

### Collection flow

1. **`create_tip_checkout_session`** — creates a Stripe hosted Checkout
   session (`mode="payment"`, card + TWINT). Sets `metadata.purpose == "tip"`
   and records `registration_pk`, `amount_chf`, and `message` so the webhook
   can reconstruct the context without an extra DB query.
2. **No idempotency key** on `create_tip_checkout_session`: unlike the
   deposit flow, a registrant may legitimately start multiple sessions with
   different amounts; a fixed key with changed params makes Stripe error.
3. **`record_tip_paid`** — called by both the success-redirect view
   (`tip_return`) and the webhook; idempotent on `stripe_payment_intent_id`.
4. **Webhook dispatch**: `billing.services.checkout.handle_checkout_completed`
   routes on `metadata.purpose == "tip"` → `record_tip_paid`; any other
   value (including absent, which is the deposit path) →
   `finalize_paid_registration`.

### Idempotency guard

`record_tip_paid` is check-then-create. Unlike the deposit flow, there is no
outer `select_for_update()` lock to serialise concurrent calls (the deposit
path locks the registration row at `finalize_paid_registration`; for a tip,
which does not transition the registration, there is no equivalent anchor). A
DB `UniqueConstraint` on `stripe_payment_intent_id` (conditional on
non-blank) is the race guard: a concurrent insert from a racing webhook retry
and `tip_return` call raises `IntegrityError`, caught and turned into a
re-fetch of the winning row — the race degrades to idempotency, never a
duplicate.

### Audience gate

The tip page (`public.views.tips.tip_page`) and its POST handler
(`tip_start`) are login-required and enforce `registration.fee_chf == 0`,
raising `Http404` for paid-tier registrants. The gate is view-layer only; a
staff member can create a `Tip` row in the Django admin for any registration.

The page is built in isolation (VERB-110) and is not yet linked from any nav
or journey page. A follow-up ticket mounts it on the confirmed-match flow.

## Consequences

- Adding `Tip` does not alter `Payment`, the matching engine, or any state
  machine. A tip never sets `Registration.status` or `Match.status`.
- The `Tip` row is created in `PAID` state immediately on payment
  confirmation; there is no pending or held phase.
- Concurrency safety relies on the DB constraint rather than a
  `select_for_update()` lock; this is correct given the absence of a
  registration-state transition to serialise around.
- Stripe processing fees on tips are not recovered — accepted at this scope.
