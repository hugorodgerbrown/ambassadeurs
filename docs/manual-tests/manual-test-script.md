# Manual test script — Ambassadeurs (deployed site)

A step-by-step smoke and acceptance script for a human tester to run against a
**deployed** environment (staging or production). It is deliberately black-box:
it exercises the app through the browser and email only, so no shell or database
access is required.

## How to keep this script maintainable

- One numbered **scenario** per user-visible flow. When a flow changes, edit its
  scenario in place — do not add a parallel copy.
- Each step states an **action** and an **expected result**. If the expected
  result no longer matches the app, that is either a bug or a doc-drift; decide
  which and fix the right one.
- Keep the **route table** and **environment switches** below in sync with
  `public/urls.py`, `accounts/urls.py`, and `config/settings/base.py`. These are
  the two sections that rot fastest.
- Scenarios are ordered so that later ones can reuse accounts created earlier.
  Run them top to bottom on a fresh environment.
- `[ ]` checkboxes are for a single run — copy this file per test cycle, or track
  runs in the results log at the bottom.

---

## 0. Before you start

### What you need

- The site's base URL, e.g. `https://ambassadeurs.example.com` (called
  `$BASE_URL` below).
- **Two email inboxes you control** — matching needs one ambassador and one
  referee. Gmail `+tag` aliases work (`you+amb@gmail.com`, `you+ref@gmail.com`).
  On production `DEBUG` is off, so magic links and confirmation links arrive
  **only by email** — there is no on-page link shortcut. Real inbox access is
  mandatory.
- A browser. For the language and privacy checks, a second private/incognito
  window helps (act as both parties without sharing a session).
- If the deposit tier is live (see below), **Stripe test cards** on a test
  environment, or a real card on production. Do not use real cards on staging.

### Environment switches that change expected behaviour

Confirm these with whoever owns the deploy before you start — they decide which
scenarios apply. All are env vars read in `config/settings/base.py`.

| Setting | Effect on the test | Default |
|---|---|---|
| `REGISTRATION_OPENS_AT` / `REGISTRATION_CLOSES_AT` | Outside the window, `/register/` shows the **closed** page (Scenario 12). | open (2020→2099) |
| `MATCHING_OPENS_AT` | Before this datetime, registrations queue but **no match is proposed** on the second registration. Auto-match scenarios (5) need this in the past. | open |
| `REGISTRATION_FEE_TIERS` | If the current date maps to a fee > 0, registration diverts to **Stripe Checkout** (Scenario 4). Empty = always free. | free |
| `CONTACT_WINDOW_HOURS` | The accept/decline deadline; drives expiry (Scenario 9). | 72 |
| `STRIPE_*` | Must be set for the paid tier to work at all. | unset |

### Route reference (verify against `*/urls.py` when this drifts)

Public: `/`, `/register/`, `/register/sent/`, `/register/confirm/<token>/`,
`/register/done/<role>/`, `/register/pay/`, `/register/pay/return/`,
`/register/pay/cancelled/`, `/how-it-works/`, `/faq/`, `/legal/<page>/`,
`/application-form/`, `/match/<token>/` (+ `/accept/`, `/decline/`,
`/withdraw/`, `/report-no-show/`), `/healthz/`, `/robots.txt`, `/sitemap.xml`.

Accounts: `/account/login/`, `/account/login/sent/`, `/account/login/<token>/`,
`/account/logout/`, `/account/` (detail), `/account/edit/`, `/account/delete/`,
`/account/match/`, `/account/rejoin/`, `/account/resend-confirmation/`.

---

## 1. Smoke test (unauthenticated)

Fast confidence that the deploy is up and serving. ~3 minutes.

- [ ] `GET $BASE_URL/healthz/` → **200**, plain body (liveness probe).
- [ ] `GET $BASE_URL/` → home page renders; nav, hero, and role call-to-action
      links are present. No stack trace, no unstyled page (CSS loaded).
- [ ] `GET $BASE_URL/how-it-works/` → renders; mentions the off-app application
      going to **Téléverbier** (`customer@televerbier.ch`).
