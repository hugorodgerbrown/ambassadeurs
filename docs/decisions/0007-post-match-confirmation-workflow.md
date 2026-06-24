# ADR 0007 — Post-match confirmation workflow

**Status:** Accepted
**Date:** 2026-06-24
**Ticket:** VERB-16

---

## Context

The matching engine (ADR 0005) proposes a `Match` the instant an eligible pair
exists and emails both parties a bare "you have been matched" notification with
**no PII and no action link**. Everything after that point is undefined: there is
no way for a party to accept or decline, no contact-window expiry, no contact
reveal, and no handling of the failure modes the program owner already knows will
dominate the real world — people commit to a partner and then vanish.

This ADR drives out the **post-match workflow**: what happens between a match
being proposed and the pair either succeeding (revealed to each other, off-app
application proceeds) or failing (one side ghosts, declines, or accepts then lets
the other down). It is a workflow decision, not an implementation; the coding
work is decomposed into sub-tickets of VERB-16.

### What already exists

- `Match.Status`: `PROPOSED → ACCEPTED | DECLINED | EXPIRED`.
- `Match.expires_at`: the contact-window deadline (`CONTACT_WINDOW_HOURS`, 72h).
- `Registration.priority`: integer, higher = nearer the front. Nothing writes it.
- `Registration.Status`: `WAITING → MATCHED → CONFIRMED | WITHDRAWN`.
- `send_match_notification()`: no-PII, no-link email to both parties.

### What is missing

Per-party accept tracking, accept/decline/report endpoints and tokens, the
contact reveal, the contact-window expiry sweep, asymmetric re-queue, the flaking
record, suspension, and the post-accept "let-down" report.

---

## Decision

### Terminology

- **Flake** — a party fails the other *after a match is proposed*. Two kinds:
  a **non-response** (the contact window lapses without that party acting) and a
  **post-accept no-show** (both accepted, contact was revealed, then the party
  vanished before the pair met/applied). A **decline** is *not* a flake — it is
  an honest "no".

### Match state machine (extended)

```
                 ┌───────── both accept ─────────► ACCEPTED ──┐
                 │                                   (reveal)  │
   PROPOSED ─────┤                                             │ no-show reported
                 │                                             ▼
                 ├── one declines ──► DECLINED            ABANDONED
                 │
                 └── window lapses ─► EXPIRED
```

- A new terminal state **`ABANDONED`** is added: a mutually-accepted match where
  one party was reported as a post-accept no-show.
- `ACCEPTED` is no longer strictly terminal-immutable: it can move to `ABANDONED`
  via a no-show report. `DECLINED` and `EXPIRED` remain terminal.

### Per-party response tracking

`Match` gains nullable per-side typed columns so the engine knows *who* has
acted:

- `ambassador_accepted_at`, `referee_accepted_at` (`DateTimeField`, null until
  that side accepts).
- `declined_by` + `declined_at`: which side declined and when. `declined_by`
  is a `Match.Side` enum value (`AMBASSADOR` / `REFEREE`) — a full FK to
  `Registration` is over-built here because the two parties are always
  retrievable from the match itself; the enum is sufficient and cheaper.
- `no_show_reported_by` + `no_show_reported_at`: same rationale — `Side` enum,
  not a FK.

The accused no-show is the *other* registration on the match — derived, not
stored.

### Transition audit log

Every state transition is **recorded for replay** — dispute resolution (the
no-show path is trust-immediately, adjudicated by staff from the records) and
debugging both depend on a reliable history of what changed when. This audit was
previously provided by `django-fsm-log` riding the FSM `pre_transition` /
`post_transition` signals; we recreate it here without the FSM dependency and
without signals (CLAUDE.md "no Django signals for side effects").

A generic log model lives in `core/` (it is cross-cutting, not Match-specific).
Each transition is recorded **after** it is applied, inline from the transition
service, inside the same atomic transaction as the field change:

- `target` — `GenericForeignKey` (content type + object id) to the changed
  instance, so the log is model-agnostic.
- `field_name` — the field that transitioned (e.g. `"status"`).
- `state_before`, `state_after` — the values either side of the transition.
- timestamp — `BaseModel.created_at` is the post-transition instant; no separate
  field is added.

A helper `record_transition(instance, field_name, *, before, after)` is called
from each transition function (`record_acceptance`, `record_decline`,
`expire_match`, `report_no_show`). Because it is generic it also logs
`Registration.status` transitions (`WAITING → MATCHED → CONFIRMED | SUSPENDED |
WITHDRAWN`), giving one ordered history across both models. Recording inline (not
via a signal) means an `.update()` or admin edit that skips the service is
*visibly* unlogged rather than silently audited into a false history. The log is
surfaced read-only in admin (VERB-22).

### Happy path — both accept

1. `PROPOSED` match notification now carries a **signed, single-purpose,
   expiring match-access token** (Invariant 6) that authenticates the holder for
   that match's action page. Accept / decline / report are CSRF-protected POSTs
   gated on the authenticated user owning a side of the match. HTMX fragment
   endpoints are guarded by `require_htmx` (Invariant 7).
2. Each side accepts within the contact window → sets its `*_accepted_at`.
3. When the **second** accept lands: `Match → ACCEPTED`, both
   `Registration → CONFIRMED`, and the counterpart's **name, email, and phone**
   are revealed to each party (Invariant 1 — first and only point of reveal).
   Both leave the pool. A "match confirmed — here is how to reach your partner"
   email goes to both, under each recipient's `preferred_language` (Invariant 8).

