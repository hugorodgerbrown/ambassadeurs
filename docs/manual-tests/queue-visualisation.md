# User Testing Scenarios — Live Queue Visualisation (Design-iteration pass)

> **Branch**: `claude/sad-dijkstra-dcebc2` — no Linear ticket; design-iteration
> pass covering glyph swap (skier / snowboarder / gondola), referee mirror
> (`-scale-x-100`), matched-zone softening to muted grey, and the current-user
> "you" highlight in amber/gold.

## Prerequisites

| Item | Detail |
|------|--------|
| Dev server | `uv run python manage.py runserver` on `http://localhost:8000` |
| Tailwind watcher | `npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch` |
| `DEBUG=True` | Required for Scenarios 1–9 and 15 (`/debug/components/`). The default development settings (`config/settings/development.py`) set this automatically. |
| Database | Scenarios 1–9 use the DEBUG gallery's synthetic hard-coded data — no DB rows needed. Scenarios 10–12 render from the live DB; no special seed data is required. |
| Dark-mode activation | No UI toggle exists. Activate by running `document.documentElement.classList.add('dark')` in the browser DevTools console. Remove with `document.documentElement.classList.remove('dark')`. |
| Pre-open env setup | Scenario 12 only: temporarily set `MATCHING_OPENS_AT=2099-10-01T00:00:00+01:00` in `.env`; restart the dev server; revert and restart after testing. |

---

## Section 1: DEBUG Component Gallery — /debug/components/

### Scenario 1: Gallery loads and all seven queue-visualisation cards render

**Goal**: Confirm the gallery page loads without error, the "Live queue" section is at the top, and seven labelled cards are present.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads; HTTP 200; browser tab title reads "Components · DEBUG" |
| 2 | Scroll to the section with heading "Live queue" | Section is visible at the top of the page, above the Match status panel section |
| 3 | Count the labelled queue cards | Exactly seven cards appear in this order: "Pre-match — registration open, matching not started"; "Live — referees matched instantly (ambassadors queue)"; "Live — ambassadors matched instantly (referees queue)"; "Live — large pool (grid caps, trailing ellipsis)"; "Live — you are waiting (ambassador, position 3)"; "Live — you are waiting (referee, position 2)"; "Live — you are matched (pair 2)" |
| 4 | Verify no card shows a Django error traceback or blank glyph area | Each card renders an `id="queue-snapshot"` container with "Ambassadors", "Matched", and "Referees" zone labels visible |

---

### Scenario 2: Pre-open state — countdown in the centre zone; skier and snowboarder glyphs in the waiting zones

**Goal**: Confirm the centre zone displays countdown text (not glyphs) when `is_open=False`, and that the waiting zones render the new skier and snowboarder icons.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 1: "Pre-match — registration open, matching not started" | Card is visible |
| 3 | Read the card sub-header below "Who's in the queue" | Text reads "Matching begins on 1st October 2026." |
| 4 | Check the "Ambassadors" zone (left column) | Count header shows **6**; six skier icons in alpine red; no amber/gold icon present |
| 5 | Check the "Matched" centre zone | People count header shows **0**; text "Matching begins in 83 days" appears in place of any glyph grid |
| 6 | Check the "Referees" zone (right column) | Count header shows **4**; four snowboarder icons in brand blue |

---

### Scenario 3: Referee snowboarders mirror to face the centre

**Goal**: Verify the `-scale-x-100` CSS class is applied to all referee snowboarder glyphs — making them face left (toward the centre zone) — while ambassador skier glyphs face right.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 3: "Live — ambassadors matched instantly (referees queue)"; observe the five snowboarder icons | Five snowboarder glyphs visible in the "Referees" zone |
| 3 | Open DevTools; inspect any referee snowboarder `<svg>` element | The `<svg>` carries the class `-scale-x-100` alongside `text-secondary` |
| 4 | Locate card 2: "Live — referees matched instantly (ambassadors queue)"; inspect any ambassador skier `<svg>` | No `-scale-x-100` class on any skier element |
| 5 | Compare the visual orientation of the two icons | The snowboarder faces **left** (toward the centre); the skier faces **right** (toward the centre); both converge on the Venn overlap |

---

### Scenario 4: Live — referees matched instantly (ambassador queue, gondola pairs, "Instant match*" on the empty referee side)