- [ ] `GET $BASE_URL/faq/` → renders.
- [ ] `GET $BASE_URL/legal/privacy/` and `/legal/terms/` → render (adjust slugs
      to whatever legal pages exist).
- [ ] `GET $BASE_URL/application-form/` → redirects to the external form PDF.
- [ ] `GET $BASE_URL/robots.txt` → **200**, text.
- [ ] `GET $BASE_URL/sitemap.xml` → **200**, XML listing the static pages.
- [ ] `GET $BASE_URL/admin/` → Django login page (not an error).
- [ ] `GET $BASE_URL/debug/anything` → **404** (the debug panel must be invisible
      in production).

**Static assets**: confirm the page is styled (Tailwind `output.css` loaded) and
the browser console shows no failed requests.

---

## 2. Internationalisation (EN default, FR)

- [ ] On any public page, note the copy is **English** by default.
- [ ] Use the language switcher to select **French**. The page reloads in French
      (nav, buttons, headings translated).
- [ ] Navigate to another page — the French choice **persists**.
- [ ] Switch back to English.

> Note: translated strings are served only where the French catalogue has been
> compiled at deploy. Untranslated strings fall back to English — that is
> expected, not a bug.

---

## 3. Registration — free tier (happy path)

Do this **twice**: once as an **Ambassador**, once as a **Referee**, using your
two inboxes. You will reuse both accounts in Scenario 5.

**Ambassador** (`you+amb@…`):

- [ ] From home, choose the **Ambassador** call-to-action → `/register/?role=ambassador`.
      The form is themed for the ambassador role.
- [ ] Fill first name, last name, email, phone, preferred location, language,
      and the `prior_pass` attestation. As an ambassador, pick a **prior pass**
      of Seasonal / Annual / Mont 4 (a returning holder).
- [ ] Accept the required terms/consent checkboxes.
- [ ] Submit → redirected to **“check your email”** (`/register/sent/`). The page
      does **not** confirm whether the address was new (no enumeration).
- [ ] Open the inbox → a **confirmation email** arrives with a signed link.
- [ ] Click the link → `/register/confirm/<token>/` → registration becomes
      **VERIFIED** and you land on `/register/done/ambassador/`.

**Referee** (`you+ref@…`), in a second browser/incognito:

- [ ] `/register/?role=referee`, themed for referee.
- [ ] As a referee, the `prior_pass` attestation must be **None** (genuinely
      new — never held a pass). Fill the rest, accept terms, submit.
- [ ] Confirmation email arrives; click to verify → `/register/done/referee/`.

> **Do not confirm the referee yet if you want to test the "match on second
> registration" trigger cleanly** — see Scenario 5.

Edge checks:

- [ ] Register again with an **already-verified** email → you still land on
      `/register/sent/` (no "already registered" leak). The owning inbox
      receives a **sign-in** link instead of a new confirmation.
- [ ] Submit the form with a **missing required field** → inline validation
      error, no email sent.
- [ ] Rapidly submit the registration form many times from one inbox → after 5
      POSTs/hour per email (or 30/hour per IP) you get an **HTTP 429**.

---

## 4. Registration — paid deposit tier (only if `REGISTRATION_FEE_TIERS` maps to a fee > 0)

Skip this scenario entirely when registration is free. On a test environment use
Stripe **test cards**; the success card is `4242 4242 4242 4242`, any future
expiry, any CVC. For Swiss flows also exercise **TWINT** if enabled.

- [ ] Complete the registration form as in Scenario 3 and submit.
- [ ] You are diverted to **`/register/pay/`** → Stripe hosted Checkout, showing
      the deposit amount in CHF.
- [ ] Pay with the test card → redirected to **`/register/pay/return/`** →
      registration is confirmed / verified and the deposit is recorded as
      **HELD**.
- [ ] Repeat, but **cancel** on the Stripe page → redirected to
      **`/register/pay/cancelled/`**; the registration is **not** placed in the
      pool (no unpaid registration is matchable).
- [ ] (If admin access) confirm a `Payment` row exists in **HELD** state for the
      registration.

Deposit lifecycle to verify later via the match outcomes:
successful mutual accept → **CAPTURED**; season ends unmatched or good-faith
cancel → **REFUNDED** (a real Stripe refund); post-accept no-show → **FORFEITED**.

