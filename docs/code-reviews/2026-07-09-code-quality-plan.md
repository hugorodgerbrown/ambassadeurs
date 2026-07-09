# Code quality review & refactoring plan — 2026-07-09

Ad-hoc whole-codebase quality scrutiny (ruff, mypy, Django 6.0 patterns via
context7, structural review of the two largest modules, architecture-drift
audit). This is a recommendations plan, not a change; each finding is tracked
as a Linear VERB ticket (see [Tracking](#tracking)).

## Overall health

The codebase is in good shape. `ruff check` is clean, `mypy` is clean, there are
no TODO/FIXME/HACK debt markers, transaction discipline is sound
(`select_for_update` inside `atomic`, side-effects via `on_commit`), every
concrete model satisfies the full-kit convention, and the `debug/` app is
correctly gated (`@require_debug` → 404 when `DEBUG` is false, on multiple
layers). The findings below are **structural refactors and documentation drift,
not defects** — the app works.

### One flagged "critical bug" — verified false

A review pass flagged `except A, B:` (unparenthesised) at `public/views.py:955,
973, 1388` as a Python-2 syntax error that would stop the module importing. This
is wrong. Confirmed with `py_compile` and a runtime probe on Python 3.14.6: this
is [PEP 758](https://peps.python.org/pep-0758/), a 3.14 feature that allows
`except A, B:` as equivalent to `except (A, B):`. It compiles, imports, and
catches both exceptions — exactly as intended. The project pins
`requires-python >=3.14` and ruff/mypy `target-version = py314`, which is why
every gate passes. No fix needed; at most a readability preference (F16).

## Effort scale

**S** ≈ <½ day · **M** ≈ ½–2 days · **L** ≈ >2 days.

## Tier 1 — Highest value (cohesion & measurement)

| # | Finding | Location | Fix | Effort |
|---|---------|----------|-----|--------|
| F1 | `public/views.py` is 1556 lines bundling 6 unrelated concerns (pages, registration, payments, tips, survey, match lifecycle) | `public/views.py` | Split into a `public/views/` package: `pages.py`, `registration.py`, `payments.py`, `tips.py`, `survey.py`, `match.py`; re-export from `views/__init__.py` so `urls.py` and the `accounts.views` import of `_render_match_page` stay valid | M |
| F2 | `billing` (Stripe/money) and `debug` are excluded from coverage `source`; no `fail_under` enforces the 90% target CLAUDE.md claims | `pyproject.toml:120-127` | Add `billing` to `source`; add `fail_under = 90`; decide whether to measure `debug` | S |
| F3 | Presentation logic lives in the domain-services module: `status_pill_for` and `match_status_context` build translated labels / CSS-tone / template context | `matching/services.py:254-412` | Move to `matching/selectors.py` (or `presentation.py`); repoint callers in `accounts.views`, `public.views` | M |

## Tier 2 — Duplication (drift risk)

| # | Finding | Location | Fix | Effort |
|---|---------|----------|-----|--------|
| F4 | The ranking rule (location flag → `-priority` → `created_at`) is written twice: once in SQL, once reimplemented in Python for the dry-run | `matching/services.py:481-490` vs `1056-1064` | Extract one shared ranking key used by both the live and simulate paths | M |
| F5 | Payment and tip Stripe flows are near-line-for-line parallel (`*_start`, `*_return`), and the customer/payment-intent narrowing block appears a third time in the webhook | `public/views.py:655-679 / 833-856`; `683-738 / 860-922 / 966-969` | Shared `_redirect_to_checkout(...)` and `_verify_return_session(...)` helpers; removes ~40 duplicated lines | M |
| F6 | Three overlapping presentation projections of one small state machine, each re-deriving "active but window lapsed → terminal" and per-side acceptance | `public/views.py:1133-1220` | Single `MatchDisplay` value object computed once from `(match, side)` exposing `.guard_state` / `.view_key` / `.side_status()` | S–M |
| F7 | `can_rejoin` and `can_cancel` are byte-for-byte identical conditions, each recomputing the same predicate | `matching/services.py:388-401` | Compute once, assign both | S |
| F8 | `accept_match` is a 4-line no-op wrapper over `record_acceptance` (exists only to re-narrow a mypy `Any`), while `decline_match` is a real orchestrator — asymmetric naming | `matching/services.py:644-671` | Fold into `record_acceptance` with a `cast()` at the call site, or rename the pair for symmetry with decline | S |

## Tier 3 — Fat views → service layer

| # | Finding | Location | Fix | Effort |
|---|---------|----------|-----|--------|
| F9 | `register_form` holds resolve-or-resend-or-create orchestration (geolocation, enrolment guard, `select_for_update` TOCTOU, `IntegrityError` fallback) in the view | `public/views.py:343-496` | Extract `register_or_resend_participant()` into `matching/services` | M |
| F10 | `stripe_webhook` routes events + branches tip/deposit + calls finalisers inline; idempotency is delegated to services with no `event.id` dedup at the edge | `public/views.py:959-1004` | Extract `billing.services.handle_checkout_completed(event)`; verify the two finalisers truly no-op on replay (the tip path relies on an `IntegrityError` on a unique `payment_intent_id` — fragile) | M |
| F11 | `register_survey_submit` does the check + create + race-backstop inline; `public/` has no services module | `public/views.py:1020-1078` | Extract `record_survey_response(...)`; seed `public/services.py` | S–M |

## Tier 4 — Documentation drift

| # | Finding | Fix | Effort |
|---|---------|-----|--------|
| F12 | The `Tip` model (VERB-110) is entirely undocumented — no ADR, no glossary entry — a second Stripe money-flow with a free-text user `message` field | Write a short ADR + glossary rows | M |
| F13 | CLAUDE.md architecture map omits `billing/` and `debug/`, both in `INSTALLED_APPS` | Add both to the app map (§Architecture) | S |
| F14 | Data-minimisation section reads as if it forbids the billing surface it doesn't mention (amounts, Stripe ids, tip messages) | Add a carve-out sentence referencing ADR 0014 ("Stripe identifiers only, never card data") | S |
| F15 | Docs routing table has no billing/payments/tips row | Add rows pointing to ADR 0014 (+ the new Tip ADR) | S |

## Tier 5 — Minor / polish

| # | Finding | Location | Effort |
|---|---------|----------|--------|
| F16 | Unparenthesised `except A, B:` (PEP 758) is valid but unusual; parenthesised form reads more clearly and is portable to readers/tools expecting <3.14 | `public/views.py:955, 973, 1388` | S (optional) |
| F17 | Broad `except Exception` at `1030` lacks the "deliberate isolation" comment its two siblings (`1155`, `1575`) carry | `matching/services.py:1030` | trivial |
| F18 | Loose `dict[str, object]` / `dict[str, str]` return types on the context builders | `matching/services.py:254, 299` | S |
| F19 | `register_participant` takes 16 keyword args; `record_acceptance`/`report_no_show`/`propose_match` run 88–121 lines | `matching/services.py` | S–M |

## Suggested sequencing

1. F2 first (one-line coverage config change; surfaces whether billing is
   actually tested before you refactor it).
2. F1 + F3 + F6 together — the view-package split, the services→selectors move,
   and the `MatchDisplay` object are one coherent "separate presentation from
   domain" pass.
3. F4, F5, F7, F8 — mechanical de-duplication, low risk, each independently
   shippable.
4. F9–F11 — the fat-view extractions; do F10 with the idempotency verification
   since it touches money.
5. F12–F15 — documentation, batchable into one docs PR.

Nothing here blocks the September 2026 launch; F2 and F10 are the two not to
leave unaddressed (money code, currently unmeasured and with edge-idempotency
unconfirmed).

## Tracking

Tier 1 and Tier 2 findings are individual tickets; Tiers 3–5 are grouped one
ticket per tier. Ticket IDs filled in below once created.

| Ticket | Scope | Tier |
|--------|-------|------|
| [VERB-134](https://linear.app/hugorodgerbrown/issue/VERB-134) | F1 — Split `public/views.py` into a views package | 1 |
| [VERB-135](https://linear.app/hugorodgerbrown/issue/VERB-135) | F2 — Enforce coverage on billing; add `fail_under=90` | 1 |
| [VERB-136](https://linear.app/hugorodgerbrown/issue/VERB-136) | F3 — Move match presentation helpers out of `matching/services` | 1 |
| [VERB-137](https://linear.app/hugorodgerbrown/issue/VERB-137) | F4 — De-duplicate the referee ranking rule (live vs simulate) | 2 |
| [VERB-138](https://linear.app/hugorodgerbrown/issue/VERB-138) | F5 — Share Stripe start/return helpers across payment & tip flows | 2 |
| [VERB-139](https://linear.app/hugorodgerbrown/issue/VERB-139) | F6 — Introduce a `MatchDisplay` value object | 2 |
| [VERB-140](https://linear.app/hugorodgerbrown/issue/VERB-140) | F7 — De-duplicate `can_rejoin`/`can_cancel` predicate | 2 |
| [VERB-141](https://linear.app/hugorodgerbrown/issue/VERB-141) | F8 — Collapse the `accept_match` wrapper / rationalise naming | 2 |
| [VERB-142](https://linear.app/hugorodgerbrown/issue/VERB-142) | Review Tier 3 fixes — fat views → service layer (F9–F11) | 3 |
| [VERB-143](https://linear.app/hugorodgerbrown/issue/VERB-143) | Review Tier 4 fixes — documentation drift (F12–F15) | 4 |
| [VERB-144](https://linear.app/hugorodgerbrown/issue/VERB-144) | Review Tier 5 fixes — minor / polish (F16–F19) | 5 |