**Goal**: Confirm that when ambassadors are queuing and the referee side is empty, the sub-header and the empty referee zone both show "Instant match*", and matched pairs render as muted-grey gondola glyphs.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 2: "Live — referees matched instantly (ambassadors queue)" | Card is visible |
| 3 | Read the card sub-header | Text reads "* Next referee will be matched immediately on registration." |
| 4 | Check the "Ambassadors" zone | Count **5**; five red skier icons |
| 5 | Check the "Referees" zone | Count **0**; text "Instant match*" in place of glyphs |
| 6 | Check the "Matched" zone | People count **6** (3 pairs × 2); three gondola/cable-car icons in muted grey (`text-meta`) |
| 7 | Confirm no gondola is amber/gold | All three gondola icons render in the same muted grey; none are highlighted |

---

### Scenario 5: Live — ambassadors matched instantly (referee queue, gondola pairs, "Instant match*" on the empty ambassador side)

**Goal**: Confirm the symmetric case: referees queuing, ambassador side empty, "Instant match*" on the ambassador side.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 3: "Live — ambassadors matched instantly (referees queue)" | Card is visible |
| 3 | Read the card sub-header | Text reads "* Next ambassador will be matched immediately on registration." |
| 4 | Check the "Ambassadors" zone | Count **0**; text "Instant match*" in place of glyphs |
| 5 | Check the "Referees" zone | Count **5**; five brand-blue snowboarder icons, each mirrored to face left |
| 6 | Check the "Matched" zone | People count **6**; three muted-grey gondola icons |

---

### Scenario 6: Large pool — grid caps and trailing ellipsis in ambassador and matched columns

**Goal**: Confirm that when a column exceeds its grid cap (20 for waiting zones, 16 for the matched zone), exactly cap − 1 glyphs render followed by one muted three-dot ellipsis, while the header retains the exact count.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 4: "Live — large pool (grid caps, trailing ellipsis)" | Card is visible |
| 3 | Check the "Ambassadors" zone count header | Reads **200** |
| 4 | Count all icons in the ambassador grid | Exactly **19** red skier icons followed by one muted three-dot ellipsis glyph (20 slots occupied) |
| 5 | Check the "Matched" zone people header | Reads **120** (60 pairs × 2) |
| 6 | Count all icons in the matched grid | Exactly **15** muted-grey gondola icons followed by one muted three-dot ellipsis (16 slots) |
| 7 | Check the "Referees" zone | Count **0**; shows "Instant match*" text — no glyphs, no ellipsis |

---

### Scenario 7: "You" highlight — waiting ambassador at position 3

**Goal**: Confirm only the third skier glyph (zero-based index 2) renders in amber/gold (`text-notice`) with an accessible label; the other five render in alpine red.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 5: "Live — you are waiting (ambassador, position 3)" | Six skier icons visible in the "Ambassadors" zone |
| 3 | Check the colour of each icon in reading order (left-to-right across each row) | Icons 1, 2, 4, 5, 6 are alpine red (`text-accent`); icon **3** is visually distinct amber/gold (`text-notice`) |
| 4 | Open DevTools; inspect the third skier `<svg>` | Carries `text-notice`; has attributes `role="img"` and `aria-label="You"` |
| 5 | Inspect any other skier `<svg>` | Carries `text-accent` and `aria-hidden="true"`; no `role` or `aria-label` |
| 6 | Check the "Referees" and "Matched" zones | No amber/gold icons in either zone |

---

### Scenario 8: "You" highlight — waiting referee at position 2

**Goal**: Confirm the second snowboarder glyph (zero-based index 1) renders in amber/gold with the mirror class preserved, while the other three render in brand blue.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 6: "Live — you are waiting (referee, position 2)" | Four snowboarder icons visible in the "Referees" zone |
| 3 | Check the colour of each icon | Icons 1, 3, 4 are brand blue (`text-secondary`); icon **2** is amber/gold (`text-notice`) |
| 4 | Open DevTools; inspect the second referee `<svg>` | Carries `text-notice` and `-scale-x-100` (the mirror class is preserved on the highlighted glyph); has `role="img"` and `aria-label="You"` |
| 5 | Inspect any other referee `<svg>` | Carries `text-secondary` and `-scale-x-100`; has `aria-hidden="true"`; no amber/gold class |
| 6 | Check the "Ambassadors" and "Matched" zones | No amber/gold icons |

---

### Scenario 9: "You" highlight — matched pair at position 2

