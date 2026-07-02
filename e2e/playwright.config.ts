/**
 * Playwright configuration for the Ambassadeurs e2e suite.
 *
 * The suite runs against a *running* app instance (never production). Two URLs
 * are read from the environment:
 *   BASE_URL    — the app under test        (default http://127.0.0.1:8000)
 *   MAILPIT_URL — the Mailpit HTTP API base (default http://127.0.0.1:8025)
 *
 * Locally, the `webServer` block boots Django for you (with the e2e settings
 * module) and reuses an already-running server if one is up. In CI we start the
 * server as an explicit job step instead and set PW_NO_WEBSERVER=1 so this block
 * is skipped — the server there must come up *after* the Postgres and Mailpit
 * service containers are ready, which the workflow sequences by hand.
 */
import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./tests",
  // Registration + matching flows mutate shared server state (the queue), so a
  // spec must own the accounts it creates. Unique-per-run emails keep specs
  // independent; we still cap workers to keep the queue reasoning simple.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // The results-log reporter prints the manual-test-script results table at the
  // end of every run (reporters/results-log.ts).
  reporter: process.env.CI
    ? [
        ["github"],
        ["html", { open: "never" }],
        ["list"],
        ["./reporters/results-log.ts"],
      ]
    : [
        ["html", { open: "never" }],
        ["list"],
        ["./reporters/results-log.ts"],
      ],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PW_NO_WEBSERVER
    ? undefined
    : {
        // Boot Django with the e2e settings module. `--insecure` makes runserver
        // serve static files even with DEBUG=False. Run from the repo root.
        command:
          "uv run python manage.py runserver 127.0.0.1:8000 --insecure --noreload",
        cwd: "..",
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
        env: {
          DJANGO_SETTINGS_MODULE: "config.settings.e2e",
        },
      },
});
