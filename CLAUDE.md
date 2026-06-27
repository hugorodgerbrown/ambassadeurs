# CLAUDE.md — Ambassadeurs

## Project overview

Django web app that **matches partners** for the 4 Vallées Ambassadors Program
(referral / *parrainage* scheme for the 4 Vallées annual season ticket).

**The problem this solves.** To get the referral discount, a returning holder (an
*ambassador*) and a genuinely new holder (a *referee*) must apply and buy together.
There are always more referees than ambassadors, so each season opens with an
uncontrolled scramble — mostly in a Facebook group — for a partner, and people
routinely commit to one partner and then vanish before the pair can meet. There is
also no way to tell who is still available: posts never expire and draw replies
months later, long after the slot is gone. This app brings order to *finding a
partner*. It is **not** the application or purchase
system: filling in the form and buying at the kiosk happen off-app and are unchanged.

**How it works — an invisible "taxi rank".** Ambassadors pre-register their
availability. Referees register and are **matched by the system** to an available
ambassador (they do not browse or choose, and the two do not know each other). A
matched pair gets a fixed **contact window** to mutually accept and make contact;
once both accept, the system reveals their contact details and they go do the
(off-app) application together. The whole product is the matchmaking — the discount,
form, and kiosk purchase all happen afterwards, elsewhere.

The frontend uses **HTMX** for dynamic updates without a JavaScript framework, and
**Tailwind CSS v4** for styling. The UI is bilingual — **English (default) and
French** — via Django i18n.

Launch: **September 2026**, promoted through the "Verbier" community on Facebook.
The public entry points are the ambassador and referee registration flows; program
staff oversee the pool and matches through the Django admin.

This is a greenfield project. The conventions below are the target shape; follow
them as code lands. Domain term → code symbol map: [`docs/glossary.md`](docs/glossary.md)
(create it as terms gain symbols). Accepted architectural decisions (the "why"):
[`docs/decisions/`](docs/decisions/).

Python 3.14 / Django 6.0 (pinned in `pyproject.toml`).

## Architecture

Target app layout. Create apps as the domain needs them; don't pre-build empty shells.

```
config/          Django project settings (split base/development/production)
core/            Shared abstractions (BaseModel; abstract, no concrete tables),
                 HTTP-layer middleware, shared helpers
accounts/        Signed-link auth, and Facebook social login (django-allauth);
                 AUTH_USER_MODEL is the default Django model. Participant
                 attributes (phone, preferred_language) live on
                 matching.Registration (OneToOneField to User). Admin users
                 have a User but no Registration.
matching/        The core domain — Registration, Match, the matching engine
                 (queue + assignment) and the Match state machine
                 (proposed → accepted / declined / expired) and its services
public/          Public-facing registration + match site (full-page views + HTMX partials)
templates/       Project-level templates shared across apps
  includes/      Reusable partials (nav.html, _button.html, _card.html, …)
src/             Tailwind CSS source (css/main.css — not served directly)
static/          CSS/JS assets (includes compiled css/output.css)
locale/          Translation catalogues (en, fr)
logs/            Log files (gitignored except .gitkeep)
```

### Core domain

The platform runs one season at a time. Season configuration (registration
window, contact window) is managed via environment variables rather than
database rows. See `docs/decisions/0005-single-season-matching-engine.md`.

- **Registration** — a user's enrolment in one **role** (`AMBASSADOR` or
  `REFEREE`). OneToOneField to `User`. Holds the role, `prior_pass` attestation
  (`NONE / SEASONAL / ANNUAL / MONT4`) that gates match eligibility, phone,
  preferred language, preferred ticket office / resort (a *soft* preference —
  used to rank matches, not to gate them), `status` (`WAITING` → `MATCHED` →
  `CONFIRMED`, or `WITHDRAWN`), and the queue **priority** that asymmetric
  flaking handling adjusts.
- **Match** — a system-created link of one ambassador registration and one
  referee registration. Terminal matches accumulate as history (no unique
  constraint on the registration FKs). State machine:
  - `PROPOSED` — the engine paired them; both are notified and see **the
    partner's first name**, but **neither sees the other's contact details**
    (email, phone, surname) until both accept.
  - each side accepts or declines within the contact window.
  - both accept → `ACCEPTED` — contact details are revealed and the pair
    proceeds to the off-app application. Terminal success; both leave the pool.
  - one declines → `DECLINED`; window lapses without both accepting → `EXPIRED`.
    In both, the registrations re-queue with **asymmetric** priority: the party
    who accepted keeps their place near the front; the non-responder is sent to
    the **back of the queue**. The contact window is **72 hours** by default
    (`CONTACT_WINDOW_HOURS` env var).

