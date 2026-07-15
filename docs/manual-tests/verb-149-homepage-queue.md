# User Testing Scenarios — VERB-149: Homepage Queue Card

> **Linear ticket**: VERB-149 "Mount the Queue component"
>
> **What changed**: The live queue snapshot pictograph (`templates/includes/_queue_snapshot.html`, built in VERB-145) is now included on the public homepage (`/`), placed between the "How it works" section and the "Which role are you?" role cards. It is gated behind `SHOW_HOMEPAGE_QUEUE` (default `false`), so the homepage renders as before until the flag is enabled. The standalone `/queue/` page is unchanged. A referee-figure fix applies `[transform:scaleX(-1)]` so referee glyphs face the centre of the Venn diagram.

## Prerequisites

| Item | Detail |
|------|--------|
| Dev server | `uv run python manage.py runserver` on `http://localhost:8000` |
| Tailwind watcher | `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch` |
| `DEBUG=True` | Development settings set this automatically. Required for `/debug/components/` (Scenarios 7–8). |
| `SHOW_HOMEPAGE_QUEUE` | Set in `.env`; requires a dev server restart when changed. Default is unset (off). |
| Pre-open env var | Scenario 6: temporarily set `MATCHING_OPENS_AT=2099-10-01T00:00:00+01:00` in `.env` and restart. Revert and restart after testing. |
| Email | In development, signed links are written to the terminal running the dev server (`EMAIL_BACKEND = console`). |
| DB state for pool scenarios | Scenarios 4–5 need at least one VERIFIED ambassador or referee registration with no active match. Register via the public form, click the confirmation link from the console, then verify status at `/admin/matching/registration/`. |
| French language | No UI language switcher exists yet. Activate French via browser preferences: Chrome — Settings → Languages → drag "Français" to top → relaunch; Firefox — Preferences → Language → add "Français" → move to top. |

## Section 1: Feature Flag Behaviour

### Scenario 1: SHOW_HOMEPAGE_QUEUE off — queue card is absent

**Goal**: Confirm the homepage renders exactly as before when the flag is off: no queue card, and the role cards follow directly after "How it works".

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Confirm `.env` does not contain `SHOW_HOMEPAGE_QUEUE=true` (or explicitly sets it `false`) | Flag is off |
| 2 | Navigate to `http://localhost:8000/` | Page loads; HTTP 200; browser tab title reads "Ski Parrainage · 2026/27" |
| 3 | Scroll from the hero downward | Sections appear in order: hero → "How it works" four-card grid → "Which role are you?" role cards — no queue card between them |
| 4 | Use "Find in page" (`Cmd+F` / `Ctrl+F`) and search for "Who's in the queue" | No match found — the heading is absent |
| 5 | Open DevTools → Elements and search for `id="queue-snapshot"` | No such element exists in the DOM |
| 6 | Navigate to `http://localhost:8000/queue/` | Standalone queue page loads normally (HTTP 200); it is unaffected by the flag |

### Scenario 2: SHOW_HOMEPAGE_QUEUE on — card renders in the correct position

**Goal**: Confirm the queue card appears between "How it works" and "Which role are you?" when the flag is enabled, and renders without errors even with an empty pool.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Add `SHOW_HOMEPAGE_QUEUE=true` to `.env` and restart the dev server | Flag is on |
| 2 | Navigate to `http://localhost:8000/` | Page loads; HTTP 200 |
| 3 | Scroll past the hero and the "How it works" grid | A card with the heading "Who's in the queue" is visible; its outer `<div>` carries `id="queue-snapshot"` |
| 4 | Verify the card's position | Queue card is below the "How it works" section and above the "Which role are you?" heading |
| 5 | Verify the three zone labels | "Ambassadors" (left, alpine red label), "Matched" (centre, muted label), "Referees" (right, blue label) — all three present |
| 6 | Verify no Django traceback and no blank zone area | Each zone shows either count glyphs, "Instant match*" text, or a dashed-outline placeholder; no raw template errors |

## Section 2: Open Season — Live Pool Counts

### Scenario 3: Count parity — homepage card shows the same numbers as /queue/

**Goal**: Confirm both surfaces draw from the same query and display identical counts.

