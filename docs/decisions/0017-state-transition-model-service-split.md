# ADR 0017 — Model/service split for state transitions

**Status:** Accepted
**Date:** 2026-07-02
**Ticket:** VERB-100

---

## Context

`matching.services.expire_lapsed_matches` had grown to do seven jobs in one
loop: select the candidate PKs, lock each match, re-check the idempotency
guard, mutate `Match.status`, save it, write the `StateTransitionLog` row,
decide the per-side outcome (re-queue-to-front vs. pause), mutate and save
each `Registration`, and queue the appropriate notification email. All of this
lived inside a single `for pk in candidate_pks:` block, indistinguishable at a
glance between "pure state mutation" and "the process/DB/email boundary".

The other four match transitions (`record_acceptance`, `record_decline`,
`withdraw_acceptance`, `report_no_show`) follow the same undifferentiated
shape: each function opens its own `transaction.atomic()`, locks the row,
mutates fields directly, saves, and calls `record_transition` inline. There is
no reusable notion of "what a state transition *is*" independent of the
locking/persistence/notification machinery around it — every transition
re-derives the same pattern by hand, and a unit test of "does EXPIRED get set
correctly" cannot be written without also exercising a `select_for_update()`
lock, a real save, and (via `transaction.on_commit`) email dispatch.

## Decision

Establish a **model-logic / business-logic boundary** for state transitions,
starting with the expiry transition (`Match.expire`, `Registration.pause`,
`Registration.requeue_to_front`) as the reference implementation.

### The boundary rule

**Model method** — mutates *only* its own object's state, in memory:

- Never calls `.save()`.
- Never touches another object (no cross-object queries, no related-object
  writes).
- Never fires a side effect (no email, no `record_transition`, no signal).
- Always `return self`, so calls chain: `match.expire().save(update_fields=[...])`.
- Raises `ValueError` on an invalid transition, mirroring the guard style
  already used in `record_acceptance` (`Cannot accept match pk=...: status is
  ..., expected PROPOSED or PENDING.`).

**Service function** — owns everything that crosses an object or the process
boundary:

- The `select_for_update()` lock and the `transaction.atomic()` block.
- Calling `.save()` with an explicit `update_fields`.
- Calling `core.services.record_transition` to write the audit-log row.
- Coordinating *across* objects (e.g. deciding the ambassador's outcome is
  independent of, but sequenced with, the referee's).
- Scheduling side effects (email) via `transaction.on_commit`.

### Applied to the expiry transition

```
expire_lapsed_matches(cutoff)     # sweep: select PKs, lock, isolate exceptions
  -> expire_match(match)          # per-match: idempotency re-check, orchestration
       -> Match.expire()          # model: status -> EXPIRED, in memory, no save
       -> record_transition(...)  # audit log
       -> handle_lapsed_participants(match)
            -> handle_lapsed_participant(registration, kept_faith)  # per side
                 -> Registration.requeue_to_front() / Registration.pause()
                 -> requeue_to_front(reg) / pause_registration(reg)  # lock+save
                 -> transaction.on_commit(send_..._notification)
```

`requeue_to_front` and `pause_registration` (the existing service functions
used by the decline and no-show paths too) keep their signatures unchanged;
they now delegate their pure mutation to the model method
(`locked.pause().save(update_fields=["status"])`) while continuing to own the
lock, save, and in-memory sync of the caller's instance.

### Inversion of control for "now"

`MatchQuerySet.lapsed()` took no arguments and read `timezone.now()`
internally. It now takes a required `cutoff: datetime` parameter, and
`expire_lapsed_matches(cutoff: datetime)` threads it through. "Now" is read
once, at the top — the `expire_matches` management command calls
`expire_lapsed_matches(cutoff=timezone.now())`. This makes the queryset (and
the sweep) a pure function of its arguments, which is what makes the new unit
tests possible without monkeypatching `django.utils.timezone`.

## Consequences

**Positive:**

- `Match.expire()`, `Registration.pause()`, and `Registration.requeue_to_front()`
  are independently unit-testable: construct an instance, call the method,
  assert the in-memory field and that nothing was written to the database.
- The sweep's per-match exception isolation and locking are visibly separated
  from the transition logic itself — `expire_match` can be read and reasoned
  about without the surrounding `try/except`/`select_for_update()` noise.
- `handle_lapsed_participant` collapses the previous duplicated
  ambassador/referee if/else into one role-agnostic function, tested once
  against both roles.
- The pattern is now demonstrated end-to-end on one transition, giving a
  concrete template for the follow-up tickets below rather than an abstract
  rule.

**Negative / trade-offs:**

- One more layer of indirection to read for a given transition (model method →
  service function → sweep). This is the intended cost of testability and is
  consistent with the project's existing service-function convention (CLAUDE.md
  "No Django signals for side effects").
- `handle_lapsed_participant` must be called by its bare module-level name
  (not captured as a default argument or local alias) so that
  `unittest.mock.patch.object(matching.services, ...)` continues to intercept
  it in tests — a convention to preserve when extending this pattern.

## Follow-up work (not in this ticket)

- The other four transitions — `record_acceptance` (accept), `record_decline`
  (decline), `withdraw_acceptance`, and `report_no_show` — will be refactored
  to the same model/service shape in follow-up tickets. Each currently mutates
  `Match` fields directly inside its own `transaction.atomic()` block; each
  should grow a corresponding pure `Match` model method (e.g. `Match.accept_side`,
  `Match.decline`, `Match.withdraw_acceptance`, `Match.cancel_for_no_show`) with
  the service function retaining the lock/save/`record_transition`/notification
  responsibilities.
- Adopting a package such as `django-side-effects` to formalise the
  `transaction.on_commit` notification-dispatch pattern is deferred to a
  separate ticket and its own ADR — it is a larger, cross-cutting change
  (touching every `transaction.on_commit(functools.partial(...))` call site in
  `matching/services.py`) and out of scope for this transition-decomposition
  ticket.