**The matching engine** assigns rather than letting users choose. Referees are
the scarce side — there are always more ambassadors than referees looking to
pair. When either party registers and an eligible counterpart is already
waiting, the engine proposes a match immediately (synchronous trigger inside
`register_participant`). Ranking: shared location first, then priority
descending, then FIFO. A match is only ever proposed between an eligible pair
(see below). Keep all eligibility and assignment logic in `matching/` services,
not in views.

### Match eligibility

A match may only be proposed between an eligible pair. Model these as data +
`matching/` services, never as inline conditionals in views. Capture the
rationale in [`docs/decisions/`](docs/decisions/).

- **Ambassador `prior_pass` in `{SEASONAL, ANNUAL, MONT4}`** — held a seasonal
  or annual 4 Vallées pass (or a Mont 4 Card / special reduction) in a prior
  season.
- **Referee `prior_pass == NONE`** — did *not* hold any prior pass (genuinely
  new). This is self-attested; proof happens off-app at the kiosk.
- **Both must be `WAITING`** — neither can already be in a proposed or accepted
  match.
- **Mont 4 / special-reduction ambassadors** (`prior_pass == MONT4`) are fully
  eligible to match. The referee they take still benefits from the referral
  discount even if the ambassador does not receive one.
- **Location is a soft preference** — the pair must ultimately buy together at
  the same ticket office, so registrations capture a `preferred_location`. The
  engine *prefers* a shared location but does not hard-gate on it; the pair
  settle the meeting between themselves.

**Data minimisation.** The full form PII (date of birth, address, photo ID,
keycard, insurance, consents) belongs to the *off-app* application and must
**not** be collected here. The app holds only what matching and contact need:
name, email, phone, role, `prior_pass` attestation, and preferred location.
Treat email and phone as sensitive (Swiss data protection) and never expose
them across a match before mutual accept (see Invariants).

### Open questions (resolve before building the relevant slice)

- **Eligibility verification depth** — pure self-attestation, or some up-front check
  (e.g. keycard / prior-pass lookup) before a registration enters the pool.

Resolved (see [ADR 0007](docs/decisions/0007-post-match-confirmation-workflow.md),
VERB-16):

- **Asymmetric flaking specifics** — non-responder/decliner to the back, kept-faith
  party to the front; a **flake** (non-response or post-accept no-show, *not* a
  decline) is recorded, and **2 flakes auto-suspends** the registration.
- **Completion + post-accept no-shows** — a confirmed party can **report** the other
  as a no-show; the report is trusted immediately, the reporter re-queues to the
  front, and the reported party is **removed from the pool** (`SUSPENDED`). The app
  does not otherwise track whether the off-app application happened.
- **Notifications** — **email + signed links only** for launch; no SMS/push.

Operating entity on the 24/25 form: **Groupe Télé-Thyon SA** (back-office contact
caissier@tele-thyon.ch). Public branding is **4 Vallées-neutral** (the program spans
Verbier, Thyon, and the wider 4 Vallées) — keep the operating company out of
user-facing copy.

## Running locally

```bash
cp .env.example .env          # fill in values
uv sync
npm install
uv run python manage.py migrate

# Terminal 1: Tailwind CSS watcher
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch

# Terminal 2: Django dev server
uv run python manage.py runserver
```

## Dependency management

Use **uv** (`uv add`, `uv add --dev`, `uv lock --upgrade`). `pyproject.toml` is the
single source of truth (PEP 621 `[project]` + PEP 735 `[dependency-groups]`); there
is no `requirements.txt`. The resolved set is pinned in `uv.lock` — commit it with
every dependency change.

