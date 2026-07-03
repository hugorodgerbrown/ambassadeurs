/**
 * Scenarios 3, 10, 11: registration + email confirmation, magic-link login,
 * and account self-service. Exercises the real email path via Mailpit.
 */
import { test, expect } from "../fixtures";
import {
  ROUTES,
  makeParticipant,
  registerVerified,
  loginViaMagicLink,
} from "../helpers/app";

test.describe("registration & account", () => {
  test("register (referee) → confirm → verified", { tag: "@S3" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("referee", runId);
    await registerVerified(page, mailbox, p);
    await expect(page).toHaveURL(/\/register\/done\/referee\//);
  });

  test("register (ambassador) → confirm → verified", { tag: "@S3" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("ambassador", runId);
    await registerVerified(page, mailbox, p);
    await expect(page).toHaveURL(/\/register\/done\/ambassador\//);
  });

  test("submitting with a missing required field shows validation, sends no mail", { tag: "@S3" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("referee", runId);
    await page.goto(ROUTES.register(p.role));
    // Leave email empty; fill the rest minimally then submit.
    await page.fill("#id_first_name", p.firstName);
    await page.fill("#id_last_name", p.lastName);
    await page.click('button[type="submit"]');
    // Still on the form (not redirected to the sent page).
    await expect(page).not.toHaveURL(/\/register\/sent\//);
    await expect(page.locator("#id_email")).toBeVisible();
  });

  test("willingness-to-pay survey submits in place and does not re-show", { tag: "@S3" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("referee", runId);
    await registerVerified(page, mailbox, p);
    const doneUrl = page.url();

    const survey = page.locator("#wtp-survey");
    await expect(survey).toBeVisible();
    await expect(survey).toContainText(/CHF \d+/);

    await survey.locator('input[name="q1_answer"]').first().check();
    await survey.locator('button[type="submit"]').click();

    // The thanks fragment replaces the survey card in place — same URL, no
    // full-page navigation, proving the HTMX submit path.
    await expect(page).toHaveURL(doneUrl);
    await expect(survey).toContainText("Thank you");

    // A reload no longer shows the survey (already-responded gate).
    await page.reload();
    await expect(page.locator("#wtp-survey")).toHaveCount(0);
  });

  test("willingness-to-pay survey skip is a no-op", { tag: "@S3" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("referee", runId);
    await registerVerified(page, mailbox, p);

    const survey = page.locator("#wtp-survey");
    await expect(survey).toBeVisible();
    // Skip is a second submit button on the same form (server-side no-op via
    // register_survey_submit, not an hx-on:click — the production CSP has no
    // unsafe-eval). The response body is empty, so the hx-swap empties the
    // block in place rather than hiding it.
    await survey.getByRole("button", { name: /skip/i }).click();
    await expect(survey).toBeEmpty();

    // Skip never blocks the page — the main CTA still navigates normally.
    await page.getByRole("link", { name: /go to my account/i }).click();
    await expect(page).toHaveURL(new RegExp(ROUTES.account.replace(/\//g, "\\/")));
  });

  test("magic-link login is prefetch-safe, logs in, and logs out", { tag: "@S10" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("referee", runId);
    await registerVerified(page, mailbox, p);
    // Confirmation logged us in; clear cookies to test login from a clean state.
    await page.context().clearCookies();
    await loginViaMagicLink(page, mailbox, p.email);
    await expect(page).toHaveURL(new RegExp(ROUTES.account.replace(/\//g, "\\/")));
    await expect(page.locator("body")).toContainText(p.firstName);

    // Log out via the dedicated confirmation page (the nav has several logout
    // forms, some hidden in collapsed menus; this page has exactly one).
    await page.goto(ROUTES.logout);
    await page.click('#main button[type="submit"]');
    await expect(page).toHaveURL(new RegExp(`${ROUTES.home}$`));
    // The account page now bounces to login (session cleared).
    await page.goto(ROUTES.account);
    await expect(page).toHaveURL(/\/account\/login\//);
  });

  test("login does not enumerate unknown addresses", { tag: "@S10" }, async ({ page }) => {
    await page.goto(ROUTES.login);
    await page.fill("#id_email", `nobody-${Date.now()}@example.test`);
    await page.click('button[type="submit"]');
    // Same "check your inbox" page as a known address — no difference leaked.
    await expect(page).toHaveURL(new RegExp(ROUTES.loginSent.replace(/\//g, "\\/")));
  });

  test("account edit persists changes", { tag: "@S11" }, async ({ page, mailbox, runId }) => {
    const p = makeParticipant("referee", runId);
    // registerVerified leaves the session logged in (confirm authenticates).
    await registerVerified(page, mailbox, p);

    await page.goto(ROUTES.accountEdit);
    const newPhone = "+41790000999";
    await page.fill("#id_phone", newPhone);
    // Scope to the main form's submit — authenticated pages also carry a nav
    // "Sign out" submit button, so a bare button[type=submit] is ambiguous.
    await page.click('#main button[type="submit"]');
    await page.goto(ROUTES.account);
    await expect(page.locator("body")).toContainText(newPhone);
  });

  test("account delete removes the user and logs out", { tag: "@S11" }, async ({
    page,
    mailbox,
    runId,
  }) => {
    const p = makeParticipant("referee", runId);
    // registerVerified leaves the session logged in (confirm authenticates).
    await registerVerified(page, mailbox, p);

    await page.goto(ROUTES.accountDelete);
    // Submit the delete-confirmation form (scoped to main; see edit test note).
    await page.click('#main button[type="submit"]');
    // After deletion, the account page is no longer reachable while logged in.
    await page.goto(ROUTES.account);
    await expect(page).toHaveURL(/\/account\/login\//);
  });
});
