/**
 * Scenario 15: the site-wide notifications strip (VERB-109).
 *
 * The full journey, exercising the real code end to end: program staff author a
 * notification in Django admin (so save() runs nh3 sanitisation), and every
 * assertion is made from a *separate* visitor context against the rendered
 * public page.
 *
 * Covered here (the user-visible surface):
 *   - staff author a notification in admin → it renders in the strip (AC1, AC6);
 *   - staff HTML is sanitised: scripts stripped, links survive and gain
 *     rel="noopener noreferrer" (AC3);
 *   - a dismissible notification dismisses for the browser session and reappears
 *     in a fresh session (AC4);
 *   - a permanent notification shows no dismiss control and survives a reload
 *     (AC5);
 *   - audience gating — EVERYONE / ANONYMOUS / AUTHENTICATED / CUSTOM — and that
 *     several active notifications stack together (AC7, AC8).
 *
 * NOT covered here: the starts_at/ends_at display window (AC2). That boundary
 * logic lives in NotificationQuerySet.active()/is_active and is exhaustively
 * covered by the Python unit suite (tests/core/test_models.py); every
 * notification created below is left always-on so the window widget never
 * enters the picture.
 *
 * Selectors mirror templates/includes/notification_strip.html:
 *   #notification-strip                     — the strip container (absent when
 *                                             the visitor has no active notice)
 *   [data-notification-id]                  — one banner, keyed on the pk
 *   .notification-banner__content           — the sanitised message body
 *   [data-dismiss-notification]             — the dismiss button (dismissible
 *                                             notifications only)
 * The dismissal script stores dismissed pks in sessionStorage under
 * "dismissedNotifications".
 */
import { type Page, type Browser } from "@playwright/test";
import { test, expect } from "../fixtures";
import { ROUTES, makeParticipant, registerVerified } from "../helpers/app";
import { runManage } from "../helpers/manage";

const STRIP = "#notification-strip";
const BANNER_CONTENT = ".notification-banner__content";
const ADMIN_PASSWORD = "e2e-admin-pass-1234"; // throwaway, ephemeral DB only

type Audience = "EVERYONE" | "ANONYMOUS" | "AUTHENTICATED" | "CUSTOM";
// Notification.Priority integer values (0 NEUTRAL … 3 HIGH).
type Priority = "0" | "1" | "2" | "3";

interface NotificationSpec {
  content: string;
  audience?: Audience;
  dismissible?: boolean;
  customGroupKey?: string;
  priority?: Priority;
  enabled?: boolean;
}

/**
 * Create a superuser (the per-test reset truncates auth_user) via the same
 * management command the project ships, then sign in through the real admin
 * login form. Returns an admin-authenticated page in its own context.
 */
async function openAdmin(browser: Browser, runId: string): Promise<Page> {
  const username = `e2eadmin-${runId}`;
  await runManage(["createsuperuser", "--noinput"], {
    DJANGO_SUPERUSER_USERNAME: username,
    DJANGO_SUPERUSER_EMAIL: `${username}@example.test`,
    DJANGO_SUPERUSER_PASSWORD: ADMIN_PASSWORD,
  });

  const page = await (await browser.newContext()).newPage();
  await page.goto("/admin/login/");
  await page.fill("#id_username", username);
  await page.fill("#id_password", ADMIN_PASSWORD);
  await page.click('input[type="submit"]');
  await expect(page).toHaveURL(/\/admin\/$/);
  return page;
}

/**
 * Author one notification through the Django admin add form — the real staff
 * path (AC1). save() sanitises the content, so what the visitor later sees is
 * the genuinely sanitised value, not a hand-written stand-in.
 */
