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
  // Since SKI-153 the URL carries the language: /faq/ is English and /fr/faq/
  // is French, whatever the cookie or Accept-Language says. Switching language
  // is therefore navigation, not a POST, and "persists across pages" means the
  // in-page links keep the /fr/ prefix rather than a cookie following you.
  test("the footer link switches language, and navigation stays in it", async ({
    page,
  }) => {
    await page.goto(ROUTES.home);
    expect(await page.getAttribute("html", "lang")).toMatch(/^en/);

    await page.getByRole("link", { name: "FR" }).click();

    await expect(page).toHaveURL(/\/fr\//);
    expect(await page.getAttribute("html", "lang")).toMatch(/^fr/);

    // Following an ordinary in-page link keeps the language, because {% url %}
    // reverses under the active one.
    await page.getByRole("link", { name: /How it works|Comment/ }).first().click();
    await expect(page).toHaveURL(/\/fr\//);
    expect(await page.getAttribute("html", "lang")).toMatch(/^fr/);
  });

  test("an unprefixed URL is English regardless of what came before", async ({
    page,
  }) => {
    await page.goto("/fr/");
    expect(await page.getAttribute("html", "lang")).toMatch(/^fr/);

    // The URL is authoritative: no cookie or history carries French over.
    await page.goto(ROUTES.howItWorks);
    expect(await page.getAttribute("html", "lang")).toMatch(/^en/);
  });
});