> **Prerequisite**: `SHOW_HOMEPAGE_QUEUE=true`. Pool counts can be any values; only the agreement between the two pages is verified.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/` | Homepage loads |
| 2 | Scroll to the queue card; record all three zone figures | Note Ambassadors count (A), Matched people figure (M), Referees count (R) |
| 3 | Without changing pool data, navigate to `http://localhost:8000/queue/` | Standalone queue page loads |
| 4 | Read the three zone figures on `/queue/` | Compare with the values noted in step 2 |
| 5 | Confirm all three numbers match exactly | A, M, and R are identical on both pages — both call the same `queue_snapshot_context` function from `matching.selectors` |

### Scenario 4: Ambassadors waiting, no referees — "Instant match*" on the referee side

**Goal**: Confirm the "Instant match*" label and subheader appear when ambassadors are queuing but no referees are waiting.

> **Setup**: Register `amb-test@example.com` at `http://localhost:8000/register/ambassador/`, then click the confirmation link from the dev server console. Verify at `/admin/matching/registration/` that this registration is `VERIFIED` and that no VERIFIED referee registrations without an active match exist.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Confirm pool state: ≥ 1 ambassador waiting, 0 referees waiting | DB state is correct |
| 2 | Navigate to `http://localhost:8000/` with `SHOW_HOMEPAGE_QUEUE=true` | Homepage loads |
| 3 | Scroll to the queue card | Card is visible |
| 4 | Read the subheader text below "Who's in the queue" | Text reads exactly: "* Next referee will be matched immediately on registration." |
| 5 | Check the "Ambassadors" zone (left column) | Count is ≥ 1; ambassador skier glyphs visible in alpine red |
| 6 | Check the "Referees" zone (right column) | Count shows 0; the text "Instant match*" appears where glyphs would be |
| 7 | Confirm no dashed-outline placeholder occupies the Referees zone alongside "Instant match*" | Only the "Instant match*" label is present in the right zone's glyph area |

### Scenario 5: Referees waiting, no ambassadors — "Instant match*" on the ambassador side

**Goal**: Confirm the symmetric case: the nudge appears on the left (Ambassadors) side when referees are queuing and no ambassadors are waiting.

> **Setup**: Register `ref-test@example.com` at `http://localhost:8000/register/referee/` and confirm the email link. Verify zero VERIFIED ambassador registrations without an active match exist.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Confirm pool state: 0 ambassadors waiting, ≥ 1 referee waiting | DB state is correct |
| 2 | Navigate to `http://localhost:8000/` | Homepage loads |
| 3 | Scroll to the queue card | Card is visible |
| 4 | Read the subheader | Text reads: "* Next ambassador will be matched immediately on registration." |
| 5 | Check the "Ambassadors" zone (left column) | Count shows 0; the text "Instant match*" appears in the left zone's glyph area |
| 6 | Check the "Referees" zone (right column) | Count is ≥ 1; referee figure glyphs visible in brand blue |
| 7 | Confirm "Instant match*" is in the Ambassadors zone only | The label marks the empty side — the role whose next registrant is matched immediately |

## Section 3: Pre-Open State

### Scenario 6: Matching not yet open — subheader shows open date; centre zone shows countdown

**Goal**: Confirm that with a future `MATCHING_OPENS_AT`, the subheader displays the opening date and the Matched zone shows countdown text instead of pair glyphs.

> **Setup**: Add `MATCHING_OPENS_AT=2099-10-01T00:00:00+01:00` to `.env`, ensure `SHOW_HOMEPAGE_QUEUE=true`, and restart the dev server. Revert `MATCHING_OPENS_AT` and restart after testing.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | With a future `MATCHING_OPENS_AT`, navigate to `http://localhost:8000/` | Page loads; HTTP 200 |
| 2 | Scroll to the queue card | Card visible with heading "Who's in the queue" |
| 3 | Read the subheader | Text reads: "Matching begins on 1st October 2099." |
| 4 | Check the "Matched" zone (centre column) | People count shows 0; text "Matching begins in [N] days" appears where pair glyphs would be (N is the whole-day count from today to 2099-10-01) |
| 5 | Check the "Ambassadors" and "Referees" zones | Each shows its waiting count; glyphs render if registrations exist; dashed-outline placeholder renders if count is 0 |
| 6 | Confirm no "Instant match*" text appears anywhere in the card | The nudge is suppressed when matching is not yet open |
| 7 | Navigate to `http://localhost:8000/queue/` | Standalone page shows the same subheader and countdown |
| 8 | Revert `MATCHING_OPENS_AT` in `.env` and restart the dev server | Both pages return to live open-state behaviour |

