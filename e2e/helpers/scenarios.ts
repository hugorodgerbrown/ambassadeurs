/**
 * The canonical scenario catalogue — the numbered list from
 * docs/manual-tests/manual-test-script.md. Tests tag themselves with `@S<n>`
 * (see each spec) and the results-log reporter (reporters/results-log.ts) maps
 * those tags back to these rows to print the manual script's results log.
 *
 * Keep this list and the manual script in lockstep: same numbers, same titles.
 */
export interface Scenario {
  n: number;
  title: string;
  /** Marks the release-blocking privacy scenario for emphasis in the log. */
  blocker?: boolean;
}

export const SCENARIOS: Scenario[] = [
  { n: 1, title: "Smoke" },
  { n: 2, title: "i18n" },
  { n: 3, title: "Register (free)" },
  { n: 4, title: "Register (paid deposit)" },
  { n: 5, title: "Match happy path + reveal" },
  { n: 6, title: "Decline + rejoin" },
  { n: 7, title: "Withdraw" },
  { n: 8, title: "No-show report + forfeit" },
  { n: 9, title: "Expiry (cron)" },
  { n: 10, title: "Magic-link login/logout" },
  { n: 11, title: "Account self-service" },
  { n: 12, title: "Closed / not-open states" },
  { n: 13, title: "Privacy invariant sweep", blocker: true },
  { n: 14, title: "Admin oversight" },
  { n: 15, title: "Notifications strip" },
];

/** The `@S<n>` tag string for a scenario number. */
export function tag(n: number): string {
  return `@S${n}`;
}

/** Parse a scenario number out of a `@S<n>` tag, or null if it isn't one. */
export function scenarioFromTag(t: string): number | null {
  const m = /^@S(\d+)$/.exec(t);
  return m ? Number(m[1]) : null;
}