**Goal**: Confirm the second gondola glyph (zero-based index 1) renders in amber/gold while the other two remain muted grey.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 7: "Live — you are matched (pair 2)" | People count **6** (3 pairs); three gondola icons in the "Matched" zone |
| 3 | Check the colour of each gondola icon | Icons 1 and 3 are muted grey (`text-meta`); icon **2** is amber/gold (`text-notice`) |
| 4 | Open DevTools; inspect the second gondola `<svg>` | Carries `text-notice`; has `role="img"` and `aria-label="You"` |
| 5 | Inspect the first and third gondola `<svg>` | Each carries `text-meta` and `aria-hidden="true"`; no amber/gold class |
| 6 | Check the "Ambassadors" zone (count 6) and "Referees" zone (count 4) | No amber/gold icons in either waiting zone |

---

## Section 2: Standalone Queue Page — /queue/

### Scenario 10: Standalone page loads for an anonymous visitor

**Goal**: Confirm the public `/queue/` page renders the queue component from the live DB without requiring authentication.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Open a private/incognito window (no authenticated session) | No session cookie |
| 2 | Navigate to `http://localhost:8000/queue/` | HTTP 200; browser title reads "Live queue · Ski Parrainage" |
| 3 | Verify the page structure | Eyebrow "Ski Parrainage", h1 "Live queue", one `id="queue-snapshot"` card with all three zones |
| 4 | Verify count headers are integers | Each of "Ambassadors", "Matched" (people), and "Referees" headers shows a non-negative integer |
| 5 | Verify no error traceback | Page renders without exception regardless of the current DB pool state |

---

### Scenario 11: Standalone page — no "you" highlight for any visitor (known limitation)

**Goal**: Confirm the live `/queue/` view does not highlight any glyph in amber/gold; all render in their role colours or muted grey.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/queue/` as an anonymous visitor | Page loads |
| 2 | If ambassador glyphs are present, open DevTools and inspect each skier `<svg>` | All carry `text-accent`; none carry `text-notice`, `role="img"`, or `aria-label="You"` |
| 3 | If referee glyphs are present, inspect each snowboarder `<svg>` | All carry `text-secondary` and `-scale-x-100`; none carry `text-notice` |
| 4 | If matched-pair glyphs are present, inspect each gondola `<svg>` | All carry `text-meta`; none carry `text-notice` |
| 5 | Log in as a registered user (if a registration exists in the dev DB) and reload `http://localhost:8000/queue/` | Still no amber/gold glyph after login |

> **Known limitation**: the current-user highlight is not wired into the live
> `/queue/` view. `queue_snapshot_context` (called in
> `public.views.pages.queue_snapshot_page`) passes no `you_role` or `you_index`
> to `build_queue_context`, so all `you_glyph` values are `None`. The highlight
> is exercised only via the DEBUG gallery (Scenarios 7–9). This is expected for
> this iteration; wiring the current user's own queue position into the live
> view is a follow-up.

---

### Scenario 12: Standalone page — pre-open countdown state

**Goal**: Confirm `/queue/` shows the countdown text and open date when `MATCHING_OPENS_AT` is a future datetime.

> **Setup**: In `.env`, set `MATCHING_OPENS_AT=2099-10-01T00:00:00+01:00`.
> Restart the dev server. Revert and restart after testing.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | With a future `MATCHING_OPENS_AT`, navigate to `http://localhost:8000/queue/` | Page loads |
| 2 | Read the card sub-header | Text reads "Matching begins on 1st October 2099." |
| 3 | Check the "Matched" centre zone | People count **0**; countdown text "Matching begins in X days" (or "Matching begins today" if the date is today) appears — no gondola glyphs |
| 4 | Check the "Ambassadors" and "Referees" waiting zones | If unmatched VERIFIED registrations exist, their glyphs render; if empty, a dashed-circle placeholder renders |
| 5 | Revert `MATCHING_OPENS_AT` and restart the dev server | Subsequent visits show the live queue with normal open-state behaviour |

---

## Section 3: Colour and Visual Fidelity

### Scenario 13: Highlight colour is visually distinct from all three role colours — light mode

**Goal**: Confirm the amber/gold "you" glyph reads as clearly separate from alpine red (ambassador), brand blue (referee), and muted grey (matched) in the default light theme.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` in the default light theme | Page loads without the `dark` class on `<html>` |
| 2 | Locate card 5: "Live — you are waiting (ambassador, position 3)" | Six skier icons visible |
| 3 | Compare icon 3 (amber `#a8740a`) against the five red icons (`#cf3127`) | The amber icon is visually distinct — warmer, less saturated, clearly a different hue from the alpine red |
| 4 | Locate card 6: "Live — you are waiting (referee, position 2)" | Four snowboarder icons visible |
| 5 | Compare icon 2 (amber) against the three blue icons (`#1f6db8`) | The amber icon is clearly distinct from the brand blue; no ambiguity between the two colours |
| 6 | Locate card 7: "Live — you are matched (pair 2)" | Three gondola icons visible |
| 7 | Compare the amber gondola (icon 2) against the two grey gondolas (`#5f7178`) | The amber gondola stands out clearly against the muted grey; non-highlighted icons read as secondary |

