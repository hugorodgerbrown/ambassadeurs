# ADR 0014 — Deferred matching and a tiered prepaid registration fee

**Status:** Accepted
**Date:** 2026-07-01
**Ticket:** VERB-82
**Parent:** VERB-81

---

## Context

The Ski Parrainage launch (epic VERB-81) reshapes two aspects of how the
season opens:

1. **Registration and matching are decoupled in time.** Today, `propose_match`
   fires synchronously the moment an eligible counterpart is available
   (ADR 0005), so matching effectively starts the instant registration opens.
   For launch, registration should open early so the queue builds on **both**
   sides, while matching itself is held until a fixed date (~1 November) —
   avoiding a lopsided early rush where the first few referees or ambassadors
   get matched against a still-thin opposite queue.
2. **Registration carries an escalating prepaid fee.** To discourage
   speculative, no-intention-to-follow-through sign-ups (and to fund the
   administrative overhead of running the program), registration is no longer
   free. The fee escalates by registration date — free at first, then CHF 5,
   10, and 20 as the season approaches — collected as a **prepaid deposit**
   rather than a matching or success fee.

This ticket is the foundation both downstream spines depend on: the written
decision (this ADR) and the env-driven configuration plus a small helper to
read it (`matching/pricing_config.py`). **No engine or payment behaviour
changes here** — nothing consumes the new config yet, so matching and
registration behave exactly as today. VERB-83 (the matching gate) and VERB-84
(the fee-tier lookup on `Registration`) build on the contract set here.

This mirrors the existing single-season-via-env pattern (ADR 0005:
`REGISTRATION_OPENS_AT` / `CONTACT_WINDOW_HOURS`), read via `python-decouple`
in `config/settings/base.py` and parsed fail-safe at the use site
(`matching/services.py::is_registration_open`).

---

## Decision

### Deferred matching

Registration and matching become decoupled: a registration can enter the pool
(status `VERIFIED`) at any time within the registration window, but the
synchronous `propose_match` trigger is gated on a new moment,
`MATCHING_OPENS_AT` (an env-configured full ISO 8601 datetime, read via
`matching.pricing_config.matching_opens_at()`). Before that moment, eligible
registrations simply accumulate in the queue; once it passes, the existing
priority-then-FIFO ranking (ADR 0005) drains the built-up queue exactly as it
does today — no new ranking rule is introduced. Wiring the gate into
`propose_match` itself is VERB-83's job; this ticket only lands the config and
the parsing helper.

### Tiered prepaid fee

Registration carries a one-off prepaid fee, escalating by the calendar date on
which the participant registers. The schedule is env-configured
(`REGISTRATION_FEE_TIERS`, a comma-separated `YYYY-MM-DD:rappen` list) and
resolved via `matching.pricing_config.fee_rappen_for(on_date)`, which returns
the amount of the last threshold on or before `on_date` (0/free before the
first threshold). The amount is **locked in at registration time** — a
participant's fee does not change retroactively if they register before a
later threshold takes effect. **Both** ambassadors and referees pay; the fee
is symmetric across roles. Stamping the resolved fee onto `Registration` at
signup is VERB-84's job.

### Prepaid-fee lifecycle

The deposit's outcome is **slaved to the existing state machines** (ADR 0011
`Registration.Status` / `Match.Status`, and ADR 0013's `PAUSED` state) —
deliberately not a parallel rulebook:

| Outcome | Trigger | Deposit action |
|---|---|---|
| `CAPTURED` | Match reaches `Match.Status.ACCEPTED` (mutual accept) | Deposit is captured/kept |
| `REFUNDED` | Season ends with no successful match, or a good-faith cancel before matching | Deposit is refunded |
| `FORFEITED` | Post-accept no-show (`Registration.Status.SUSPENDED` via `suspend_for_no_show`) | Deposit is forfeited |

No new registration- or match-level status values are introduced for this;
the deposit's own lifecycle (a `Payment`-shaped concept, deferred to VERB-85)
reads the existing `Registration.Status` / `Match.Status` transitions to decide
capture vs. refund vs. forfeit.

### Lenient PAUSE is a deliberately accepted trade

ADR 0013's `PAUSED` state — reached via decline or contact-window expiry — is
**self-recoverable** and, per the table above, keeps the deposit **refundable**
rather than forfeit. This is intentionally lenient: a participant could in
principle register, let their match lapse without responding (or decline it),
and still get their deposit back, repeating this to avoid ever actually
completing a match while never losing money. We accept this abuse path at
launch rather than solving it up front — only a post-accept no-show
(`SUSPENDED`) forfeits. If lapse-then-refund abuse is observed in practice, the
follow-up is to make expiry-induced `PAUSE` forfeit the deposit too (a policy
change, not an architecture change, since the lifecycle is already keyed off
`Registration.Status`).

### Provider and collection timing

Payment is collected via **Stripe**, supporting both **card (CHF) and TWINT**
(the dominant Swiss mobile payment method), **at registration time** — while
the payer is present in the browser, so an inline 3-D Secure / SCA challenge
can complete synchronously. Charging a saved payment method weeks or months
later (e.g. at match time) is unreliable — cards expire, SCA mandates can lapse,
and TWINT does not support arbitrary off-session charges — so collecting
up front is the only dependable timing. Stripe integration itself
(`Payment` model, checkout flow) is VERB-85; this ADR only fixes the provider
and timing decision so VERB-85 has a settled target.

Stripe charges in its minor unit (centimes for CHF); the CHF-to-centimes
conversion happens at the Stripe API boundary in VERB-85/86 — the config and
stored values in this ticket and its downstream siblings are rappen (Swiss
minor-unit) amounts throughout, with no separate major/minor unit split to
track in this codebase.

---

## Consequences

- **Refund is the common path, not the exception.** Because most registrations
  will *not* end in a mutually-accepted match at any given moment (the pool is
  asymmetric and matching is deferred), refund logic must be built and tested
  first-class, not bolted on as an edge case.
- **Stripe keeps its processing fee on every refund.** A refunded transaction
  is not fee-free — Stripe retains its processing fee even when the full
  captured amount is returned to the payer. This erodes the already-thin
  margin on the cheap tiers (CHF 5 collected, a chunk of it gone to fees on
  refund) — a cost the program absorbs, not the participant.
  A `close_season` sweep (deferred to VERB-87/88) is needed as a backstop for
  dormant deposits that were never explicitly resolved (e.g. a participant who
  simply never returns after being paused).
- **Simpler mental model.** The deposit lifecycle piggybacks entirely on
  `Registration.Status` / `Match.Status`; there is no new state machine to
  reason about, keep in sync, or drift from the existing ones.
- **Deferred matching changes nothing about eligibility.** `is_eligible_pair`
  and the ranking rules (ADR 0005) are untouched; only the *timing* of the
  first `propose_match` call is gated.

---

## Out of scope (later tickets)

The matching gate wired into `propose_match` (VERB-83), `Registration.fee_rappen`
and stamping it at signup (VERB-84), the Stripe `Payment` model and checkout
flow (VERB-85), and all collect/refund/forfeit flows (VERB-86/87/88). This
ticket ships config, a parsing helper, and this decision record only.
