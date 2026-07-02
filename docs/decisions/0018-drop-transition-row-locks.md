# ADR 0018 — Drop the row lock from the state-transition service helpers

**Status:** Accepted
**Date:** 2026-07-02
**Ticket:** VERB-106 (deferred from VERB-100 / [ADR 0017](0017-state-transition-model-service-split.md))

---

## Context

The state-transition service helpers established by ADR 0017 all followed a
`lock a fresh copy → mutate → sync the fields back onto the caller's instance`
shape:

```python
def requeue_to_front(registration: Registration) -> None:
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.requeue(priority=1).save(update_fields=["status", "priority"])
        registration.status = locked.status
        registration.priority = locked.priority
```

The same pattern appeared in `pause_registration`, `suspend_for_no_show`,
`rejoin_queue`, `confirm_registration`, `record_acceptance`, `record_decline`,
`withdraw_acceptance`, `report_no_show`, and the per-match loop of
`expire_lapsed_matches`. The re-fetch-under-lock plus sync-back is three lines
of concurrency machinery around a one-line mutation, and it reads awkwardly.

Two mechanisms were conflated in the original design, and they are **not**
interchangeable:

- `select_for_update()` prevents a **lost update** — it serialises two
  concurrent writers on the same row.
- `refresh_from_db()` only fixes **staleness** — it re-reads current values but
  gives no concurrency guarantee.

So the question is not "lock vs. `refresh_from_db`" (they solve different
problems) but "is a lost update on a single registration/match row actually
reachable, and if so, what harm does it do?"

### The two realistic races

1. **The hourly `expire_matches` cron racing a user's accept/decline** on the
   same match. Both paths transition `Match.status` from `PROPOSED`/`PENDING`.
   Whichever commits second finds the row already in a terminal state — and the
   model methods (`Match.expire`, `Match.accept`, `Match.decline`) already
   guard their own source state and raise `StateTransitionError` from that
   terminal state. The sweep catches `StateTransitionError` as a benign skip
   (ADR 0017). So the *match* transition is protected by the state guard, not
   by the lock.

2. **Two overlapping cron runs** selecting the same lapsed-candidate PKs. Same
   outcome: the second run's `Match.expire()` raises `StateTransitionError`
   from the already-`EXPIRED` state and is skipped. The overlap is also
   unlikely in practice — the sweep is hourly and finishes in well under a
   second on the expected pool size.

The residual exposure is the **`Registration` side**: `requeue_to_front`
(`priority += 1`) is a read-modify-write with no state guard, so two writers
could in principle both read the same priority and one increment could be lost.
The blast radius is one participant's queue-priority integer being off by one —
it changes their ordering slightly, never their eligibility, never their PII,
never the 1:1 invariant. It is self-correcting on the next requeue and
invisible to the participant.

### Testing-confidence factor

The dev/test database is SQLite, which silently no-ops `SELECT ... FOR UPDATE`;
production and the e2e stack are Postgres (cf. VERB-97,
[[sqlite-masks-postgres-for-update-distinct]]). The lock was therefore never
actually exercised by the pytest/CI suite — it only did anything in prod/e2e —
so removing it loses no test coverage, and the code the suite runs is now the
same code that runs in production.

## Decision

**Drop the `select_for_update()` row lock (and the accompanying re-fetch and
sync-back) from every state-transition service helper.** The helpers now mutate
the caller's passed-in instance directly:

```python
def requeue_to_front(registration: Registration) -> None:
    registration.requeue(priority=1).save(update_fields=["status", "priority"])
```

We accept the possibility of a rare lost priority increment rather than pay the
readability and lock-contention cost of serialising every transition. If
inconsistencies are ever reported, they can be reconciled by hand — the audit
trail (`StateTransitionLog`, via `record_transition`) records every status
change. This is the "go on exception, reconcile if needed" posture, chosen over
defensive pessimistic locking that the test suite could not even verify.

The model/service boundary from ADR 0017 is otherwise unchanged: model methods
still validate their own source state and raise `StateTransitionError` (fail
hard, low); service functions still own `save`, `record_transition`,
cross-object coordination, and `transaction.on_commit` notifications. The
`StateTransitionError` catch in `expire_lapsed_matches` is retained and is now
the *sole* concurrency defence for the sweep.

### What keeps its lock

`propose_match` (the matching engine) **keeps** its
`select_for_update()` on the candidate pool. That lock does not guard a state
transition — it prevents two concurrent proposers from both selecting the same
counterpart and creating two active matches for one registration, which would
violate the **1:1-per-season invariant** (Invariant 3). Unlike a lost priority
increment, that failure is not self-correcting and has no `StateTransitionError`
to catch (a second `Match.objects.create` just succeeds). It is also outside
this ticket's scope (the transition helpers), so it is left intact. The
`RegistrationQuerySet._without_active_match` `Exists`-subquery shape (chosen so
Postgres allows `FOR UPDATE` without a `DISTINCT`) therefore still matters.

Locking in `billing/` (deposit transitions) and `public/` is likewise out of
scope and unchanged.

## Consequences

**Positive:**

- Each helper collapses to its essential one-line mutation; the model/service
  split reads cleanly without the lock/re-fetch/sync-back scaffolding.
- The test suite now runs the same code path as production (no SQLite-only
  no-op lock hiding behaviour — VERB-97).
- No behavioural change on the happy path: the helpers already mutated and
  saved; they simply do so on the passed-in instance now.

**Negative / trade-offs:**

- A concurrent writer on the same `Registration` row can lose a `priority`
  increment. Bounded, self-correcting, and invisible to participants; reconciled
  by hand from the audit log if ever reported.
- The helpers now assume the caller's instance is fresh enough. In every current
  call site the instance was fetched immediately upstream (the view's token
  lookup, or the sweep's per-match `get`), so this holds; a future caller that
  holds an instance across a long gap must re-fetch before calling.

## Follow-up work

None. The change is applied across all transition helpers at once (VERB-106) so
they stay consistent, rather than folding into VERB-101–104 as originally
mooted.
