# ADR 0011 — Two independent state machines: Registration.Status and Match.Status

**Status:** Accepted  
**Date:** 2026-06-28  
**Ticket:** VERB-44

---

## Context

Before this ADR, `Registration.Status` served double duty:

- **Pool standing** — whether a registration was awaiting a match (`WAITING`),
  had an active match (`MATCHED`), or had completed the season (`CONFIRMED`).
- **Match progress proxy** — flipping to `MATCHED` when a match was proposed and
  back to `WAITING` when it expired or was declined.

This coupling caused several problems:

1. **Eligibility logic was spread across two models.** The matching engine had
   to check `Registration.status == WAITING` everywhere; views had to check
   both `Registration.status` and `Match.status` to determine what to display.
2. **Re-queuing required two writes.** Expiring or declining a match required
   transitioning both `Match.status` → EXPIRED/DECLINED and both
   `Registration.status` → WAITING, and these had to be done atomically.
3. **The intermediate "one side accepted" state was invisible.** There was no
   persisted match state between PROPOSED (neither accepted) and ACCEPTED (both
   accepted). The ambassador's `accepted_at` timestamp implied intermediate
   progress, but the match remained PROPOSED until both accepted — making the
   state ambiguous in the DB and hard to reason about.
4. **Post-accept no-shows (`ABANDONED`) were confusingly named** — the word
   implies an incomplete process rather than a no-show after a successful match.

## Decision

Split into two independent state machines:

### Registration.Status (pool standing only)

| Value | Meaning |
|-------|---------|
| `UNVERIFIED` | Registered but email not yet confirmed (formerly `PENDING`). |
| `VERIFIED` | In the pool, available to be matched. Replaces `WAITING`. |
| `WITHDRAWN` | Voluntarily left the pool. |
| `SUSPENDED` | Removed by the system (two flakes or a reported no-show). |

`MATCHED` and `CONFIRMED` are removed. A registration's pool-standing status
never changes because a match was proposed — it stays `VERIFIED` until the
season ends, it withdraws, or it is suspended. Pool availability is enforced
by `RegistrationQuerySet._without_active_match()`, which excludes registrations
that hold an active match (PROPOSED, PENDING, or ACCEPTED).

### Match.Status (match progress)

| Value | Meaning |
|-------|---------|
| `PROPOSED` | Engine paired them; neither side has accepted yet. |
| `PENDING` | One side has accepted; waiting for the other. **New in VERB-44.** |
| `ACCEPTED` | Both sides accepted; contact details revealed. Terminal success. |
| `DECLINED` | One side declined. Terminal; both re-queue. |
| `EXPIRED` | Contact window lapsed without both accepting. Terminal. |
| `CANCELLED` | Previously ACCEPTED; one party filed a post-accept no-show. Replaces `ABANDONED`. |

The PROPOSED → PENDING transition fires when the first party accepts.
PENDING → ACCEPTED fires when the second party accepts.
PENDING → PROPOSED fires when the accepting party withdraws (see ADR 0010).

All three transitions are recorded in `StateTransitionLog`.

## Consequences

**Positive:**

- Eligibility queries are simpler: `Registration.objects.verified()` returns
  all `VERIFIED` registrations, and `_without_active_match()` narrows to those
  without an active match. No `MATCHED`/`CONFIRMED` to handle.
- The intermediate one-sided-accept state is now a real, logged DB value
  (`PENDING`) rather than a timestamp-only implication.
- Re-queuing after decline/expiry is one write (Registration.priority/flake_count
  update) rather than two (registration status + match status).
- `CANCELLED` is a clearer name for the post-accept no-show terminal state.
- The two state machines can evolve independently.

**Negative / trade-offs:**

- Pool availability now requires a join (`_without_active_match()` does a
  subquery/exclude). Previously a simple `status=WAITING` filter sufficed.
  The cost is acceptable for the pool sizes expected at launch.
- Existing data required a migration (`0007_verb44_update_status_enums`) to
  remap old values. The migration is forward and backward safe.

## Migration

`matching/migrations/0007_verb44_update_status_enums.py` handles:

- `Registration.PENDING` → `UNVERIFIED`
- `Registration.WAITING` → `VERIFIED`
- `Registration.MATCHED` → `VERIFIED`
- `Registration.CONFIRMED` → `VERIFIED`
- `Match.ABANDONED` → `CANCELLED`
- `Match` rows in `PROPOSED` with a single accepted-at timestamp → `PENDING`
