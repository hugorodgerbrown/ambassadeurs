---
name: screenshot
description: |
  Capture a screenshot of a named page of the running Ambassadeurs app, show it
  inline in the thread, and optionally attach it to a Linear ticket. Covers any
  public or authenticated page — home, register, register-done (per role),
  how-it-works, legal, admin, or a match accept/decline screen. Use whenever the
  user says "/screenshot", "capture/take/grab a screenshot", "attach a
  screenshot", "show me the how-it-works page", "what does the decline screen
  look like", or any similar request to see, render, or share a page — even when
  they don't use the word "screenshot". Also invoke this PROACTIVELY when you are
  implementing or reviewing a Linear ticket (VERB-xxx) whose description or
  comments ask to "attach screenshots" (or "add screenshots", "include
  screenshots"): after the UI change is in place, capture the affected page(s)
  and attach them to that ticket. Do NOT use for Figma/design mockups, for
  native-desktop or terminal screenshots, or for reading an existing image file
  the user already has.
allowed-tools: Bash, Read, Glob, Grep, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8__prepare_attachment_upload, mcp__bee16520-0a2b-446d-b267-fbf9f62cf3a8__create_attachment_from_upload
---

# Screenshot a named page

Capture a rendered page of the running Django app, drop it into the conversation,
and — when a ticket asks for it — attach it to Linear. The capture is done by a
bundled headless-Chromium script so it works without a connected browser and
produces the same result every time.

## What "a named page" means

Pages are identified by their Django URL **name**, not a guessed path. Resolve the
user's plain-English request to a route in `public/urls.py` (namespace `public`)
or the admin. Common public routes:

| Asked for | URL name | Notes |
|-----------|----------|-------|
| home / landing | `public:home` | |
| register / sign-up | `public:register` | |
| how it works | `public:how_it_works` | |
| a legal page | `public:legal` | needs `--arg page=<slug>` (e.g. `privacy`) |
| registration done | `public:register_done` | needs `--arg role=<ambassador\|referee>` |
| a match accept/decline page | `public:match` | authenticated — see below |
| Django admin | `--path /admin/` | authenticated — see below |

If you cannot map the request to a route, list the candidate routes from
`public/urls.py` and ask which one rather than screenshotting the wrong page.

## Step 1 — Make sure the app is serving

The script screenshots a **running** dev server; it does not start one. Check
reachability, and if nothing answers, start the pieces from `CLAUDE.md`:

```bash
# Build CSS once so the page is styled (skip if static/css/output.css is fresh):
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css
# Start the server in the background if http://localhost:8000 is not answering:
uv run python manage.py runserver
```

A screenshot of an unstyled page (missing `output.css`) is a bug, not a result —
build the CSS before capturing if you started the server yourself.

## Step 2 — Capture

Run the bundled script through `uv run --with playwright` so Playwright sits
alongside the project's Django. The first run downloads Chromium (~150 MB) and
then caches it. Save PNGs under `.claude/screenshots/` with a descriptive name.

```bash
uv run --with playwright python .claude/skills/screenshot/scripts/screenshot.py \
  --name public:register \
  --out .claude/screenshots/register.png
```

The script prints `SAVED <path> <resolved-url>` as its final line. Useful options:

- `--arg key=value` — fill route placeholders, e.g. `--name public:legal --arg page=privacy`.
- `--path /admin/` or `--url <full-url>` — when there is no clean route name.
- `--viewport mobile` — capture at 390×844 (default is desktop 1280×800); or pass `WIDTHxHEIGHT`.
- `--full-page` — capture the whole scrollable page rather than just the viewport.
- `--wait-selector "main"` — wait for an element before shooting (use for HTMX-loaded content).

### Authenticated pages

`prior_pass`-gated and staff pages need a session; the script mints one against the
dev database the server shares, so it Just Works locally. This only holds when the
script and the running server read the **same** database — true in a normal single
checkout, but not in a git worktree that has its own SQLite file. If an authenticated
capture fails with `no such table` or a blank/logged-out page, point both at one
database (run the server from the same checkout as the script).

- **Admin pages** — add `--admin` (authenticates as the first superuser). If none
  exists the script tells you to run `createsuperuser`.
- **As a specific user** — add `--login-email someone@example.com` (lower-cased for you).
- **Match accept/decline pages** — these are authenticated by a *signed token in the
  URL*, not a cookie. Pass `--match <match_pk>` (optionally `<match_pk>:<registration_pk>`
  to choose a side; defaults to the ambassador). The script mints a valid
  `make_match_access_token` and builds the `public:match` URL. Find a match pk with
  `uv run python manage.py shell -c "from matching.models import Match; print(Match.objects.values_list('pk', flat=True)[:5])"`.
  Never paste a real production token; always mint one locally.

## Step 3 — Put it in the thread

Read the saved PNG so it renders inline for the user:

- Use the `Read` tool on the `.claude/screenshots/<name>.png` path.

State which page and viewport it shows. If the user only wanted to *see* the page,
stop here — do not attach to Linear unless asked or unless a ticket requests it
(see "When a ticket asks for screenshots").

## Step 4 — Attach to a Linear ticket (when asked)

Attach when the user says so, or when acting on a ticket that requests screenshots.

1. **Identify the ticket.** Prefer an explicit `VERB-xxx` from the user. Otherwise
   derive it from the branch name:
   `git rev-parse --abbrev-ref HEAD` → extract the `VERB-\d+`. If you cannot
   determine the ticket, ask rather than guessing.
2. **Upload via the Linear MCP** (three steps — do not base64 the file):
   - `prepare_attachment_upload` with `issue` (the `VERB-xxx` identifier), `filename`
     (e.g. `register-desktop.png`), `contentType: image/png`, and `size` — the exact
     byte count from `wc -c < <path>`.
   - `PUT` the raw bytes to `uploadRequest.url`, sending **every** header in
     `uploadRequest.headers` verbatim (omitting or re-casing any returns 403; the
     signed URL expires in 60 s, so do this immediately):
     ```bash
     curl -X PUT --data-binary @.claude/screenshots/register.png \
       -H "content-type: image/png" \
       -H "<each-signed-header: value from uploadRequest.headers>" \
       "<uploadRequest.url>"
     ```
   - `create_attachment_from_upload` with the same `issue`, the `assetUrl` from step
     one, and a `title` (e.g. "Register page — desktop").
3. Confirm to the user with the ticket link and which page/viewport was attached.

## When a ticket asks for screenshots

While implementing or reviewing a `VERB-xxx` whose description or comments include
"attach screenshots" (or "add/include screenshots"), treat that as a definition-of-
done item: once the relevant UI is built, work out which page(s) changed, capture
each (consider both `--viewport desktop` and `--viewport mobile` if the change is
responsive), and attach them to that same ticket via Step 4. Mention in your summary
that you did so.

## Conventions

- Keep PNGs in `.claude/screenshots/`; name them `<page>[-<viewport>].png`.
- These are throwaway build artefacts — they do not belong in git. If `.claude/`
  is tracked, add `.claude/screenshots/` to `.gitignore` rather than committing images.
- Never mint or attach a token from production; screenshots are a local-dev activity.
