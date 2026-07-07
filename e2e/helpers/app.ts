/**
 * App-flow helpers and the single source of truth for routes and selectors.
 *
 * Everything the suite knows about the app's URLs and DOM lives here, so when a
 * template changes there is exactly one file to reconcile. Field selectors use
 * Django's default auto-generated ids (`id_<field>`), which are stable across
 * copy changes; action selectors key off the form `action` attribute (e.g.
 * `form[action$="/accept/"]`), which is likewise independent of i18n copy. See
 * matching/forms.py and templates/public/partials/match_actions.html.
 */
import { expect, type Browser, type Page } from "@playwright/test";
import { Mailbox } from "./mail";

export const ROUTES = {
  home: "/",
  register: (role: "ambassador" | "referee") => `/register/?role=${role}`,
  registerSent: "/register/sent/",
  howItWorks: "/how-it-works/",
  faq: "/faq/",
  healthz: "/healthz/",
  robots: "/robots.txt",
  sitemap: "/sitemap.xml",
  login: "/account/login/",
  loginSent: "/account/login/sent/",
  account: "/account/",
  accountEdit: "/account/edit/",
  accountDelete: "/account/delete/",
  accountMatch: "/account/match/",
  accountRejoin: "/account/rejoin/",
  logout: "/account/logout/",
} as const;

/** Link path patterns for pulling signed URLs out of emails (see mail.ts). */
export const LINK = {
  confirm: /\/register\/confirm\/[^/]+\//,
  login: /\/account\/login\/(?!sent\/)[^/]+\//,
  match: /\/match\/[^/]+\/$/,
} as const;

/** Registration form field ids (Django default `id_<field>`). */
export const FIELD = {
  firstName: "#id_first_name",
  lastName: "#id_last_name",
  email: "#id_email",
  phone: "#id_phone",
  preferredLocation: "#id_preferred_location",
  preferredLanguage: "#id_preferred_language",
  nationality: "#id_nationality",
  priorPass: "#id_prior_pass",
  termsAccepted: "#id_terms_accepted",
} as const;

/** Match-action forms, keyed on their `action` suffix. */
export const ACTION = {
  accept: 'form[action$="/accept/"] button[type="submit"]',
  decline: 'form[action$="/decline/"] button[type="submit"]',
  withdraw: 'form[action$="/withdraw/"] button[type="submit"]',
  reportNoShow: 'form[action$="/report-no-show/"] button[type="submit"]',
} as const;

export interface Participant {
  role: "ambassador" | "referee";
  email: string;
  firstName: string;
  lastName: string;
  phone: string;
}

let seq = 0;
/** A unique participant identity per call, so specs never collide on email. */
export function makeParticipant(
  role: "ambassador" | "referee",
  runId: string,
): Participant {
  seq += 1;
  const tag = `${runId}-${seq}`;
  return {
    role,
    email: `e2e-${role}-${tag}@example.test`,
    firstName: role === "ambassador" ? "Amba" : "Refe",
    lastName: `Test-${tag}`,
    phone: `+417900${String(100000 + seq).slice(-6)}`,
  };
}

/** Select the first non-placeholder option of a native <select>. */
async function selectFirstReal(page: Page, selector: string): Promise<void> {
  const values = await page
    .locator(`${selector} option`)
    .evaluateAll((opts) =>
      (opts as HTMLOptionElement[])
        .map((o) => o.value)
        .filter((v) => v !== ""),
    );
  expect(values.length, `no real options in ${selector}`).toBeGreaterThan(0);
  await page.selectOption(selector, values[0]);
}

/**
 * Fill and submit the registration form, then confirm via the emailed link so
 * the participant ends VERIFIED and in the pool. This is the real user path —
 * no debug shortcut.
 *
 * NOTE: following the confirmation link logs the user in (register_confirm calls
 * django.contrib.auth.login), so on return the `page` holds an authenticated
 * session. Callers that want the anonymous / logged-out state must clear cookies
 * or use a fresh context.
 */
export async function registerVerified(
  page: Page,
  mailbox: Mailbox,
  p: Participant,
): Promise<void> {
  await page.goto(ROUTES.register(p.role));
  await page.fill(FIELD.firstName, p.firstName);
  await page.fill(FIELD.lastName, p.lastName);
  await page.fill(FIELD.email, p.email);
  await page.fill(FIELD.phone, p.phone);
  await selectFirstReal(page, FIELD.preferredLocation);
  // Prefer an explicit language/nationality where those option values are known
  // and stable; fall back to the first real option otherwise.
  await page.selectOption(FIELD.preferredLanguage, "en").catch(() =>
    selectFirstReal(page, FIELD.preferredLanguage),
  );
  await page.selectOption(FIELD.nationality, "CH").catch(() =>
    selectFirstReal(page, FIELD.nationality),
  );
  // Ambassadors additionally choose a prior-season pass; referees have no such
  // field (their prior_pass is always NONE).
  if (p.role === "ambassador") {
    await page.selectOption(FIELD.priorPass, "SEASONAL");
  }
  await page.check(FIELD.termsAccepted);
  await page.click('button[type="submit"]');

  await expect(page).toHaveURL(new RegExp(ROUTES.registerSent.replace(/\//g, "\\/")));

  const confirmUrl = await mailbox.waitForLink(p.email, LINK.confirm);
  await page.goto(confirmUrl);
  // Landing on register/done confirms the registration verified.
  await expect(page).toHaveURL(/\/register\/done\//);
}

/** Request a magic link for `email`, then follow + confirm it to log in. */
export async function loginViaMagicLink(
  page: Page,
  mailbox: Mailbox,
  email: string,
): Promise<void> {
  await page.goto(ROUTES.login);
  await page.fill(FIELD.email, email);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(
    new RegExp(ROUTES.loginSent.replace(/\//g, "\\/")),
  );

  const loginUrl = await mailbox.waitForLink(email, LINK.login);
  await page.goto(loginUrl);
  // The verify page does NOT log in on GET (prefetch-safe); confirm to log in.
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(
    new RegExp(ROUTES.account.replace(/\//g, "\\/")),
  );
}

/**
 * Register one ambassador and one referee (each in its own context) so the
 * second confirmation proposes a match between them. Returns both identities;
 * the match links wait in Mailpit for `openMatchFromEmail`.
 */
export async function makeProposedPair(
  browser: Browser,
  mailbox: Mailbox,
  runId: string,
): Promise<{ amb: Participant; ref: Participant }> {
  const amb = makeParticipant("ambassador", runId);
  const ref = makeParticipant("referee", runId);

  const ctxA = await browser.newContext();
  await registerVerified(await ctxA.newPage(), mailbox, amb);
  await ctxA.close();

  const ctxB = await browser.newContext();
  await registerVerified(await ctxB.newPage(), mailbox, ref);
  await ctxB.close();

  return { amb, ref };
}

/** Open the match link that was emailed to `email`. Returns the match URL. */
export async function openMatchFromEmail(
  page: Page,
  mailbox: Mailbox,
  email: string,
): Promise<string> {
  const url = await mailbox.waitForLink(email, LINK.match);
  await page.goto(url);
  return url;
}

/**
 * Click a match action. Decline and report-no-show pop a browser confirm()
 * (hx-confirm) — auto-accept it so the action proceeds.
 */
export async function matchAction(
  page: Page,
  action: keyof typeof ACTION,
): Promise<void> {
  if (action === "decline" || action === "reportNoShow") {
    page.once("dialog", (d) => d.accept());
  }
  await page.click(ACTION[action]);
}