---

## 5. Matching — happy path (mutual accept + contact reveal)

This is the core product. It needs **one VERIFIED ambassador and one VERIFIED
referee** with **no active match** and a shared/compatible profile.
`MATCHING_OPENS_AT` must be in the past.

Setup: verify the ambassador first (Scenario 3), then verify the referee. The
engine proposes a match **synchronously** when the second eligible party is
verified.

- [ ] After the second verification, **both inboxes receive a match
      notification email** with a signed match link.
- [ ] **Ambassador** opens `/match/<token>/`. The page shows the referee's
      **first name and initials only**.
- [ ] 🔒 **Privacy invariant** — on this proposed page, the ambassador sees
      **no email and no phone** for the referee. Confirm neither appears
      anywhere on the page or in page source.
- [ ] Ambassador clicks **Accept** → `/match/<token>/accept/`. Match moves to
      **PENDING** (one-sided). The referee is notified.
- [ ] 🔒 Still no contact details revealed to either side while only one has
      accepted.
- [ ] **Referee** opens their match link and clicks **Accept**. Match becomes
      **ACCEPTED**.
- [ ] ✅ **Now, and only now**, both match pages reveal the **other party's email
      and phone**, plus guidance to complete the off-app application (sent to
      Téléverbier).
- [ ] Both parties can reach the same match view from their account at
      `/account/match/` while logged in.

---

## 6. Matching — decline path (pause + rejoin)

Use a fresh pair (register two more accounts, or re-run after Scenario 8/rejoin).

- [ ] A match is **PROPOSED** and both notified.
- [ ] One party opens their match link and clicks **Decline**
      (`/match/<token>/decline/`). Match becomes **DECLINED**.
- [ ] The **declining** party's registration is set to **PAUSED** (out of the
      pool). Their account page offers **“Rejoin the queue”**.
- [ ] The party who did **not** decline is re-queued to the **front** and can be
      matched again.
- [ ] 🔒 No contact details were revealed at any point in a decline.
- [ ] The paused party clicks **Rejoin the queue** (`/account/rejoin/`) → back in
      the pool, eligible to match again.

---

## 7. Matching — one-sided accept then withdraw

- [ ] From a **PENDING** match (one side accepted — Scenario 5 up to the first
      accept), the party who accepted opens their match and clicks **Withdraw**
      (`/match/<token>/withdraw/`).
- [ ] Confirm the match is torn down and both parties return to the expected
      queue state (verify the wording/outcome against the current UI; update
      this step if the behaviour has changed).
- [ ] 🔒 No contact details were revealed.

---

## 8. Post-accept no-show (report + refund)

Requires an **ACCEPTED** match (Scenario 5 completed).

- [ ] One party opens the accepted match and uses **Report no-show**
      (`/match/<token>/report-no-show/`).
- [ ] The report is trusted immediately: the match becomes **CANCELLED**, the
      **reporter** is re-queued to the front, and the **reported** party's
      registration is set to **SUSPENDED** (removed from the pool).
- [ ] (Paid tier) the reported party's deposit is **FORFEITED**; the reporter's
      deposit outcome follows the season rules.

---

## 9. Contact-window expiry (time-dependent)

The cron job `expire_matches` runs hourly on the `ambassadeurs-expire-matches`
service and sweeps matches whose contact window (`CONTACT_WINDOW_HOURS`) has
lapsed without both parties accepting.

- [ ] Create a PROPOSED (or PENDING) match and **do not respond**.
- [ ] After the window elapses and the cron has run, the match becomes
      **EXPIRED**. The non-responder is **PAUSED**; a one-sided accepter is
      re-queued to the **front**.
- [ ] 🔒 Expiry never reveals contact details.

> This scenario is slow by design. On a test environment, ask the deploy owner to
> lower `CONTACT_WINDOW_HOURS` or trigger `manage.py expire_matches` manually so
> you do not wait 72 hours.

---

## 10. Magic-link login / logout

- [ ] `GET /account/login/` → single email field.
- [ ] Submit a **known** address → redirected to `/account/login/sent/`
      ("check your inbox"). A magic link is emailed.
