/**
 * Scenario 9: contact-window expiry.
 *
 * Rather than wait out the real 72-hour window, the test backdates the match's
 * expires_at into the past (helpers/db.ts) and then runs the ACTUAL cron command
 * — `manage.py expire_matches` (helpers/manage.ts) — so it exercises the exact
 * code path the Render cron service runs hourly.
 */
import { test, expect } from "../fixtures";
import { makeProposedPair, openMatchFromEmail, ACTION } from "../helpers/app";
import { expireContactWindowNow, latestMatchStatus } from "../helpers/db";
import { runManage } from "../helpers/manage";

test.describe("lifecycle", () => {
  test("expire_matches lapses an unanswered match", { tag: "@S9" }, async ({
    browser,
    mailbox,
    runId,
  }) => {
    const { amb } = await makeProposedPair(browser, mailbox, runId);
    expect(await latestMatchStatus()).toBe("PROPOSED");

    // Manually expire: move the window into the past, then run the cron command.
    await expireContactWindowNow();
    const out = await runManage(["expire_matches"]);
    expect(out).toMatch(/Expired \d+ match/);

    expect(await latestMatchStatus()).toBe("EXPIRED");

    // The ambassador's match page no longer offers the accept action.
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await openMatchFromEmail(page, mailbox, amb.email);
    await expect(page.locator(ACTION.accept)).toHaveCount(0);
    await ctx.close();
  });
});
