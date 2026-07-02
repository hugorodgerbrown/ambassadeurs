/**
 * Scenarios 5 (match happy path + reveal), 6 (decline + rejoin), 7 (withdraw),
 * 8 (post-accept no-show), and the Scenario 13 privacy-invariant sweep — the
 * core of the product.
 *
 * Both parties are driven in separate browser contexts so their sessions never
 * bleed together, exactly as two real people would experience it. The match
 * links come from Mailpit; the signed token is the only auth the match views
 * need.
 */
import { test, expect } from "../fixtures";
import type { Page } from "@playwright/test";
import {
  ACTION,
  makeProposedPair,
  openMatchFromEmail,
  matchAction,
  loginViaMagicLink,
  ROUTES,
  type Participant,
} from "../helpers/app";
import { registrationStatus, latestMatchStatus } from "../helpers/db";

/** Assert the page shows the partner's first name but NOT their email/phone. */
async function assertContactHidden(page: Page, partner: Participant) {
  const body = page.locator("body");
  await expect(body).toContainText(partner.firstName);
  await expect(body).not.toContainText(partner.email);
  await expect(body).not.toContainText(partner.phone);
}

test.describe("matching", () => {
  test("mutual accept reveals contact details; nothing leaks before", { tag: ["@S5", "@S13"] }, async ({
    browser,
    mailbox,
    runId,
  }) => {
    const { amb, ref } = await makeProposedPair(browser, mailbox, runId);

    const ambCtx = await browser.newContext();
    const refCtx = await browser.newContext();
    const ambPage = await ambCtx.newPage();
    const refPage = await refCtx.newPage();

    // PROPOSED: each sees the other's first name only.
    await openMatchFromEmail(ambPage, mailbox, amb.email);
    await assertContactHidden(ambPage, ref);
    await openMatchFromEmail(refPage, mailbox, ref.email);
    await assertContactHidden(refPage, amb);

    // Ambassador accepts → PENDING. Still no contact details either side.
    await matchAction(ambPage, "accept");
    await assertContactHidden(ambPage, ref);
    await refPage.reload();
    await assertContactHidden(refPage, amb);

    // Referee accepts → ACCEPTED. Now, and only now, contact details reveal.
    await matchAction(refPage, "accept");
    await expect(refPage.locator("body")).toContainText(amb.email);
    await expect(refPage.locator("body")).toContainText(amb.phone);

    await ambPage.reload();
    await expect(ambPage.locator("body")).toContainText(ref.email);
    await expect(ambPage.locator("body")).toContainText(ref.phone);

    await ambCtx.close();
    await refCtx.close();
  });

  test("decline pauses the decliner and never reveals contact details", { tag: ["@S6", "@S13"] }, async ({
    browser,
    mailbox,
    runId,
  }) => {
    const { amb, ref } = await makeProposedPair(browser, mailbox, runId);

    const refCtx = await browser.newContext();
    const refPage = await refCtx.newPage();
    await openMatchFromEmail(refPage, mailbox, ref.email);
    await assertContactHidden(refPage, amb);

    await matchAction(refPage, "decline");
    // Post-decline the page must still not reveal the ambassador's contact PII.
    await expect(refPage.locator("body")).not.toContainText(amb.email);
    await expect(refPage.locator("body")).not.toContainText(amb.phone);

    // The paused referee can rejoin the queue from their account.
    await loginViaMagicLink(refPage, mailbox, ref.email);
    await refPage.goto(ROUTES.account);
    // The rejoin control is present while PAUSED; exercise it.
    const rejoin = refPage.locator(`a[href$="${ROUTES.accountRejoin}"], form[action$="${ROUTES.accountRejoin}"] button`);
    await expect(rejoin.first()).toBeVisible();
    await rejoin.first().click();

    await refCtx.close();
  });

  test("withdraw un-accepts and returns to the proposed state", { tag: ["@S7", "@S13"] }, async ({
    browser,
    mailbox,
    runId,
  }) => {
    const { amb, ref } = await makeProposedPair(browser, mailbox, runId);

    const ambCtx = await browser.newContext();
    const ambPage = await ambCtx.newPage();
    await openMatchFromEmail(ambPage, mailbox, amb.email);

    // Accept → PENDING (you_accepted). A withdraw control appears on the token
    // route while the partner has not yet accepted.
    await matchAction(ambPage, "accept");
    await expect(ambPage.locator(ACTION.withdraw)).toBeVisible();
    await assertContactHidden(ambPage, ref);

    // Withdraw → back to PROPOSED, so the accept form is offered again.
    await matchAction(ambPage, "withdraw");
    await expect(ambPage.locator(ACTION.accept)).toBeVisible();
    await assertContactHidden(ambPage, ref);
    expect(await latestMatchStatus()).toBe("PROPOSED");

    await ambCtx.close();
  });

  test("post-accept no-show suspends the reported party", { tag: "@S8" }, async ({
    browser,
    mailbox,
    runId,
  }) => {
    const { amb, ref } = await makeProposedPair(browser, mailbox, runId);

    const ambCtx = await browser.newContext();
    const refCtx = await browser.newContext();
    const ambPage = await ambCtx.newPage();
    const refPage = await refCtx.newPage();

    // Both accept → ACCEPTED.
    await openMatchFromEmail(ambPage, mailbox, amb.email);
    await openMatchFromEmail(refPage, mailbox, ref.email);
    await matchAction(ambPage, "accept");
    await refPage.reload();
    await matchAction(refPage, "accept");
    // Wait for the reveal (proof the accept landed and the match is ACCEPTED)
    // before reading the DB, so we don't race the HTMX post.
    await expect(refPage.locator("body")).toContainText(amb.email);
    expect(await latestMatchStatus()).toBe("ACCEPTED");

    // Ambassador reports the referee as a post-accept no-show.
    await ambPage.reload();
    await matchAction(ambPage, "reportNoShow");
    // The action forms are gone once the match is cancelled; wait for that swap.
    await expect(ambPage.locator(ACTION.reportNoShow)).toHaveCount(0);

    // Trusted immediately: match CANCELLED, reported referee SUSPENDED.
    expect(await latestMatchStatus()).toBe("CANCELLED");
    expect(await registrationStatus(ref.email)).toBe("SUSPENDED");

    await ambCtx.close();
    await refCtx.close();
  });
});
