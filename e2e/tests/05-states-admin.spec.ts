/**
 * Scenarios 4 (paid deposit), 12 (closed / not-open states), and 14 (admin).
 *
 * S4 and S12 depend on the server being booted with a different configuration
 * than the default open/free e2e instance, so they are env-gated: they run only
 * when the corresponding server has been started, and otherwise report n/a with
 * a reason (which the results-log reporter prints). S14 runs unconditionally.
 */
import { test, expect } from "../fixtures";
import { ROUTES, makeParticipant } from "../helpers/app";
import { runManage } from "../helpers/manage";

test.describe("states & admin", () => {
  test("paid deposit diverts registration to Stripe Checkout", { tag: "@S4" }, async ({
    page,
    runId,
  }) => {
    test.skip(
      !process.env.E2E_RUN_PAID,
      "needs a server booted with REGISTRATION_FEE_TIERS>0 and Stripe test keys; " +
        "run with E2E_RUN_PAID=1 against a paid-tier instance",
    );
    const p = makeParticipant("referee", runId);
    await page.goto(ROUTES.register(p.role));
    await page.fill("#id_first_name", p.firstName);
    await page.fill("#id_last_name", p.lastName);
    await page.fill("#id_email", p.email);
    await page.fill("#id_phone", p.phone);
    await page.check("#id_prior_pass_attestation");
    await page.check("#id_terms_accepted");
    await page.click('button[type="submit"]');
    // Paid tier confirms via email then diverts to the deposit funnel.
    await expect(page).toHaveURL(/\/register\/pay\//);
  });

  test("registration-closed page is shown when the window is shut", { tag: "@S12" }, async ({
    page,
  }) => {
    test.skip(
      !process.env.E2E_EXPECT_CLOSED,
      "needs a server booted with REGISTRATION_CLOSES_AT in the past; " +
        "run with E2E_EXPECT_CLOSED=1 against a closed instance",
    );
    await page.goto(ROUTES.register("referee"));
    // The closed template replaces the form; the email field must be absent.
    await expect(page.locator("#id_email")).toHaveCount(0);
  });

  test("admin can review registrations, matches, and payments", { tag: "@S14" }, async ({
    page,
    runId,
  }) => {
    // The per-test reset truncates auth_user, so create the superuser here, then
    // sign in through the real admin login form.
    const username = `e2eadmin-${runId}`;
    const password = "e2e-admin-pass-1234"; // throwaway, ephemeral DB only
    await runManage(["createsuperuser", "--noinput"], {
      DJANGO_SUPERUSER_USERNAME: username,
      DJANGO_SUPERUSER_EMAIL: `${username}@example.test`,
      DJANGO_SUPERUSER_PASSWORD: password,
    });

    await page.goto("/admin/login/");
    await page.fill("#id_username", username);
    await page.fill("#id_password", password);
    await page.click('input[type="submit"]');
    await expect(page).toHaveURL(/\/admin\/$/);

    for (const path of [
      "/admin/matching/registration/",
      "/admin/matching/match/",
      "/admin/billing/payment/",
    ]) {
      const res = await page.goto(path);
      expect(res?.status(), `${path} status`).toBeLessThan(400);
      // Django renders the changelist inside #content.
      await expect(page.locator("#content")).toBeVisible();
    }
  });
});