---

### Scenario 14: Dark-mode colour rendering

**Goal**: Confirm all three role colours invert to their dark-mode values, and the "you" highlight shifts to the dark-mode amber token (`#e3b84a`).

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads in light mode |
| 2 | In DevTools console, run `document.documentElement.classList.add('dark')` | Page background shifts to dark navy (`#0c1417`); body text shifts to near-white (`#eaf3f4`) |
| 3 | Locate card 2: "Live — referees matched instantly (ambassadors queue)"; check the skier icons | Skiers render in bright dark-mode red (`#ef6a5e`) — noticeably brighter and lighter than the light-mode red |
| 4 | Locate card 3: "Live — ambassadors matched instantly (referees queue)"; check the snowboarder icons | Snowboarders render in bright dark-mode blue (`#4ea3e6`) |
| 5 | Check the gondola icons in the same card's "Matched" zone | Gondolas render in dark-mode grey (`#7e9298`) |
| 6 | Locate card 5: "Live — you are waiting (ambassador, position 3)"; check icon 3 | Icon 3 renders in dark-mode amber `#e3b84a` — lighter and brighter than the light-mode amber; visually distinct from both the dark-mode red skiers and the dark-mode grey gondolas |
| 7 | Locate card 7: "Live — you are matched (pair 2)"; check gondola icon 2 | Highlighted gondola in amber `#e3b84a`; the other two gondolas remain in dark-mode grey |
| 8 | Run `document.documentElement.classList.remove('dark')` | Page reverts to light mode |

---

## Section 4: Accessibility

### Scenario 15: Screen-reader attributes — "You" glyph exposed; all others hidden

**Goal**: Confirm the highlighted glyph carries `role="img"` and `aria-label="You"` for assistive-technology exposure, while all non-highlighted glyphs carry `aria-hidden="true"`, and the wrapping container carries an aggregate label.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` | Page loads |
| 2 | Locate card 5: "Live — you are waiting (ambassador, position 3)"; open DevTools and select the third skier `<svg>` | Attributes present: `role="img"` and `aria-label="You"` |
| 3 | Select any other skier `<svg>` in the same zone | Attribute `aria-hidden="true"` present; no `role` or `aria-label` |
| 4 | Select the `<div role="img">` that wraps all six skier icons | `aria-label` reads "6 ambassadors waiting" (the aggregate label for the whole pictograph group) |
| 5 | Locate card 6: "Live — you are waiting (referee, position 2)"; select the second snowboarder `<svg>` | `role="img"` and `aria-label="You"` present; class list includes `-scale-x-100` |
| 6 | Select any other snowboarder `<svg>` | `aria-hidden="true"` present; no `role` or `aria-label` |
| 7 | Locate card 7: "Live — you are matched (pair 2)"; select the second gondola `<svg>` | `role="img"` and `aria-label="You"` present |
| 8 | Select the first and third gondola `<svg>` | Each carries `aria-hidden="true"`; no `role` or `aria-label` |

---

## Section 5: Mobile Width

### Scenario 16: Component reflows at smartphone viewport width without overflow

**Goal**: Confirm the three-zone Venn diagram and glyph grids remain usable at 375 px width without zone collapse, icon overflow, or illegible text.

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to `http://localhost:8000/debug/components/` at desktop width | Page loads normally |
| 2 | Open DevTools device toolbar; set viewport to **375 × 667** (iPhone SE) | Layout reflows |
| 3 | Scroll to card 2: "Live — referees matched instantly (ambassadors queue)" | The three Venn zones remain side by side in one row; no zone collapses |
| 4 | Check the ambassador glyph grid | Skier icons wrap within the `max-w-40` flex container; no icons clip outside the card boundary |
| 5 | Check that zone labels and count numbers are readable | "Ambassadors", "Matched", "Referees" labels and integer counts are not truncated or overlapping |
| 6 | Navigate to `http://localhost:8000/queue/` at 375 px | Standalone page renders; card is contained within `px-4` padding with no horizontal scrollbar |
| 7 | Set viewport to **320 px** width | Zones remain visible and non-overlapping; content stays within the viewport |
