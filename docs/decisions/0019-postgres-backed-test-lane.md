# ADR 0019 — Postgres-backed test lane

**Status:** Accepted
**Date:** 2026-07-03
**Ticket:** VERB-98

---

## Context

`config/settings/development.py` — the settings module used both for local
development and by the entire pytest suite (`tox -e test`) — has always
pointed at SQLite. Production (Render) and the Playwright e2e stack both run
Postgres. SQLite is more permissive than Postgres for some SQL constructs, so
a class of bug can pass the full pytest suite and CI, and only fail once
deployed or once exercised by the (much narrower, much slower) Postgres-backed
e2e suite.

The concrete instance: the matching engine's `_without_active_match()`
queryset chained `.select_for_update()` onto a queryset that also applied
`.distinct()` (via a reverse-FK join). Postgres rejects `SELECT ... FOR UPDATE`
combined with `DISTINCT` (`django.db.NotSupportedError`); SQLite silently
treats `FOR UPDATE` as a no-op and never errors. This meant registration
confirmation 500'd on Postgres (`confirm → find_match → exists`) despite 800+
green pytest tests and a green CI run. The bug was only caught because the
e2e suite happens to run against Postgres, and was fixed on `main` in VERB-97
(#99) by replacing the reverse-FK join with an `Exists` subquery, removing the
need for `.distinct()` altogether.

The e2e suite is not a substitute safety net for this class of bug day to
day: it is browser-driven, an order of magnitude slower, covers only a
handful of full user journeys, and is not the suite most changes are
iterated against. A regression in a rarely-exercised query path can land and
sit for a long time before an e2e run happens to touch it.

## Decision

Add a **Postgres-backed lane to the pytest suite**, run alongside — not
instead of — the existing SQLite default:

- `config/settings/development.py` gains the same `DATABASE_URL`-conditional
  pattern already used in `config/settings/e2e.py`: when `DATABASE_URL` is
  set, `DATABASES` is built via `dj_database_url.config()` (Postgres);
  otherwise it falls back to the existing local `db.sqlite3` file. Local
  `tox -e test` is therefore unchanged — SQLite, zero setup — unless a
  developer opts in by exporting `DATABASE_URL`.
- The CI `test` job (`.github/workflows/ci.yml`) gains a `postgres:16-alpine`
  service (mirroring the one already used by `.github/workflows/e2e.yml`) and
  sets `DATABASE_URL` in the job's `.env` file, so the **full** pytest suite —
  not a curated subset — runs against Postgres 16 on **every pull request**.
- `format` and `checks` jobs are untouched; only `test` gains the Postgres
  service, since only that job exercises real queries against the database.

## Rationale

- **Coverage × fidelity, at unit-test speed.** The pytest suite already has
  near-complete coverage of the matching engine and its query patterns; running
  it a second time against Postgres gets Postgres-accurate SQL semantics for
  that entire surface, at pytest speed (seconds), not e2e speed (minutes).
- **The e2e suite is deliberately narrow.** It targets a small number of full
  user journeys through the browser and is not where day-to-day query-level
  changes are iterated. Relying on it alone to catch backend divergence means
  most changes go unchecked for the class of bug this ADR addresses.
- **SQLite stays the default.** Removing SQLite entirely would slow every
  local test run and add a database dependency to local development, which
  the project has deliberately avoided elsewhere (see the e2e settings'
  own SQLite fallback). Running both is the version of this that costs
  nothing locally and catches the most in CI.
- **One version, pinned.** Postgres 16 matches the version already used by
  the e2e lane and (by convention) production, so a divergence caught here is
  a divergence that would also occur in Render.

## Consequences

**Positive:**

- The VERB-97 class of bug — a query that is valid SQLite but invalid
  Postgres — now fails the pytest suite on every PR, not just an occasional
  e2e run.
- `tests/matching/test_backend_divergence.py` encodes the VERB-97 divergence
  directly (`select_for_update()` chained onto `distinct()`) as a
  backend-aware regression test: it asserts `NotSupportedError` on Postgres
  and no error on SQLite, so it stays green on both lanes and documents the
  trap for future contributors.
- No change to local developer workflow: `tox -e test` without `DATABASE_URL`
  set behaves exactly as before.

**Negative / trade-offs:**

- CI's `test` job now runs the full suite once per lane it is invoked from —
  in practice, the same job now depends on a Postgres service container, adding
  service start-up time (mitigated by the existing health-check wait already
  proven in the e2e workflow).
- Two backends now need to independently accept every new query added to the
  codebase; a query that is Postgres-valid but SQLite-invalid (the inverse
  direction) would newly surface here too — considered a feature of this
  change, not a cost.
- `config/settings/development.py` picks up the `dj_database_url` import
  conditionally (only when `DATABASE_URL` is set), mirroring `e2e.py`; both
  `dj-database-url` and `psycopg[binary]` were already `tox.ini` dependencies
  (added for the e2e lane), so no new dependency was required.
