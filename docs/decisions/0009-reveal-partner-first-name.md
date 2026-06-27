# ADR 0009 — Reveal the partner's first name before mutual accept

**Status:** Accepted
**Date:** 2026-06-27
**Ticket:** Match page redesign

---

## Context

The original [Invariant 1](../../CLAUDE.md) was absolute: a matched user could not
see the other party's **name, email, or phone** until both had accepted. The match
page therefore showed only role labels and a status pill for each side — no name.

The match-page redesign shows each party the other's **first name** and a
role-tinted initials avatar from the `PROPOSED` state (the "roster" component and
the header copy — "You and Léa have been paired…"). Showing a name makes the
pairing read as a real person rather than an anonymous slot, which the redesign
treats as load-bearing for the accept decision.

This conflicts with the letter of Invariant 1. The choice was made deliberately
(not by accident of implementation): reveal the first name early, keep the
contact channel closed.

## Decision

Reveal the partner's **first name and initials** on the match page from the
`PROPOSED` state onward, for every viewer perspective. Continue to hide **email
and phone** until the match reaches `ACCEPTED` (both parties accepted).

Invariant 1 is re-scoped from "name, email, or phone" to "**email or phone**".
The protected data is the *contact channel* — what someone would need to actually
reach the other party off-app — not the first name.

Mechanically:

- `public.views._match_context` adds `partner_name` (the counterpart's first name)
  and a `roster` structure carrying each side's first name + initials. These are
  populated in **all** states.
- The counterpart `Registration` (which exposes email and phone in the contact
  card) is still added to the context **only** when `match.status == ACCEPTED`,
  unchanged from before.
- When a party declines, their `User`/`Registration` row is deleted, so their
  name is no longer available; the roster and copy fall back to a generic
  "your partner" label.

## Consequences

- The privacy guarantee now protects email/phone, not the first name. The
  security-auditor and QA checks key off the re-scoped Invariant 1.
- A first name plus role is low-sensitivity and cannot be used to contact someone
  off-app; the data-minimisation posture for the *contact channel* is unchanged.
- A declined party's name vanishes with their account, so the non-decliner never
  retains it — the early reveal does not outlive the match.
- The full name (first + last) and the contact rows remain confined to the
  confirmed state's contact card.
