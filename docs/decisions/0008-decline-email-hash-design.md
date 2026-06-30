# ADR 0008 — Decline email hash design

**Status:** Superseded by ADR 0013 (VERB-74, 2026-06-30)
**Date:** 2026-06-26
**Ticket:** VERB-41

---

## Context

When a match is declined, the decliner's `User` row may be deleted later (the
user re-registers under a new account, or requests erasure).  To surface prior
decline history at re-registration time — specifically to set
`Registration.prior_decline_count` without retaining the raw email address —
the system stores a hash of the decliner's email on the `Match` row as
`Match.declined_by_email_hash`.

The hash must support a single operation: **equality lookup** ("does this new
registrant's email match a prior decliner?").  This is a **blind index**, not a
password hash.  The design choices follow from that constraint.

---

## Decision

### Deterministic by design

The hash must be deterministic (same normalised email → same digest) so a
cross-registration lookup can succeed.  A per-row salt (e.g. the original
User's primary key) is therefore unusable: the original `User` is deleted, and
the re-registrant gets a new primary key that would produce a different hash.
A salt derivable from the email alone (e.g. `HMAC(email, email)`) adds no
real entropy against a targeted-email attacker, because the "salt" is the
input itself.

### Pepper via `EMAIL_HASH_SECRET`

The hash is keyed with a secret pepper stored in the environment
(`EMAIL_HASH_SECRET`, via `python-decouple`), never in the database.  The
algorithm is HMAC-SHA256 (implemented in `core.hashing.hash_email`).

Defence model: a database-only leak exposes only the hashes.  Without the
pepper, an attacker cannot verify whether a specific email address is present,
because HMAC requires the key.

Residual risk: an attacker who obtains both the database and the secret can
perform a targeted confirmation attack ("is email X present as a prior
decliner?") — this is inherent to any equality-searchable scheme.  The datum
is low-sensitivity (it records that someone declined a match, not their
credentials or identity), so this residual risk is accepted.

### Future hardening

Rotating `EMAIL_HASH_SECRET` per season naturally expires all prior decline
hashes (old hashes no longer match), which is the correct behaviour: a
decliner's history should not follow them across seasons.  Per-row salting
cannot achieve this without storing the salt, which does not fit the
deleted-user use case above.

### Normalisation dependency

`hash_email` delegates normalisation to `core.emails.normalise_email` (see
VERB-41).  All stored email values in the database are produced by the same
function.  This guarantees that `hash_email(email_from_form)` always matches
`hash_email(email_stored_at_registration_time)`, regardless of whitespace or
casing variations in the raw input.

---

## Consequences

- `Match.declined_by_email_hash` stores a 64-character lowercase hex digest.
- `core.hashing.hash_email` is the single function that produces hashes for
  storage and for lookup.  It must not be bypassed.
- All entry points that store or look up email addresses must pass through
  `core.emails.normalise_email` before calling `hash_email`, ensuring the
  digest is consistent.
- `EMAIL_HASH_SECRET` must be present in the environment; its absence raises
  an `AttributeError` at hash time (fail-fast).
- If `EMAIL_HASH_SECRET` is rotated, existing `declined_by_email_hash` values
  become unmatchable — old decline history is effectively cleared.  This is
  acceptable and is the recommended per-season rotation strategy.
