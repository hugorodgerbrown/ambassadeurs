---
name: audit
description: |
  Run a security audit scoped to Ambassadeurs by invoking the security-auditor
  agent with the project's specific threat surface pre-loaded (signed-link
  match-action/login tokens, Facebook OAuth via allauth, email-keyed accounts,
  matchmaking abuse — PII reveal before mutual accept, self-matching,
  eligibility spoofing, fake registrations, match integrity — HTMX partials,
  split Django settings, Render single-service deploy) — no need to describe
  the stack each time. Use
  whenever the user asks for a security audit, vulnerability scan, CVE or
  dependency check, secrets scan, pentest, or pre-deploy security review of
  this project — "/audit", "audit the project", "check for vulnerabilities",
  "run a security scan". Accepts a scope argument: "deps" for a dependency CVE
  scan only, or a path to limit the audit to one module. Do NOT use for
  reviewing the pending changes on a single branch or diff — that is the
  `security-review` skill.
allowed-tools: Task, Read, Bash, Skill, mcp__linear
---

# Ambassadeurs security audit

Invokes the `security-auditor` agent with Ambassadeurs' specific threat
surface pre-loaded, so you don't have to describe the stack each time.

## Scope argument ($ARGUMENTS)

- *(empty)* — full audit of the current working tree.
- `deps` — dependency CVE scan only (`pip-audit` via `tox -e audit` +
  `npm audit`).
- `<path>` — limit the audit to a specific module or directory.

## Steps

1. Invoke the `security-auditor` subagent via the Task tool, passing the
   scope from $ARGUMENTS and the following context injected:

   **Ambassadeurs threat surface** (share with the auditor):
   - **Signed-link match-action / login tokens** — registrants verify their
     email, log in, and accept/decline a match via signed, tokenised links
     (Django signing), without a password. Check that tokens are
     single-purpose and expiring, and check for replay (token reuse after the
     action/expiry), missing expiry enforcement, token forgery, and timing
     attacks on verification.
   - **Facebook OAuth via django-allauth** — launch runs through the Verbier
     Facebook community. Check OAuth `state` (CSRF on the callback), redirect
     handling (open redirect on login/next), and account-linking — an
     attacker linking a Facebook identity to someone else's email-keyed
     account, or hijacking via unverified email.
   - **Email-keyed accounts + lowercase normalisation** — the custom user
     model is keyed on a lowercase email. Check that every entry point
     (registration, match accept, social login, admin) normalises to lowercase
     before storage and lookup; a missed normalisation lets two records, or an
     account-takeover via case variance, slip through.
   - **Matchmaking abuse** — the core domain risk. Lead with **PII exposure
     before mutual accept**: contact details (name, email, phone) must stay
     hidden until *both* parties accept; declines and expiry never reveal
     them. Check that a `proposed` match never leaks the other party's PII
     into a page, response, HTMX swap, or match-status poll, and that a
     registrant cannot harvest contact data by registering and pulling match
     state without accepting. Then check **self-matching** (one person as both
     roles / a second account), **eligibility spoofing** (faking the
     prior-holder or genuinely-new attestation to enter the pool), **fake /
     duplicate registrations** (draining the scarce ambassador pool or gaming
     the queue / priority), and **match integrity** (can a Match reach
     `accepted` / reveal PII without both parties accepting? can the engine
     propose an ineligible pair? is 1:1-per-season enforced?).
   - **HTMX partials** — all fragment endpoints must be guarded by
     `require_htmx`; check for missing guards and CSRF exposure on
     state-changing partials.
   - **Django split settings** — check `DEBUG`, `ALLOWED_HOSTS`, `SECRET_KEY`
     source (via `python-decouple`, never hard-coded), `SECURE_*` headers,
     `SESSION_COOKIE_SECURE`, and `CSRF_COOKIE_SECURE` across the split
     settings layout (`config/settings/base.py`, `development.py`,
     `production.py`).
   - **Render single-service deploy** — one web service + one Postgres DB,
     deploy on merge to `main` with `build.sh` running migrations. Check the
     deploy path for secrets in source or `build.sh`, and that production
     settings are selected via `DJANGO_SETTINGS_MODULE`.
   - **No `mark_safe()` on user content** — check that user-supplied data
     (registrant names, contact details, profile fields, anything from
     outside the codebase) is never passed through `mark_safe()` or `|safe`.

2. The auditor writes its report to
   `.claude/security-audits/YYYY-MM-DD-HHMM.md`.

3. After the auditor completes, summarise:
   - Count of Critical / High / Medium / Low findings
   - Top 3 issues with one-line descriptions
   - Whether any of the `## Invariants` in [CLAUDE.md](../../CLAUDE.md)
     are violated

4. Ask the user if they want to create Linear tickets for any Critical or
   High findings. If yes, create them via the `ticket-authoring-guide`
   skill so the tickets follow the standard contract.