## Section 4: Glyph Orientation

### Scenario 7: Referee figures carry the horizontal-flip class; ambassador skiers do not

**Goal**: Verify every referee figure `<svg>` carries `[transform:scaleX(-1)]` so it faces left (toward the Venn centre), while ambassador skier elements are unmirrored.

> Uses the DEBUG component gallery for deterministic synthetic data — no DB setup required.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads (HTTP 200); `DEBUG=True` required |
| 2 | Scroll to the "Live queue" section | Labelled queue cards are visible |
| 3 | Locate a card where referees are queuing (e.g. "ambassadors matched instantly (referees queue)") | Referee figures appear in the "Referees" zone in brand blue |
| 4 | Observe the referee figures visually | Figures face left (toward the Venn centre overlap zone) |
| 5 | Open DevTools; inspect any referee figure `<svg>` in that card | `class` includes `[transform:scaleX(-1)]` alongside `h-6 w-6` and `text-secondary` |
| 6 | Locate a card where ambassadors are queuing; inspect any ambassador skier `<svg>` | `class` contains `text-accent` but no `[transform:scaleX(-1)]`; the element is unmirrored |
| 7 | In a card showing both roles, compare orientations | Ambassador skiers face right; referee figures face left — both groups converge on the Venn centre |

### Scenario 8: Orientation is preserved on the live homepage card

**Goal**: Confirm the glyph orientation fix applies when the component renders from live DB data on the homepage.

> **Prerequisite**: `SHOW_HOMEPAGE_QUEUE=true`; at least one VERIFIED referee registration with no active match so referee glyphs are drawn.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/` | Homepage loads |
| 2 | Scroll to the queue card; locate the "Referees" zone (right column) | Referee figure glyphs are visible |
| 3 | Observe the direction the figures face | Figures face left (toward the Venn centre) |
| 4 | Open DevTools; inspect any referee `<svg>` in the Referees zone | Class list includes `[transform:scaleX(-1)]` |
| 5 | If ambassador glyphs are also visible in the left zone, inspect any one | No `[transform:scaleX(-1)]` on ambassador skier elements |

## Section 5: Bilingual Rendering

### Scenario 9: French browser — card renders without errors; untranslated strings fall back to English

**Goal**: Confirm the homepage and queue card render correctly under French `Accept-Language`. Strings with French translations appear in French; queue snapshot strings not yet in the French catalogue display in English without errors.

> **Note**: The UI language switcher is hidden pre-launch. The queue snapshot strings ("Who's in the queue", "Ambassadors", "Referees", "Matched", "Matching begins on …", "Instant match*", etc.) are wrapped for translation but their French `msgstr` entries may not be compiled yet — VERB-145/149 landed after the last catalogue rebuild. Django falls back to the English source string when a translation is absent. This is expected per ADR 0016 and is not a bug.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Move "Français" to the top of the browser language list and reload (see Prerequisites) | Browser sends `Accept-Language: fr` |
| 2 | Navigate to `http://localhost:8000/` with `SHOW_HOMEPAGE_QUEUE=true` | Page loads; HTTP 200; no Django traceback |
| 3 | Inspect `<html lang="…">` in DevTools | `lang="fr"` — `LocaleMiddleware` has activated the French locale |
| 4 | Check the visually-hidden "Skip to main content" link at the top of the DOM | Rendered as "Aller au contenu principal" (this string is translated in the catalogue) |
| 5 | Scroll to the queue card | Card renders without exceptions; all three zones show their counts; no raw `msgid` strings appear in visible page text |
| 6 | Read the queue card heading and zone labels | May display in English if French translations are not yet compiled — English fallback is correct; no error copy or garbled output |
| 7 | Navigate to `http://localhost:8000/queue/` | Standalone page renders without errors in the French browser; same fallback behaviour |
| 8 | Reset browser language preference to English | Subsequent visits restore the default English UI |
