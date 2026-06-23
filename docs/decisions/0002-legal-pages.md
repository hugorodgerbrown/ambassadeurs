# 0002 — Legal pages and naming the data controller

Status: accepted (VERB-5)

## Context

VERB-5 adds the standard legal documents (Privacy Policy, Cookie Policy, Terms of
Use). CLAUDE.md says public branding is 4 Vallées-neutral and the operating company
(Groupe Télé-Thyon SA) is kept out of user-facing copy.

## Decisions

- **The data controller is named in the Privacy Policy only.** A privacy policy is
  legally required to identify the controller, so the Privacy Policy names **Groupe
  Télé-Thyon SA** (back-office `caissier@tele-thyon.ch`). This is the necessary
  exception to the 4 Vallées-neutral rule; all marketing/registration copy stays
  neutral.

- **"GDPR" is folded into the Privacy Policy** as a "Your rights (GDPR / Swiss FADP)"
  section rather than a separate page — the ticket lists GDPR as a required document,
  but data-subject rights belong inside the privacy notice.

- **One parameterised view** (`public.views.legal_page`) validates the page slug
  against a fixed set (`privacy` / `cookies` / `terms`) and 404s otherwise, rather
  than three near-identical views.

- **Content is a first-draft baseline.** Each page carries a "last updated" date and a
  note that it needs legal review before launch. The copy is factual and grounded in
  the data-minimisation stance and the 72-hour / back-of-queue rules already in the
  product.

## Consequences

Legal pages are linked site-wide from `templates/includes/footer.html`, included from
`base.html`. Adding a future document means adding its slug to `LEGAL_PAGES` and a
`templates/public/legal/<slug>.html` template.
