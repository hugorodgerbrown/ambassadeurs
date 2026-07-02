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
`Registration.requeue`) as the reference implementation.

### The boundary rule

**Model method** — mutates *only* its own object's state, in memory, and is
the **sole guard** on whether the transition is legal from the object's
current state:

- Validates that `self`'s current state is a legal source for the requested
  transition, and raises `core.exceptions.StateTransitionError` immediately if
  not — **fail hard, low in the stack**. This is the only place the condition
  is checked; nothing upstream re-validates it (see "No double-checking"
  below).
- Never calls `.save()`.
- Never touches another object (no cross-object queries, no related-object
  writes).
- Never fires a side effect (no email, no `record_transition`, no signal).
- Always `return self`, so calls chain: `match.expire().save(update_fields=[...])`.

**Service function** — owns everything that crosses an object or the process
boundary:

- The `select_for_update()` lock and the `transaction.atomic()` block.
- Calling `.save()` with an explicit `update_fields`.
- Calling `core.services.record_transition` to write the audit-log row.
- Coordinating *across* objects (e.g. deciding the ambassador's outcome is
  independent of, but sequenced with, the referee's).
- Scheduling side effects (email) via `transaction.on_commit`.
- **Catching `StateTransitionError` high**, only where a benign, expected
  race makes "skip" the correct response (see `expire_lapsed_matches` below).
  Elsewhere it is left to propagate to the caller — a service function must
  not pre-check the same condition the model method already guards.

### `StateTransitionError` (`core/exceptions.py`)

A single shared exception type, used by every model state-mutation method:

```python
class StateTransitionError(Exception):
    def __init__(self, current: str, proposed: str, obj: object = None) -> None:
        self.current = current
        self.proposed = proposed
        self.obj = obj
        ...
```

It carries the current and proposed state values (and, optionally, the
offending object) as attributes, so both the raised exception and any log line
that catches it can report exactly which transition was rejected, without
string-parsing a message. It replaces the plain `ValueError` used in the first
cut of this pattern.

### Applied to the expiry transition

```
expire_lapsed_matches(cutoff)     # sweep: select PKs, lock, isolate exceptions
  -> expire_match(match)          # per-match orchestration (no pre-check)
       -> Match.expire()          # model: guards source state, raises
                                   #   StateTransitionError, or sets EXPIRED
                                   #   in memory (no save)
       -> record_transition(...)  # audit log
       -> handle_lapsed_participants(match)
            -> handle_lapsed_participant(registration, kept_faith)  # per side
                 -> Registration.requeue(priority=1) / Registration.pause()
                 -> requeue_to_front(reg) / pause_registration(reg)  # lock+save
                 -> transaction.on_commit(send_..._notification)
```

`requeue_to_front` and `pause_registration` (the existing service functions
used by the decline and no-show paths too) keep their signatures unchanged;
they now delegate their pure mutation to the model method
(`locked.pause().save(update_fields=["status"])`,
`locked.requeue(priority=1).save(update_fields=["status", "priority"])`) while
continuing to own the lock, save, and in-memory sync of the caller's instance.

### Model methods validate their own source state

Both `Match.expire()` and `Registration.pause()` guard the transition's
legality from inside the model method — not just `Match.expire()`.
`Registration.pause()` only accepts `VERIFIED` as a source state (decline and
expiry-non-response both act on a `VERIFIED` registration — VERB-74 / ADR
0013); any other source (`PAUSED`, `SUSPENDED`, `UNVERIFIED`, `WITHDRAWN`)
raises `StateTransitionError`. `Registration.requeue()` has no illegal source
state to guard — a kept-faith party is always `VERIFIED` when it is called —
so it has no precondition check, only the mutation.

### No double-checking (`expire_match` / `expire_lapsed_matches`)

An earlier draft of `expire_match` re-checked `match.status not in (PROPOSED,
PENDING)` before calling `Match.expire()`, returning `False` on a skip. That
duplicated the condition `Match.expire()` already guards — two places
asserting the same fact, with the model method's own `ValueError`
(as it was then) unreachable from this caller. This is now corrected:
`expire_match` performs no pre-check. It calls `match.expire()` directly and
lets `StateTransitionError` propagate; it no longer returns a bool (there is
nothing to report — it either succeeds or raises). The sweep,
`expire_lapsed_matches`, is the one place that *needs* to treat an illegal
transition as a skip (a benign concurrency race: another worker or an
accept/decline changed the match's status between the candidate-PK query and
this loop's lock), so it is the layer that catches:

```python
try:
    with transaction.atomic():
        match = Match.objects.select_for_update()...get(pk=pk)
        expire_match(match)
        expired_count += 1
except StateTransitionError as exc:
    logger.debug("Skipping match pk=%s: no longer expirable (%s)", pk, exc)
except Exception:
    logger.exception("Error expiring match pk=%s; skipping", pk)
```

`StateTransitionError` is caught specifically (debug-level, not counted as a
failure) ahead of the broad `except Exception` (error-level, counted as a
failure) — this is "fail hard low, catch high", applied precisely at the one
layer that has a legitimate reason to treat the failure as routine.

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

- `Match.expire()`, `Registration.pause()`, and `Registration.requeue()` are
  independently unit-testable: construct an instance, call the method, assert
  the in-memory field (and, for the two guarded methods, that an illegal
  source state raises `StateTransitionError` with the right `.current` /
  `.proposed`) and that nothing was written to the database.
- The sweep's per-match exception isolation and locking are visibly separated
  from the transition logic itself — `expire_match` can be read and reasoned
  about without the surrounding `try/except`/`select_for_update()` noise, and
  contains no state-guard logic to duplicate.
- `handle_lapsed_participant` collapses the previous duplicated
  ambassador/referee if/else into one role-agnostic function, tested once
  against both roles.
- `StateTransitionError` gives every model state-mutation method a common,
  attribute-carrying exception type rather than each raising an ad hoc
  `ValueError` with only a formatted message — callers that need to
  distinguish "illegal transition" from "some other failure" (as
  `expire_lapsed_matches` does) can catch it specifically.
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
- Catching `StateTransitionError` in `expire_lapsed_matches` means a caller
  reading only the sweep function needs to know that `Match.expire()` (two
  layers down, inside `expire_match`) is where the exception originates.
  Documented here and in the `expire_match/expire_lapsed_matches` docstrings
  to keep that link visible.

## Follow-up work

- **Done.** The other four transitions have since been migrated to this
  model/service shape, each growing a pure `Match` (and, where needed,
  `Registration`) model method that guards its own source state and raises
  `StateTransitionError`, with the service function retaining the
  lock/save/`record_transition`/notification responsibilities and not
  re-checking the guarded condition:
  - `record_acceptance` (accept) → `Match.accept` — VERB-101.
  - `record_decline` (decline) → `Match.decline` — VERB-102.
  - `withdraw_acceptance` → `Match.withdraw_acceptance` — VERB-103.
  - `report_no_show` (no-show/cancel) → `Match.cancel` plus
    `Registration.suspend` — VERB-104.

  All five match transitions (expire, accept, decline, withdraw-acceptance,
  no-show/cancel) now follow the boundary rule end-to-end.
- Adopting a package such as `django-side-effects` to formalise the
  `transaction.on_commit` notification-dispatch pattern is deferred to a
  separate ticket and its own ADR — it is a larger, cross-cutting change
  (touching every `transaction.on_commit(functools.partial(...))` call site in
  `matching/services.py`) and out of scope for this transition-decomposition
  ticket.
