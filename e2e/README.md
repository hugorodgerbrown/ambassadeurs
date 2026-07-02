# End-to-end tests (Playwright)

Automated browser tests that walk the same flows as
[`docs/manual-tests/manual-test-script.md`](../docs/manual-tests/manual-test-script.md),
so the manual script becomes a fallback rather than the only safety net.

The suite runs against an **ephemeral, production-shaped instance** of the app
(`config.settings.e2e`, `DEBUG=False`) — never against production. In CI a fresh
Postgres and a fresh mail sink are spun up per run; locally Playwright boots
Django for you.

---

## The email problem, and how this suite solves it

Almost every meaningful flow depends on a link the app **only sends by email** —
registration confirmation, magic-link login, and match notifications. With
`DEBUG=False` there is no on-page shortcut. So an e2e suite has to get those
links from somewhere.

### Recommendation: a Mailpit mail sink the tests read back over HTTP

Point Django's SMTP at [**Mailpit**](https://mailpit.axllent.org/) — a tiny,
disposable SMTP server that also exposes an HTTP API. The app sends mail exactly
as in production; the test then polls Mailpit's API for the message just sent to
that address and pulls the signed link out of the body. That is the automated
equivalent of a human opening their inbox and clicking the link.

Why this over the alternatives:

| Approach | Fidelity | Verdict |
|---|---|---|
| **Mailpit sink + HTTP read-back** (chosen) | Real SMTP send, real templates, real signed links, with `DEBUG=False`. Deterministic, no external inbox, no rate limits. | **Recommended.** |
| `DEBUG=True` on-page link + `/debug/` panel | Exercises **DEBUG-only** code that does not exist in production; the debug panel can fabricate matches the real engine never would. Fast, but low-fidelity. | Fine for a quick smoke lane only. |
| `filebased` email backend, read `.eml` from disk | Faithful send, but couples the runner to a shared filesystem and to `.eml` parsing; awkward once the app and tests run in separate containers. | Workable fallback if you cannot run a container. |
| A real inbox (Gmail/Mailosaur/etc.) | Highest fidelity, but slow, flaky, rate-limited, and needs live credentials in CI. | Reserve for the **manual** script against a real deploy, not CI. |

The whole dependency is isolated in [`helpers/mail.ts`](helpers/mail.ts) behind
`waitForMessage()` / `extractLink()`. If you ever swap Mailpit for another sink,
that one file changes and no spec does.

Do **not** point the CI suite at a real production deploy and a real inbox — that
is what the manual test script is for. CI tests an app instance it fully controls.

---

## What the suite covers

Mapped to the manual script scenarios:

The suite covers **all 14 scenarios** of the manual script. Each test is tagged
with its scenario number (`@S<n>`), and a custom reporter prints the manual
script's results log at the end of every run (see "Results log" below).

| Spec | Manual scenarios |
|---|---|
| `tests/01-smoke.spec.ts` | 1 (smoke), 2 (i18n) |
| `tests/02-registration-login-account.spec.ts` | 3 (register + confirm), 10 (magic-link login/logout, enumeration), 11 (account edit/delete) |
| `tests/03-matching.spec.ts` | 5 (mutual accept → reveal), 6 (decline + rejoin), 7 (withdraw), 8 (post-accept no-show), 13 (privacy sweep, asserted inline) |
| `tests/04-expiry.spec.ts` | 9 (contact-window expiry) |
| `tests/05-states-admin.spec.ts` | 4 (paid deposit), 12 (closed states), 14 (admin) |

The privacy invariant (Invariant 1 — contact PII hidden until mutual accept) is
asserted directly (Scenario 13): before both accept, the partner's email and
phone must be **absent** from the page; only after the second accept may they
appear.

A few scenarios need a server-side action a user cannot take from a page — the
**expiry** sweep (Scenario 9) and **admin** (Scenario 14). Rather than fake them,
those tests shell out to the real management command (`helpers/manage.ts`):
Scenario 9 backdates the match's `expires_at` (`helpers/db.ts`) and then runs the
actual `expire_matches` cron command; Scenario 14 creates a superuser via
`createsuperuser` and signs in through the real admin form.

### Scenarios reported n/a by default (env-gated)

Two scenarios need the server booted with a **different configuration** than the
default open/free instance, so they self-skip and report **n/a** unless you run
them against an appropriately-configured server:

