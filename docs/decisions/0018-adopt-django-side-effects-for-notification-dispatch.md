# ADR 0018 — Adopt `django-side-effects` for transition notification dispatch

**Status:** Accepted
**Date:** 2026-07-02
**Ticket:** VERB-105

---

## Context

Every match state transition ends by dispatching one or more notification
emails. The dispatch is uniformly written as a deferred call scheduled from
inside the service function that owns the transition:

```python
transaction.on_commit(functools.partial(send_requeued_notification, other))
```

`transaction.on_commit` is the deliberate boundary (ADR 0017): the email fires
only if the surrounding `transaction.atomic()` block commits, so a rolled-back
transition never emails anyone. There are six such call sites, spanning the five
match transitions, all in `matching/services.py`:

| # | Transition | Trigger function | Notification |
|---|-----------|-----------------|--------------|
| 1 | Propose | `propose_match` | `send_match_notification` |
| 2 | Accept (first side → `PENDING`) | `record_acceptance` | `send_partner_accepted_notification` |
| 3 | Accept (second side → `ACCEPTED`) | `record_acceptance` | `send_match_confirmed_email` |
| 4 | Decline | `record_decline` | `send_requeued_notification` |
| 5 | Expire (sweep) | `handle_lapsed_participant` | `send_requeued_notification` / `send_window_expired_notification` |
| 6 | No-show | `report_no_show` | `send_no_show_notification` + `send_requeued_notification` |

Tests drive these through `TestCase.captureOnCommitCallbacks(execute=True)`,
which runs the deferred callbacks so the email outbox can be asserted (22 uses
across `tests/matching/test_services.py`).

The pattern works, but there is no single place that answers "what fires on each
transition?" — the mapping is scattered across six `on_commit` lines interleaved
with the transition logic, and each new transition re-derives the same
`on_commit(functools.partial(...))` boilerplate by hand. ADR 0017 deferred the
question of formalising this dispatch layer to its own ticket (VERB-105); this
is that ADR.

[`django-side-effects`](https://pypi.org/project/django-side-effects/) (yunojuno,
v3.2.0) is the candidate. It resolves cleanly against Python 3.14 / Django 6.0 —
no version blocker — and is not currently a dependency.

### How the library works

Dispatch is **decorator-based and same-process** — not signals, and not an
`emit_side_effect()` call threaded through the body of the transition:

- `@has_side_effects("label")` marks the *origin* function (the transition). When
  that function returns, the library runs every handler bound to the label,
  deferring them to `transaction.on_commit` when called inside a transaction.
- `@is_side_effect_of("label")` binds a *handler* (a `send_*` function) to that
  label. Many handlers may bind to one label.
- `disable_side_effects()` is a context manager / decorator for tests that
  suppresses execution and yields the list of fired labels:
  `with disable_side_effects() as events: record_decline(...); assert events == ["match_declined"]`.
- `SIDE_EFFECTS_TEST_MODE=True` is a global kill-switch that disables all events.

The origin↔handler binding is by string label, resolved at import time from the
decorators — this is the one new piece of indirection the library introduces.

## Decision

**Adopt `django-side-effects` as the single dispatch mechanism for transition
notifications**, replacing the six hand-written
`transaction.on_commit(functools.partial(send_x, ...))` call sites.

Each transition function is decorated with `@has_side_effects("<label>")`, and
the existing `send_*` functions are decorated with `@is_side_effect_of("<label>")`
against the same label. A module-level vocabulary of transition labels (e.g.
`SIDE_EFFECT_MATCH_DECLINED = "match_declined"`) becomes the one declarative
registry of "which notifications fire on which transition." The `send_*`
functions themselves are unchanged in body; only how they are *invoked* changes.

### Why this is compatible with the "no signals for side effects" rule

CLAUDE.md bans wiring side effects through `post_save`/signals; save-time side
effects must be called inline from the relevant service function. `django-side-effects`
does **not** reintroduce that pattern:

- It does not use Django signals. Handlers are bound explicitly by decorator to a
  named origin function, not to a model's `post_save`.
- Dispatch is same-process and synchronous relative to the request (deferred only
  to `on_commit`, exactly as today), not queued or out-of-band.

The mechanism is therefore closer to the current inline-service style than to the
banned signal pattern: the trigger is still "this function ran," made explicit by
a decorator on that function, rather than "some row was saved." This assessment
rests on how the library dispatches (decorator binding at the call site's own
function), not on the library's marketing.

### Testability

The migration is a strict superset of today's test ergonomics:

- `captureOnCommitCallbacks(execute=True)` continues to work unchanged, because
  the library still defers handlers to `transaction.on_commit` inside a
  transaction. Existing outbox assertions need no rewrite.