async function addNotification(page: Page, spec: NotificationSpec): Promise<void> {
  const audience = spec.audience ?? "EVERYONE";
  const dismissible = spec.dismissible ?? true;

  await page.goto("/admin/core/notification/add/");
  await page.fill("#id_content", spec.content);
  await page.selectOption("#id_audience", audience);
  if (audience === "CUSTOM") {
    await page.selectOption("#id_custom_group_key", spec.customGroupKey ?? "");
  }
  if (spec.priority !== undefined) {
    await page.selectOption("#id_priority", spec.priority);
  }
  // is_dismissible defaults checked; uncheck to author a permanent notice.
  if (dismissible) {
    await page.check("#id_is_dismissible");
  } else {
    await page.uncheck("#id_is_dismissible");
  }
  // enabled (kill switch) defaults checked; uncheck to author a hidden notice.
  if (spec.enabled === false) {
    await page.uncheck("#id_enabled");
  } else {
    await page.check("#id_enabled");
  }
  await page.click('input[name="_save"]');
  // A successful save redirects to the changelist; a validation error would
  // re-render the add form instead, failing this assertion.
  await expect(page).toHaveURL(/\/admin\/core\/notification\/$/);
}

/** Open a fresh, cookie-less visitor context on a page and return it. */
async function openVisitor(browser: Browser, path: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto(path);
  return page;
}

