/**
 * Scenario 1 (smoke) + Scenario 2 (i18n) from the manual test script.
 *
 * Unauthenticated, no email needed — the fast "is the deploy alive and styled"
 * lane.
 */
import { test, expect } from "../fixtures";
import { ROUTES } from "../helpers/app";

test.describe("smoke", { tag: "@S1" }, () => {
  test("liveness probe responds 200", async ({ request, baseURL }) => {
    const res = await request.get(`${baseURL}${ROUTES.healthz}`);
    expect(res.status()).toBe(200);
  });

  test("public pages render", async ({ page }) => {
    for (const path of [
      ROUTES.home,
      ROUTES.howItWorks,
      ROUTES.faq,
      "/legal/privacy/",
      "/legal/terms/",
    ]) {
      const res = await page.goto(path);
      expect(res?.status(), `${path} status`).toBeLessThan(400);
      await expect(page.locator("body")).toBeVisible();
    }
  });

  test("styling is loaded (Tailwind output.css served)", async ({ page }) => {
    await page.goto(ROUTES.home);
    // A styled page paints a non-transparent background on <body>. If output.css
    // failed to build/serve this is the default rgba(0,0,0,0).
    const bg = await page
      .locator("body")
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bg).not.toBe("rgba(0, 0, 0, 0)");
  });

  test("robots and sitemap are served", async ({ request, baseURL }) => {
    expect((await request.get(`${baseURL}${ROUTES.robots}`)).status()).toBe(200);
    const sitemap = await request.get(`${baseURL}${ROUTES.sitemap}`);
    expect(sitemap.status()).toBe(200);
    expect(await sitemap.text()).toContain("<urlset");
  });

  test("debug panel is not publicly usable in production-shaped settings", async ({
    request,
    baseURL,
  }) => {
    // DEBUG is off in the e2e settings, so the /debug/ test-data routes must be
    // unreachable. An anonymous, CSRF-less POST is blocked (403/404/405) — the
    // exact `require_debug` 404 for a well-formed request is covered by the
    // pytest suite (debug/views.py docstring). Here we assert it is not usable.
    const res = await request.post(`${baseURL}/debug/create-counterpart/`);
    expect(res.status()).toBeGreaterThanOrEqual(400);
  });
});

test.describe("i18n", { tag: "@S2" }, () => {
  test("language switch to French persists across pages", async ({ page }) => {
    await page.goto(ROUTES.home);
    // The language form posts to Django's set_language endpoint. Switch to fr
    // via the API the switcher uses, then assert the cookie + a reload.
    await page.evaluate(async () => {
      const body = new URLSearchParams({ language: "fr", next: "/" });
      await fetch("/i18n/setlang/", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken":
            document.cookie.match(/csrftoken=([^;]+)/)?.[1] ?? "",
        },
        body,
      });
    });
    await page.goto(ROUTES.howItWorks);
    const lang = await page.getAttribute("html", "lang");
    expect(lang).toMatch(/^fr/);
  });
});