The virtualenv lives at `.venv/` inside the repo (uv's default). When a runtime
dependency is added via `uv add`, also add it to the relevant `deps =` block in
`tox.ini` (`test`, `django-checks`, and `mypy` need it; `fmt` and `lint` usually
don't) — tox does not read `pyproject.toml` dependencies automatically.

## Conventions

### Code

- **Header comment block** on every module describing its purpose; **docstring**
  on every function and class.
- All function arguments are typed, except `*args` and `**kwargs`.
- `ruff` for linting and formatting (includes import sorting); `pre-commit` hooks
  enforce on commit. No `# noqa` without a reason and a comment explaining why.
- **British English** spellings in code, comments, and docs (colour, behaviour,
  organise) — except third-party identifiers. User-facing copy follows the i18n
  rules below, not this one.
- **Composition over inheritance** — favour passing service objects as arguments
  over deep class hierarchies.
- **Simple over complex** — no abstractions until needed by at least two callers.
- Settings are split: `config/settings/base.py`, `development.py`, `production.py`.
  Set `DJANGO_SETTINGS_MODULE` in the environment.
- Use `python-decouple` for secrets; never hard-code credentials.
- Logging is configured in `base.py` under `LOGGING`. Use
  `logging.getLogger(__name__)` in every module.
- **No Django signals for side effects** — save-time side effects are called
  inline from the relevant service function, never via `post_save`.
- **TextChoices** - fixed choice values must be modelled as `TextChoices` within
  the relevant model class, and the choice values must be UPPER_CASE.

### Models

Every concrete model ships the full kit — uniformity across models is the point,
so don't skip pieces for "simple" models:

- inherits from the `BaseModel` abstract model;
- an explicit admin class;
- an explicit `to_string()` method (`__str__` delegates to it);
- an explicit `Meta.ordering` (`-created_at` by default);
- a custom queryset;
- a test factory and test coverage.

### Testing

- pytest + FactoryBoy. Tests live in a top-level `tests/` directory that mirrors
  the source tree; each module has a corresponding `test_{module_name}.py`.
- All new code must have covering tests; the coverage target is 90%.
- Always run tests via `uv run tox -e test` (not a bare `pytest` call) — the tox
  env mirrors CI.
- All datetime objects must have `tzinfo`.
- Always call factories with `.create()` (e.g. `MatchFactory.create(...)`) —
  never direct instantiation. `.create()` is properly typed and lets mypy infer
  the model return type.

## Authentication

No passwords. Two entry paths, both keyed on a lowercase email address:

- **Signed email links** — registrants verify their email, log in, and action a
  match (accept / decline) via signed, tokenised links (Django signing). Tokens are
  single-purpose and expiring.
- **Facebook login** — via `django-allauth`, since launch happens through the
  Verbier Facebook community.

Normalise every email to lowercase at every entry point before storage and lookup.

## Frontend

**Tailwind CSS v4** compiled via the `@tailwindcss/cli` package.

- Source: `src/css/main.css` — contains `@import "tailwindcss"`, `@theme` design
  tokens, and component exceptions. Lives outside `static/` so WhiteNoise never
  post-processes it.
- Output: `static/css/output.css` — gitignored build artefact loaded by templates.
- All styling uses Tailwind utility classes in templates. Add custom CSS to
  `src/css/main.css` only for what Tailwind cannot express.
- Build with the watch command under "Running locally"; production builds use
  `--minify` instead of `--watch`.

**HTMX** patterns:

- Full-page views return a complete HTML response.
- Partial/fragment views return only the inner HTML snippet; route them under a
  `partials/` prefix and guard them with `require_htmx` (reject plain HTTP with 400).
- Use `hx-target`, `hx-swap="innerHTML"`, and `hx-indicator` for dynamic requests.

**Template comments:**

- `{# … #}` is **single-line only** — content after the first line renders as
  visible page text. Any comment **longer than 50 characters must use
  `{% comment %} … {% endcomment %}`**, even when it fits on one line. Keep
  `{# #}` for short (≤50 char), single-line notes. Neither `djangofmt` nor `tox`
  flags a multi-line `{# #}`, so this is on the author.

## Internationalisation

The UI ships in **English (default) and French**. Wrap all user-facing strings in
Django's translation functions (`gettext`/`gettext_lazy` in Python, `{% translate %}`
/ `{% blocktranslate %}` in templates) — never hard-code display copy. Translation
catalogues live in `locale/en/` and `locale/fr/`. Code, comments, and docs stay
British English (see Conventions); the i18n rule governs display strings only.

**Always run `makemessages` with `--no-location`** (e.g.
`uv run python manage.py makemessages -l en -l fr --no-location`). The `#: file:line`
comments churn on every extraction as code moves, so leaving them in makes `.po`
merges almost impossible — two branches touch the same line references independently
and conflict, and the numbers shift again after merge. Omitting them keeps the
catalogues stable and mergeable.

## Local CI — always run tox

**`tox` is the single entry point** for linters, type checks, Django system checks,
and the test suite. The tox envs declare their own dependencies (independent of the
uv venv), so a tox run mirrors CI.

```bash
uv run tox                    # run every env (fmt, lint, mypy, django-checks, test)
uv run tox -e test            # one env at a time
uv run tox -e mypy
uv run tox -e django-checks
uv run tox -e fmt             # ruff format --check
uv run tox -e lint            # ruff check
uv run tox -e audit           # pip-audit on the locked dependency set
uv run tox --recreate         # rebuild envs after a deps change
```

Template formatting is enforced by `djangofmt` as a pre-commit hook. Run
`pre-commit run djangofmt --files <path>` after editing templates so the hook
doesn't reformat on commit.

**Before opening a PR**, run `uv run tox` and fix every failure.

## Linear workflow

Linear (team prefix `VERB-`) is the issue source of truth. Chat creates and scopes
tickets through `Ready for dev`; Code moves the ticket to `In Progress` via the
Linear MCP immediately after creating the local branch (no push at that point). The
GitHub–Linear integration handles `In Review` (PR opened) and `Done` (PR merged);
both require `VERB-xxx` in the branch name or PR body.

- Branch: `feature/VERB-xxx-short-description` (`fix/VERB-xxx-…` for bugs,
  `chore/VERB-xxx-…` for tooling/infra). One ticket per branch.
- Commit subject prefix `VERB-xxx:` — keeps the ticket reference in the git log
  after squash-merge.
- PR title: `VERB-42: short imperative summary`. The body must start with
  `Closes VERB-42` — that closes the Linear ticket on merge.
- **Stop and ask** if: the scoping comment is missing (scope in Chat first); tests
  fail and the fix isn't obvious; or implementation reveals the scope was wrong
  (comment on the Linear issue first).

## Path to live

Deployed on **Render**. Topology:

- **Web service** (`ambassadeurs`) — serves the Django app via Gunicorn.
- **Cron service** (`ambassadeurs-expire-matches`) — runs `manage.py expire_matches`
  hourly (`0 * * * *`) to sweep PROPOSED matches whose contact window has expired and
  re-queue both registrations.
- **Postgres database** (`ambassadeurs-db`) — shared by both services via
  `DATABASE_URL`.

Every merge to `main` auto-deploys the web service; `build.sh` runs migrations on
each deploy. The cron service shares the same `build.sh` so migrations are safe to
run from either service (they are idempotent).

- **No secrets in source** — all credentials via `python-decouple`; `.env` is
  gitignored and never committed.

## Invariants

These must hold at all times. The QA agent and security-auditor check for drift
against this list on every PR.

1. **Contact PII hidden until mutual accept** — a matched user must not see the
   other party's **email, phone, or full name** until *both* have accepted the
   match. The partner's **first name** may be shown from the moment a match is
   `PROPOSED`, so a participant knows who they have been matched with; email,
   phone and surname stay hidden until mutual accept. Declines and expiry never
   reveal contact details. This is the core privacy guarantee of the product.
2. **Matches are only ever proposed between an eligible pair** — the engine enforces
   the price-category ordering and the prior-season (returning-ambassador /
   genuinely-new-referee) rules before a `proposed` match exists. No view or admin
   path may create an ineligible match.
3. **1:1 per season** — an ambassador and a referee each hold at most one
   non-terminal match in a season; a confirmed match removes both from the pool.
4. **No `mark_safe()` on user-supplied content** — never bypass Django's
   auto-escaping for data originating outside the codebase.
5. **Email addresses normalised to lowercase** before storage and lookup —
   `email = email.lower()` at every entry point.
6. **Signed-link tokens are single-purpose and expiring** — scope every token to
   one action (verify email, accept match, …) and set an expiry; never issue a
   long-lived, multi-purpose token.
7. **HTMX partial views guarded by `require_htmx`** — every fragment endpoint must
   reject plain HTTP requests with a 400.
8. **All user-facing copy is translated** — no hard-coded display strings; use the
   i18n functions so French stays in sync.
9. **No secrets in source** — all credentials via `python-decouple`; `.env`
   gitignored.

## Documentation

When you make a non-obvious architectural choice, add a file to
[`docs/decisions/`](docs/decisions/). When a domain term gains a code symbol, add a
line to [`docs/glossary.md`](docs/glossary.md). Keep this routing table current as
feature docs are written:

| Area | Doc |
|------|-----|
| Domain term → code symbol map | [`docs/glossary.md`](docs/glossary.md) |
| Accepted architectural decisions | [`docs/decisions/`](docs/decisions/) |
| Matching engine (queue, assignment, eligibility) | _to be written_ |
| Match lifecycle (states, contact window, reveal-on-accept) | [ADR 0007](docs/decisions/0007-post-match-confirmation-workflow.md) |
| Flaking / priority handling | [ADR 0007](docs/decisions/0007-post-match-confirmation-workflow.md) |
| Authentication (signed links + Facebook) | _to be written_ |
| Internationalisation | _to be written_ |
| Deployment (Render single-service) | _to be written_ |
| Linear workflow (full lifecycle) | _to be written_ |