test.describe("notifications strip", { tag: "@S15" }, () => {
  test("staff-authored notification renders and its HTML is sanitised", async ({
    browser,
    runId,
  }) => {
    const admin = await openAdmin(browser, runId);
    // A link that must survive, and a script that must not.
    const marker = `sanitised-${runId}`;
    await addNotification(admin, {
      content:
        `${marker} <a href="https://example.com/read" target="_blank">read more</a>` +
        `<script>window.__xssRan = true;</script>`,
    });
    await admin.context().close();

    const visitor = await openVisitor(browser, ROUTES.home);

    await expect(visitor.locator(STRIP)).toBeVisible();
    await expect(visitor.getByText(marker)).toBeVisible();

    // The anchor survives with its href/target, and nh3 has force-added the
    // tabnabbing guard even though the author never wrote a rel attribute.
    const link = visitor.locator(`${BANNER_CONTENT} a`);
    await expect(link).toHaveText("read more");
    await expect(link).toHaveAttribute("href", "https://example.com/read");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", /noopener/);

    // The <script> was stripped, not just inert: it is absent from the DOM and
    // never executed.
    await expect(
      visitor.locator(`${BANNER_CONTENT} script`),
    ).toHaveCount(0);
    const xssRan = await visitor.evaluate(
      () => (window as unknown as { __xssRan?: boolean }).__xssRan,
    );
    expect(xssRan).toBeFalsy();

    await visitor.context().close();
  });

  test("a dismissible notification dismisses for the session and reappears in a fresh one", async ({
    browser,
    runId,
  }) => {
    const admin = await openAdmin(browser, runId);
    const marker = `dismiss-me-${runId}`;
    await addNotification(admin, { content: marker, dismissible: true });
    await admin.context().close();

    const visitor = await openVisitor(browser, ROUTES.home);
    const banner = visitor.locator("[data-notification-id]").filter({
      hasText: marker,
    });
    await expect(banner).toBeVisible();

    // Dismiss it, and it disappears without a server round-trip.
    await banner.locator("[data-dismiss-notification]").click();
    await expect(banner).toBeHidden();

    // A reload in the same session keeps it hidden (sessionStorage remembers).
    await visitor.reload();
    await expect(
      visitor.locator("[data-notification-id]").filter({ hasText: marker }),
    ).toBeHidden();
    const stored = await visitor.evaluate(() =>
      window.sessionStorage.getItem("dismissedNotifications"),
    );
    expect(stored).toBeTruthy();
    await visitor.context().close();

    // A brand-new context is a brand-new session: the notice is back.
    const fresh = await openVisitor(browser, ROUTES.home);
    await expect(
      fresh.locator("[data-notification-id]").filter({ hasText: marker }),
    ).toBeVisible();
    await fresh.context().close();
  });

  test("a permanent notification has no dismiss control and survives a reload", async ({
    browser,
    runId,
  }) => {
    const admin = await openAdmin(browser, runId);
    const marker = `permanent-${runId}`;
    await addNotification(admin, { content: marker, dismissible: false });
    await admin.context().close();

    const visitor = await openVisitor(browser, ROUTES.home);
    const banner = visitor.locator("[data-notification-id]").filter({
      hasText: marker,
    });
    await expect(banner).toBeVisible();
    await expect(banner.locator("[data-dismiss-notification]")).toHaveCount(0);

    await visitor.reload();
    await expect(
      visitor.locator("[data-notification-id]").filter({ hasText: marker }),
    ).toBeVisible();
    await visitor.context().close();
  });

  test("priority sets the banner colour tone and the kill switch hides a notice", async ({
    browser,
    runId,
  }) => {
    const shown = `high-priority-${runId}`;
    const hidden = `killed-${runId}`;

    const admin = await openAdmin(browser, runId);
    // A HIGH-priority notice (Priority.HIGH == 3) and a disabled one.
    await addNotification(admin, { content: shown, priority: "3" });
    await addNotification(admin, { content: hidden, enabled: false });
    await admin.context().close();

    const visitor = await openVisitor(browser, ROUTES.home);

    // The HIGH notice renders and carries the "high" colour tone.
    const banner = visitor.locator("[data-notification-id]").filter({
      hasText: shown,
    });
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("data-priority", "high");

    // The disabled notice never reaches the page (kill switch, window aside).
    await expect(visitor.getByText(hidden)).toHaveCount(0);
    await visitor.context().close();
  });

  test("audience gating shows each notification only to its intended visitors", async ({
    browser,
    mailbox,
    runId,
  }) => {
    const everyone = `everyone-${runId}`;
    const anonOnly = `anon-only-${runId}`;
    const authOnly = `auth-only-${runId}`;
    const ambaOnly = `amba-only-${runId}`;

    const admin = await openAdmin(browser, runId);
    await addNotification(admin, { content: everyone, audience: "EVERYONE" });
    await addNotification(admin, { content: anonOnly, audience: "ANONYMOUS" });
    await addNotification(admin, { content: authOnly, audience: "AUTHENTICATED" });
    await addNotification(admin, {
      content: ambaOnly,
      audience: "CUSTOM",
      customGroupKey: "ambassadors",
    });
    await admin.context().close();

    // Anonymous visitor: EVERYONE + ANONYMOUS, and both stack in one strip.
    const anon = await openVisitor(browser, ROUTES.home);
    await expect(anon.getByText(everyone)).toBeVisible();
    await expect(anon.getByText(anonOnly)).toBeVisible();
    await expect(anon.getByText(authOnly)).toHaveCount(0);
    await expect(anon.getByText(ambaOnly)).toHaveCount(0);
    await anon.context().close();

    // Authenticated ambassador: EVERYONE + AUTHENTICATED + the ambassadors group.
    const ambCtx = await browser.newContext();
    const ambPage = await ambCtx.newPage();
    await registerVerified(ambPage, mailbox, makeParticipant("ambassador", runId));
    await ambPage.goto(ROUTES.home);
    await expect(ambPage.getByText(everyone)).toBeVisible();
    await expect(ambPage.getByText(authOnly)).toBeVisible();
    await expect(ambPage.getByText(ambaOnly)).toBeVisible();
    await expect(ambPage.getByText(anonOnly)).toHaveCount(0);
    await ambCtx.close();

    // Authenticated referee: EVERYONE + AUTHENTICATED, but NOT the ambassadors
    // group (proves CUSTOM excludes non-members, not just anonymous visitors).
    const refCtx = await browser.newContext();
    const refPage = await refCtx.newPage();
    await registerVerified(refPage, mailbox, makeParticipant("referee", runId));
    await refPage.goto(ROUTES.home);
    await expect(refPage.getByText(everyone)).toBeVisible();
    await expect(refPage.getByText(authOnly)).toBeVisible();
    await expect(refPage.getByText(ambaOnly)).toHaveCount(0);
    await expect(refPage.getByText(anonOnly)).toHaveCount(0);
    await refCtx.close();
  });
});
