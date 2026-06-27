# ADR 0010 — Withdraw acceptance (no-penalty un-accept)

**Status:** Accepted
**Date:** 2026-06-27
**Ticket:** VERB-43

---

## Context

The post-match confirmation workflow (ADR 0007) tracks each party's response as
a nullable `Match.{ambassador,referee}_accepted_at` timestamp. The first accept
sets one timestamp and leaves the match `PROPOSED`; the second accept transitions
`PROPOSED → ACCEPTED` and reveals contact PII (Invariant 1).

Between those two events the first accepter sits in a "waiting on partner" view
with no way out short of declining — and decline is destructive (it deletes the
decliner's `User` and `Registration`, see ADR 0008) and re-queues the other
party. A participant who accepted by mistake, or who changed their mind before
the partner responded, had no clean exit. The match-page redesign surfaces a
"Withdraw my acceptance" control in that waiting state; this ADR records the
transition that backs it.

---

## Decision

Add `matching.services.withdraw_acceptance(match, registration)`: the inverse of
the *first* accept.

### Transition

It adds a `PROPOSED → PROPOSED` self-transition that clears the calling side's
`*_accepted_at` timestamp:

```
PROPOSED (this side accepted, other side not) → PROPOSED (neither accepted)
```

The viewer's display state moves from `waiting` back to `actionable`, so the
match page re-renders the accept/decline buttons.

### No penalty, no re-queue, no log row

Withdrawing is a **clean** un-accept:

- **No flake penalty.** It is distinct from a non-response flake (recorded at
  expiry by `record_flake_and_requeue`) and from a decline. The party is still
  engaged with the match; they have simply retracted a premature acceptance.
- **Nothing is re-queued.** Both registrations stay `MATCHED` against the same
  live match. `withdraw_acceptance` touches only the timestamp column.
- **No `StateTransitionLog` row.** The match never leaves `PROPOSED`, so there is
  no status transition to record. This is symmetric with the first accept, which
  also writes no log row (a log row is written only on the `PROPOSED → ACCEPTED`
  mutual-accept and on the terminal transitions).

### Guard: only valid before the partner accepts

The operation is rejected (`ValueError`) unless:

1. `match.status == PROPOSED`, **and**
2. the calling side's `*_accepted_at` is set (there is an acceptance to retract).

The PROPOSED guard is the load-bearing safety property. If both sides had
accepted, the match would already be `ACCEPTED` — a terminal, contact-revealed
state — so there is no window in which a withdrawal could un-reveal PII or
reverse a confirmed match. Once the partner accepts, the only remaining
post-accept exit is `report_no_show` (ADR 0007), not withdrawal.

The service runs inside `transaction.atomic()` with `select_for_update()`,
mirroring `record_acceptance`, so a concurrent second accept cannot interleave
with a withdrawal.

### Endpoint

`public.views.match_withdraw` mirrors `match_accept`: `@require_htmx`
(Invariant 7) + `@require_POST`, re-validates the signed token, guards that the
viewer is in the `waiting` display state, calls the service, and re-renders the
`match_actions.html` partial. A POST once the state is no longer `waiting` (e.g.
the partner accepted in the interim) is a safe no-op.

---

## Consequences

- The match state machine gains a `PROPOSED → PROPOSED` self-edge that clears one
  acceptance timestamp. It is the only transition that *removes* an acceptance.
- The audit log does not record withdrawals (no transition row). If withdrawal
  frequency ever needs measuring, it must be added explicitly; it is not
  inferable from `StateTransitionLog`.
- A participant can accept and withdraw repeatedly while the partner is
  unresponsive. This is harmless: no penalty accrues and no other party is
  affected, so no rate limiting is applied.
