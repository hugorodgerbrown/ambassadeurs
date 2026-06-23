# ADR 0005 — Single-season matching engine

**Status:** Accepted  
**Date:** 2026-06-23  
**Ticket:** VERB-10

---

## Context

The original data model had five domain objects:
`User → Account → Registration → Match` (with `Season` + `PriceCategory`).
After the initial design discussions with the program owner, two simplifications
were agreed:

1. **One season at a time.** The platform runs one campaign season. There is no
   need to retain historical Season rows or model per-season price categories in
   the database — season configuration (registration window, contact window)
   belongs in the environment.

2. **Adults only for launch.** The 4 Vallées program currently has one qualifying
   tier (adult annual / seasonal pass). `PriceCategory` and the price-category
   ordering eligibility rule add code without adding domain value at this stage.

The combined effect collapses the model from five objects down to three:
`User → Registration → Match`.

---

## Decision

### Model shape

| Before | After |
|--------|-------|
| `Season` (DB table, admin-managed) | `REGISTRATION_OPENS_AT` / `REGISTRATION_CLOSES_AT` (env vars, ISO-8601) |
| `PriceCategory` (per-season ordered table) | removed |
| `Account` (1:1 to User, holds phone + language) | removed; fields moved to `Registration` |
| `Registration.account` FK | `Registration.user` OneToOneField |
| `Registration.held_prior_pass` (bool) | `Registration.prior_pass` TextChoices: `NONE / SEASONAL / ANNUAL / MONT4` |
| `Registration.discount_eligible` (bool) | removed |
| `Registration.season` / `price_category` FKs | removed |

`Registration` is now the single participant object. One registration per user
(enforced by `OneToOneField`). No unique constraint on `Match` registration FKs —
declined and expired matches accumulate as history.

### Registration window

`is_registration_open() -> bool` compares `timezone.now()` to the two
ISO-8601 strings from settings. Dev defaults (`2020-01-01` → `2099-12-31`)
keep the window always open in local development. Production overrides them
via environment variables. Parse errors fail safe (returns `False`).

### Eligibility rules (simplified)

A match may only be proposed between an eligible pair:

- **Ambassador**: `role == AMBASSADOR`, `status == WAITING`,
  `prior_pass in {SEASONAL, ANNUAL, MONT4}`.
- **Referee**: `role == REFEREE`, `status == WAITING`, `prior_pass == NONE`.
- **Location**: soft preference — the engine ranks shared `preferred_location`
  first but never gates on it.

The price-category ordering rule (`referee.category >= ambassador.category`)
is dropped because there is only one category at launch.

### Matching engine — synchronous trigger

`register_participant()` calls `propose_match()` inside the same
`transaction.atomic()` block. When a new registration arrives and an eligible
counterpart is already waiting, a `PROPOSED` `Match` is created immediately
and both registrations flip to `MATCHED`. No background worker is needed for
the trigger (a future expiry sweep needs a scheduler — that is out of scope
for this ticket).

### Ranking

Within `propose_match()`, waiting candidates are ranked:

1. **Shared `preferred_location`** — annotated flag (1 if match, 0 if not).
2. **`priority` descending** — higher priority means nearer the front
   (asymmetric flaking handling adjusts this in a future ticket).
3. **`created_at` ascending** — FIFO tiebreak.

### Match notification (Invariant 1)

`send_match_notification()` sends a single bare notification to each party
(under their `preferred_language`) that they have been matched. The body
contains **no PII** (no name, email, phone, or action link) and **no token**.
Contact details are revealed only after mutual accept, which is out of scope
for this ticket.

---

## Consequences

- **Simpler DB schema.** Three tables (User, Registration, Match) instead of
  five-plus; admin surface is reduced to two models.
- **Scarcity clarification.** CLAUDE.md previously stated "ambassadors are the
  scarce side". The owner confirmed the opposite: ambassadors are plentiful;
  **referees are scarce** (there are always more referees than ambassadors
  trying to pair). The engine and docs have been corrected.
- **Ineligible ambassadors.** The Mont 4 / special-reduction distinction
  (formerly `discount_eligible`) is now represented by `prior_pass == MONT4`.
  An ambassador with `MONT4` is fully eligible to match; the referee they take
  still benefits from the discount even if the ambassador does not. This matches
  the original intent.
- **Multi-season caveat.** If the program eventually needs to retain historical
  registrations across multiple seasons, the season scope will need to be
  reintroduced. At that point re-add a `Season` FK to `Registration` and a
  `unique_registration_per_season` constraint.
