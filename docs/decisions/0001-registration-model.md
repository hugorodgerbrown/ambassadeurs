# 0001 — Registration model and the prior-season attestation

Status: accepted (VERB-2)

## Context

VERB-6 deferred the `Registration` model to its feature tickets. VERB-2 (the
ambassador registration flow) is the first to need it, and VERB-3 (referee)
mirrors it, so the model and the registration machinery are introduced here and
shared.

## Decisions

- **`Registration` FKs to `accounts.Account`, not `auth.User`.** Account holds the
  participant's non-core attributes and is the domain-facing identity; a person can
  register across seasons reusing one Account. `UniqueConstraint(season, account)`
  enforces one registration per account per season.

- **A single `held_prior_pass` boolean captures both roles' attestation.** The
  ambassador rule (returning holder) and the referee rule (genuinely new) are
  opposite faces of the same fact, so `register_participant` derives it from the
  role: ambassador → `True`, referee → `False`. The per-role wording shown to the
  user lives in the template; the form only enforces that the box is ticked.

- **Registration is passwordless and does not log the user in.** The service creates
  a `User` with an unusable password (username = lowercased email) and its Account.
  Email verification and the signed-link / Facebook login are the VERB-4 auth slice;
  registration only enrols the participant into the pool.

- **No phone collected at registration.** The ticket's field list omits it; it is
  added later on the account page. `Account.phone` stays blank until then.

- **`preferred_location` is a fixed `Resort` choice set, optional.** Location is a
  soft preference (CLAUDE.md "Match eligibility") — the engine prefers a shared
  resort but never gates on it.

- **Side effects run inline in `register_participant`** within one transaction — no
  Django signals (CLAUDE.md "Models").

## Consequences

`discount_eligible` defaults to `True`; Mont 4 / special-reduction handling and the
queue-`priority` flaking adjustment are later tickets. The view validates the role
slug (`ambassador` / `referee`) and 404s otherwise, keeping unknown roles out of the
form and templates.
