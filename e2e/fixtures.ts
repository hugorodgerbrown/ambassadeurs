/**
 * Shared test fixtures.
 *
 * `mailbox` — a Mailpit client, created per test and disposed automatically.
 * `runId`   — a short unique token, mixed into every generated email address so
 *             parallel or retried tests never read each other's mail.
 */
import { test as base } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { Mailbox } from "./helpers/mail";
import { resetDatabase } from "./helpers/db";

export const test = base.extend<{
  mailbox: Mailbox;
  runId: string;
  resetDb: void;
}>({
  // Empty the shared registration pool before every test so the matching engine
  // never cross-matches leftovers from another test. Auto-used — no spec opts in.
  resetDb: [
    // eslint-disable-next-line no-empty-pattern
    async ({}, use) => {
      await resetDatabase();
      await use(undefined);
    },
    { auto: true },
  ],
  // eslint-disable-next-line no-empty-pattern
  runId: async ({}, use) => {
    await use(randomUUID().slice(0, 8));
  },
  mailbox: async ({}, use) => {
    const mailbox = await Mailbox.create();
    await use(mailbox);
    await mailbox.dispose();
  },
});

export { expect } from "@playwright/test";