- [ ] Submit an **unknown** address → **same** sent page (no enumeration); no
      email arrives.
- [ ] Open the magic link → `/account/login/<token>/` shows **"Sign in as
      you@example.com"** with a Confirm button. Note you are **not yet logged
      in** (this GET is prefetch-safe).
- [ ] Click **Confirm** → logged in, redirected to `/account/`.
- [ ] Re-open the **same** link within an hour → it logs you in again
      (idempotent within its window).
- [ ] Wait past the 1-hour expiry (or use an old link) → error page (**HTTP
      400**), no login.
- [ ] Tamper with the token (change a character) → error page, no login.
- [ ] `POST /account/logout/` (the Log out control) → logged out, redirected to
      the home page.

---

## 11. Account self-service (authenticated)

Log in first (Scenario 10).

- [ ] `/account/` shows the user's registration: role, status, and — if matched —
      a link to the match.
- [ ] `/account/edit/` — change phone / preferred location / language → save →
      values persist on `/account/`.
- [ ] `/account/resend-confirmation/` (only relevant while UNVERIFIED) resends
      the confirmation email.
- [ ] `/account/rejoin/` appears and works only when the registration is PAUSED
      (see Scenario 6).
- [ ] `/account/delete/` — delete the account. Confirm the user and their
      registration are removed and you are logged out. (Paid tier: any deposit
      history row survives deletion by design.)

---

## 12. Closed / not-yet-open states

- [ ] With registration **closed** (`REGISTRATION_CLOSES_AT` in the past, or
      `REGISTRATION_OPENS_AT` in the future), `GET /register/` shows the
      **registration-closed** page, not the form.
- [ ] With `MATCHING_OPENS_AT` in the **future**, verifying a second eligible
      party does **not** propose a match — both simply queue. No match email
      arrives.

---

## 13. Privacy invariant sweep (do this every cycle)

The single most important guarantee. Across every state above, confirm:

- [ ] **PROPOSED** — each party sees the other's **first name + initials** only.
- [ ] **PENDING** (one accept) — still **no** email/phone.
- [ ] **DECLINED / EXPIRED / withdrawn** — **no** email/phone, ever.
- [ ] **ACCEPTED** (both accept) — email **and** phone revealed to **both**.
- [ ] Check page **source**, not just the rendered page, for leaked contact data
      before mutual accept.

If contact PII (email or phone) is ever visible before both parties have
accepted, that is a **release-blocking** defect. Stop and report it.

---

## 14. Admin oversight (staff only)

- [ ] Log into `/admin/` as a staff user.
- [ ] Registrations, Matches, and Payments are all listed with an explicit admin
      class (searchable/filterable).
- [ ] A match's state and the parties' statuses reflect what you exercised above.
- [ ] Confirm you cannot construct an **ineligible** match by hand (the engine's
      eligibility rules are the guard; admin should not be a backdoor around
      Invariant 2).

---

## Results log

Copy this block per test run.

```
Date:              YYYY-MM-DD
Environment:       staging | production
Base URL:
Tester:
Build / commit:
Registration fee tier active?  free | paid (CHF ___)

Scenario                              Result   Notes
 1  Smoke                             [ ] pass / [ ] fail
 2  i18n                              [ ] pass / [ ] fail
 3  Register (free)                   [ ] pass / [ ] fail
 4  Register (paid deposit)           [ ] pass / [ ] n/a
 5  Match happy path + reveal         [ ] pass / [ ] fail
 6  Decline + rejoin                  [ ] pass / [ ] fail
 7  Withdraw                          [ ] pass / [ ] fail
 8  No-show report + forfeit          [ ] pass / [ ] fail
 9  Expiry (cron)                     [ ] pass / [ ] n/a
10  Magic-link login/logout           [ ] pass / [ ] fail
11  Account self-service              [ ] pass / [ ] fail
12  Closed / not-open states          [ ] pass / [ ] n/a
13  Privacy invariant sweep           [ ] PASS / [ ] FAIL  (release blocker)
14  Admin oversight                   [ ] pass / [ ] fail

Defects raised (Linear VERB-___):
```