### Failure — decline (honest "no")

- One party declines → `Match → DECLINED`, record `declined_by` / `declined_at`.
- **Decliner**: re-queued `WAITING`, sent to the **back** (`priority -= 1`). A
  decline costs queue position (matching is scarce) but is **not** a flake — no
  flake is recorded and it never counts toward suspension.
- **Other party** (left hanging): re-queued `WAITING`, keeps their place near the
  **front** (`priority += 1`).

### Failure — non-response (contact window lapses)

A periodic **expiry sweep** finds `PROPOSED` matches past `expires_at` and
transitions them to `EXPIRED`, then re-queues asymmetrically based on who acted:

- **Exactly one accepted**: the accepter keeps their place near the front
  (`priority += 1`); the non-responder goes to the **back** (`priority -= 1`) and
  a **flake is recorded** (`flake_count += 1`).
- **Neither accepted**: both are non-responders → both to the back, both flake.

(If both had accepted the match would already be `ACCEPTED`, never `EXPIRED`.)

### Failure — post-accept no-show ("let-down")

After `ACCEPTED` and the contact reveal, a confirmed party can report that their
partner vanished, from their match page (CSRF-protected POST / `require_htmx`
fragment). The report is **trusted immediately** — no verification gate, because
the only thing at stake is queue position and staff can review records in admin:

- `Match → ABANDONED`; record `no_show_reported_by` / `no_show_reported_at`.
- **Reporter** → re-queued to the **front** (`WAITING`, `priority += 1`) with
  on-screen reassurance ("You're back near the front of the queue").
- **Accused (flaker)** → **removed from the pool**: `Registration.status →
  SUSPENDED`, `flake_count += 1`, and a **polite** notification email.
- **First report wins.** Once the match is `ABANDONED` the accused cannot
  counter-report (the match is terminal and they are suspended). This is the
  known cost of the trust-immediately model; staff adjudicate disputes in admin.

### Flaking record and suspension

- `Registration.flake_count` (integer, default 0) records flakes (non-responses
  and post-accept no-shows; **not** declines).
- A new `Registration.Status.SUSPENDED` marks an involuntary removal, distinct
  from voluntary `WITHDRAWN`.
- **Auto-suspend at 2 flakes.** When an increment takes `flake_count` to 2, the
  registration is set `SUSPENDED` instead of re-queued. (A post-accept no-show is
  itself an immediate removal, so the threshold is reached in practice via
  repeated non-responses.)
- `propose_match` / the eligibility querysets must exclude `SUSPENDED`
  registrations so the engine never re-matches a suspended party.

### Priority semantics

Re-queue adjusts the existing `priority` band used by `propose_match`'s ranking
(`-location_match, -priority, created_at`):

- Kept-faith / wronged party: `priority += 1` (floats above the default band; a
  repeat victim floats higher still).
- Flaker / decliner: `priority -= 1` (sinks below the default band → back).

`created_at` continues to provide FIFO ordering within a band.

### Notifications

Email only for launch, via signed links — confirming CLAUDE.md's assumed default;
no SMS/push. New emails: confirmed-with-contact-details, polite no-show notice,
and the back-in-queue reassurance (the last may be on-screen only). All under the
recipient's `preferred_language` (Invariant 8).

### Expiry sweep topology

The sweep is a management command run by a **Render scheduler service** (per
CLAUDE.md "Path to live"). It must be idempotent and transactional
(`select_for_update`) so concurrent runs cannot double-process a match.

---

## Consequences

- **New invariant surface.** Contact reveal stays bound to `ACCEPTED`
  (Invariant 1). The match-access token is single-purpose/expiring (Invariant 6);
  fragment endpoints stay `require_htmx`-guarded (Invariant 7).
- **`ACCEPTED` is no longer immutable.** Reporting/analytics must treat
  `ABANDONED` as the post-accept failure outcome distinct from `EXPIRED`/`DECLINED`.
- **Trust-immediately is abusable in the small.** A party could pre-emptively
  report the other to jump the queue. Accepted deliberately: low stakes (queue
  position only), and every report is recorded for staff review. If abuse
  materialises, revisit toward the provisional/staff-review model.
- **Scheduler dependency.** Launch now requires the Render scheduler service for
  the expiry sweep; document the topology in CLAUDE.md when that slice lands.
- **Open item deferred.** Whether the app tracks that the pair *actually applied*
  off-app (beyond the no-show report) remains out of scope; the no-show report is
  the only completion signal for launch.

---

## Coding follow-ups (sub-tickets of VERB-16)

1. Per-party accept tracking + `Match` state machine (`ABANDONED`, response
   fields, accept/decline/mutual-accept transitions + contact reveal) +
   generic `core` transition audit log (`record_transition`).
2. Asymmetric re-queue + flaking record + suspension (`flake_count`,
   `SUSPENDED`, priority bands, exclude suspended from matching).
3. Accept / decline endpoints + signed match-access token + match page +
   contact-reveal + confirmation email.
4. Contact-window expiry sweep (management command + Render scheduler service).
5. Post-accept no-show reporting + notifications + reassurance.
6. Admin & flaking reporting (surface `flake_count`, `SUSPENDED`, `ABANDONED`,
   no-show fields; a let-downs report).