- **Scenario 4 (paid deposit / Stripe).** Needs `REGISTRATION_FEE_TIERS>0` and
  Stripe **test** keys. Run with `E2E_RUN_PAID=1` against a paid-tier instance.
  Driving Stripe's hosted Checkout is slow/flaky, so keep it off the PR gate.
- **Scenario 12 (closed / not-open states).** Needs a server booted with
  `REGISTRATION_CLOSES_AT` in the past. Run with `E2E_EXPECT_CLOSED=1` against a
  closed instance.

Rate-limit 429s (a Scenario 3 edge) stay in pytest: `RATELIMIT_ENABLE=False` in
the e2e settings so a full browser run is not throttled.

## Results log

Every run ends by printing the manual script's results-log table, one row per
scenario, filled in from the actual run — `PASS` / `FAIL` / `n/a` with notes
(skip reasons, failure summaries). It is produced by `reporters/results-log.ts`,
which maps each test's `@S<n>` tag to a scenario row. A FAILED Scenario 13
(privacy) is called out as a release blocker. The scenario catalogue lives in
`helpers/scenarios.ts` — keep it in lockstep with the manual script.

---

## Running locally — the same way CI runs

Local and CI use the **same container images** (`e2e/compose.yaml` pins the same
tags the CI job's service containers use) and the **same settings, env vars, and
steps** (`e2e/run-local.sh` mirrors `.github/workflows/e2e.yml`). The only
difference is who starts the containers.

Prerequisites: the Python env (`uv sync`), Node 20+, and a container runtime
(Docker Desktop, OrbStack, Colima, …).

```bash
cd e2e
npm install
npx playwright install chromium   # one-time: browser binaries

# One command: starts postgres + mailpit (compose), builds CSS, migrates,
# boots Django (config.settings.e2e), and runs the suite — the CI sequence.
npm run test:local                 # whole suite
npm run test:local -- 03-matching  # a subset (args pass through to playwright)
```

Or drive the pieces yourself:

```bash
npm run services:up      # postgres + mailpit, same images as CI
npm test                 # Playwright boots Django via its webServer block
npm run services:down    # tear the containers down
```

While the sink is up, browse delivered mail at <http://localhost:8025>. Open the
HTML report with `npm run report`; `npm run test:ui` steps through interactively.

> **Run e2e against Postgres, not SQLite.** `config.settings.e2e` falls back to
> SQLite only when `DATABASE_URL` is unset (a no-container convenience). Do not
> rely on it: SQLite silently accepts SQL that Postgres rejects — most sharply
> `SELECT ... DISTINCT ... FOR UPDATE`, which SQLite ignores and Postgres 500s
> on. `run-local.sh` and CI both set `DATABASE_URL` at the compose Postgres, so
> they catch these; a bare SQLite run would not.

## How it runs in CI

[`.github/workflows/e2e.yml`](../.github/workflows/e2e.yml) starts Postgres and
Mailpit as service containers, applies migrations, builds the CSS, and runs the
suite. Playwright boots the Django server (`config.settings.e2e`) pointed at both
services via the job's environment variables. The HTML report is uploaded as an
artifact on every run.

---

## Test isolation

The matching engine matches across the **entire** pool of verified
registrations, so a registration one test leaves behind can be cross-matched by
a later test — the tests are not independent by construction, and this bites
within a single run (a fresh CI database does not fix it). An auto-used fixture
(`fixtures.ts` → `helpers/db.ts`) therefore **truncates the domain tables before
every test** over a direct Postgres connection (fast; no per-test Django boot).
This is why the suite needs Postgres, not the SQLite fallback: the reset is a
no-op without `DATABASE_URL`, and matching tests would then flake.

## Maintenance notes

- **Selectors live in one place.** Routes, form-field ids, and match-action
  selectors are all in [`helpers/app.ts`](helpers/app.ts). Form fields key off
  Django's stable `id_<field>` ids and actions key off the form `action`
  attribute, so neither breaks when display copy changes. When a template's
  structure changes, reconcile that one file.
- **Consider adding `data-testid`** to the match reveal block and the account
  controls if the markup churns — it makes the intent explicit and the selectors
  bomb-proof. The current selectors avoid it to keep the app untouched.
- **Do not assert translated copy.** Following the project convention, assertions
  match on data you control (names, emails, phone numbers) or on structure, not
  on English/French strings — the CI app serves English source strings anyway.
- Keep this suite and the manual script in step: when a flow changes, update the
  spec here and the scenario in `docs/manual-tests/manual-test-script.md`.
