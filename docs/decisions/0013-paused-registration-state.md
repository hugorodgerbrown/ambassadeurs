# ADR 0013 — PAUSED registration state replaces account-deletion-on-decline

**Status:** Accepted
**Date:** 2026-06-30
**Ticket:** VERB-74
**Supersedes:** ADR 0008 (decline email-hash design)

---

## Context

The original post-match workflow (ADR 0007) handled a decliner's registration
by **deleting the User account**. This was paired with an HMAC blind-index
scheme (ADR 0008) that stored a hash of the deleted user's email in
`Match.declined_by_email_hash` so that, if the same person re-registered,
`register_participant` could look up their prior `declined_by_email_hash`
occurrences and set `Registration.prior_decline_count` accordingly.

Non-responses (contact-window expiry) were handled differently: the registration
was retained but re-queued to the back of the pool and a `flake_count` integer
was incremented. Two flakes auto-suspended the registration.

This design had several problems:

1. **Account deletion is irreversible and extreme.** A single honest "no"
   permanently deletes a person's account. If they later want to participate they
   have to re-register from scratch with no context of what happened before.
2. **The email-hash machinery is complex and fragile.** `EMAIL_HASH_SECRET` must
   be present in production, its rotation invalidates all prior hashes, and the
   code path in `register_participant` that reads prior decline counts is hard to
   reason about.
3. **Two different code paths for the same logical outcome.** Declines deleted
   the account; non-responses re-queued with a flake count. Both end with the
   same real-world result — the person is no longer in the active pool — but the
   code treated them entirely differently.
4. **The two-strike suspension model added brittleness.** Edge cases around the
   boundary (exactly 2 flakes), combined with the fact that "flake" excluded
   declines, required careful bookkeeping that was easy to get wrong.

---

## Decision

Replace account-deletion-on-decline, the email-hash machinery, and the
two-strike flake model with a single, unified **`PAUSED`** registration state.

### `Registration.Status.PAUSED`

| Value | Meaning |
|-------|---------|
| `UNVERIFIED` | Registered; email not yet confirmed. |
| `VERIFIED` | In the pool; available to be matched. |
| `PAUSED` | Out of the pool; can self-rejoin. **New in VERB-74.** |
| `WITHDRAWN` | Voluntarily left the pool permanently. |
| `SUSPENDED` | Removed by the system (post-accept no-show report). |

`PAUSED` is the soft, self-recoverable out-of-pool state. It replaces:

- the account-deletion path for declines.
- the `requeue_to_back` / `flake_count` path for non-responses.

### Transitions to `PAUSED`

Both failure modes now produce `PAUSED`:

- **Decline (`Match → DECLINED`):** the decliner's `Registration.status →
  PAUSED`. The other party is re-queued to the front (`priority += 1`).
- **Contact-window expiry (`Match → EXPIRED`):** non-responding parties'
  `Registration.status → PAUSED`. Each non-responder receives a
  "your match expired" email (sent via `transaction.on_commit`). The faithful
  party (if any) is re-queued to the front, as before.

### Self-service rejoin (`rejoin_queue`)

A `PAUSED` registration can transition back to `VERIFIED` at any time by the
user themselves, from their account page. The `rejoin_queue` service function:

1. Acquires a `select_for_update` lock and confirms the registration is still
   PAUSED (idempotent guard against double-submit).
2. Sets `status = VERIFIED`, `priority -= 1` (a mild de-prioritisation — the
   person left the queue and is rejoining behind those who never left).
3. Calls `propose_match` immediately, so if a counterpart is waiting the pair
   is matched on the spot.

The UI surface is a "Rejoin the queue" button on the account page, visible only
when `registration.status == PAUSED` and there is no active match.

### Retired machinery

The following are entirely removed:

- `Registration.flake_count` field.
- `Registration.prior_decline_count` field.
- `Match.declined_by_email_hash` field.
- `MatchQuerySet.for_decline_hash()` method.
- `core/hashing.py` (`hash_email`, `EMAIL_HASH_SECRET`).
- `requeue_to_back()` service function.
- `record_flake_and_requeue()` service function.
- The email-hash lookup block inside `register_participant`.
- `HasFlakesListFilter` in admin.
- `EMAIL_HASH_SECRET` environment variable requirement.

### `suspend_for_no_show` is unchanged in outcome

`suspend_for_no_show` continues to set `Registration.status → SUSPENDED` for
post-accept no-shows reported via the match page. `SUSPENDED` is intentionally
not self-recoverable (unlike `PAUSED`) — it requires staff action in admin.

### Match FKs are now `CASCADE` (non-nullable)

Because registrations are never deleted, `Match.ambassador_registration` and
`Match.referee_registration` no longer need `on_delete=SET_NULL` or `null=True`.
Both FKs are changed to `on_delete=CASCADE`. If a registration is ever deleted
(via admin), its matches cascade away.

### Migrations

The existing nine migrations (0001–0009) are squashed into a single
`0001_initial.py` for the `matching` app. This is safe to do because VERB-74 is
the first migration squash and no other app holds data-level dependencies on
intermediate `matching` migrations.

---

## Consequences

- **Simpler code.** One code path for both decline and non-response. No hash
  machinery, no flake counter, no two-strike logic.
- **Better user experience.** A decliner or non-responder is not locked out;
  they can rejoin at their own pace with a single button click.
- **Lower operational complexity.** `EMAIL_HASH_SECRET` is no longer a required
  production environment variable. The security audit (2026-06-30) noted its
  absence as a potential gap; removing it closes that finding.
- **History is retained.** The registration and all prior matches remain in the
  database as an audit trail. Staff can see that a registration was paused and
  when it rejoined.
- **Mild re-entry cost.** `rejoin_queue` decrements `priority` by 1, so repeat
  rejecters slowly drift behind fresh registrations in the queue. This is a
  gentle disincentive without the blunt-force punishment of account deletion.
- **No auto-suspension for non-response.** The two-strike rule is removed.
  `SUSPENDED` is now exclusively a consequence of a post-accept no-show report.
  Staff retain the ability to manually set `SUSPENDED` in admin.

---

## Supersedes

ADR 0008 (decline email-hash design) is superseded by this decision. The HMAC
blind-index approach it describes is entirely retired; see "Retired machinery"
above.