- `disable_side_effects()` additionally lets a unit test assert *which* transition
  labels fired without executing the send — so a test can verify "declining emits
  the re-queued notification" by label, decoupled from the email body. This is not
  expressible with the current pattern.

## Consequences

**Positive:**

- One declarative registry of transition → notifications, colocated with the
  transition functions, replacing six scattered `on_commit` lines.
- Uniform dispatch across all six call sites; a new transition adds a decorator
  and a label rather than re-deriving the `on_commit(functools.partial(...))`
  boilerplate.
- Label-level test assertions via `disable_side_effects()`, on top of the existing
  outbox assertions.

**Negative / trade-offs:**

- One new pinned runtime dependency (`django-side-effects==3.2.0`), to be added
  with `uv add` and mirrored into the `tox.ini` `deps` blocks (`test`,
  `django-checks`, `mypy`) per CLAUDE.md.
- The origin↔handler binding is by string label resolved at import, one level of
  indirection above a direct `on_commit(functools.partial(...))` call. Mitigated
  by defining the labels as module-level constants next to the transitions (no
  bare string literals) so "jump to definition" still connects the two ends.
- The library must be added to `INSTALLED_APPS` and its handlers imported at
  startup for the decorators to register — an app-config wiring cost paid once.

## Follow-up work

This ADR is decision-only; it changes no production code. A single follow-up
implementation ticket — **"Apply `django-side-effects` across all five match
transitions"** — migrates all six call sites in **one pass** (not piecemeal, to
avoid two competing dispatch patterns mid-flight). That ticket owns:

- adding the `django-side-effects` runtime dependency (`uv add`) and the matching
  `tox.ini` `deps` entries, plus `INSTALLED_APPS` / handler-import wiring;
- defining the transition-label constants and decorating the six sites;
- confirming the existing `captureOnCommitCallbacks` tests still pass and adding
  `disable_side_effects()` label assertions where they add value.

## Amendment (VERB-107) — realised shape

VERB-107 implemented the follow-up in one pass. The realised shape refines two
points beyond what this ADR anticipated:

- **Labels sit on the transition function, not an intermediate marker.** Each of
  the five real transition functions in `matching/services.py` —
  `propose_match`, `record_acceptance`, `record_decline`, `expire_match`,
  `report_no_show` — is decorated directly with `@has_side_effects("<label>")`.
  The label constants (`MATCH_PROPOSED`, `MATCH_ACCEPTED`, `MATCH_DECLINED`,
  `MATCH_EXPIRED`, `MATCH_NO_SHOW`) live in the new `matching/side_effects.py`,
  imported into `services.py` — the "one declarative registry" from the
  Decision section is this module, not a separate mapping table.
- **One handler notifies one recipient, and every handler derives its
  recipient by walking the mutated `Match`** (`ambassador_registration` /
  `referee_registration`, `declined_by`, `no_show_reported_by`,
  `*_accepted_at`) rather than being passed a loose registration. A transition
  that emails two people therefore binds two `@is_side_effect_of` handlers to
  its label (e.g. `notify_ambassador_of_proposal` /
  `notify_referee_of_proposal`; `notify_ambassador_of_confirmation` /
  `notify_referee_of_confirmation`), each calling a small shared `_email_*`
  render helper carved from the pre-VERB-107 `send_*` bodies. `match_accepted`
  binds three handlers to one label — the waiting-partner nudge and both
  confirmation handlers all fire on every `record_acceptance` call and each
  guards on `match.status` (PENDING vs ACCEPTED) rather than being invoked
  conditionally by the caller.
- **`propose_match` is the sanctioned exception to "walk the argument, not
  `return_value`."** It creates the match rather than receiving it, so its two
  handlers (`notify_ambassador_of_proposal`, `notify_referee_of_proposal`) read
  the created `Match` from `return_value`. The no-match `None` case is gated by
  the library's own `run_on_exit=lambda match: match is not None`, so the
  handlers never fire when no counterpart was waiting — no defensive check is
  needed inside the handlers themselves.
- Every handler's signature ends `**kwargs`, mirroring the origin's positional
  parameters — required because the registry's `try_bind` raises
  `SignatureMismatch` (a hard error, not a silent skip) if a handler's
  signature cannot bind the origin's `*args`/`**kwargs`/`return_value`.
- `django-side-effects` ships no `py.typed` marker; `pyproject.toml` scopes
  `ignore_missing_imports` and `disallow_untyped_decorators` to the two
  modules that use the decorators (`matching.services`,
  `matching.side_effects`) rather than relaxing either setting project-wide.
