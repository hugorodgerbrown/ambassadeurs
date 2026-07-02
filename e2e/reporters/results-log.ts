/**
 * Custom Playwright reporter that prints the manual-test-script results log.
 *
 * It maps every test's `@S<n>` tags (see helpers/scenarios.ts) onto the numbered
 * scenarios from docs/manual-tests/manual-test-script.md and, at the end of the
 * run, prints that document's "Results log" block filled in from the actual run:
 * PASS / FAIL / n/a per scenario, plus notes (skip reasons, failure counts).
 *
 * A scenario is PASS if at least one tagged test ran and none failed; FAIL if any
 * tagged test failed; n/a if every tagged test was skipped; and "not covered" if
 * no test carries its tag.
 */
import type {
  FullResult,
  Reporter,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";
import { SCENARIOS, scenarioFromTag } from "../helpers/scenarios";

type Status = "pass" | "fail" | "na" | "none";

interface Tally {
  passed: number;
  failed: number;
  skipped: number;
  notes: Set<string>;
}

export default class ResultsLogReporter implements Reporter {
  private readonly tally = new Map<number, Tally>();

  private bucket(n: number): Tally {
    let t = this.tally.get(n);
    if (!t) {
      t = { passed: 0, failed: 0, skipped: 0, notes: new Set() };
      this.tally.set(n, t);
    }
    return t;
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    const scenarios = test.tags
      .map(scenarioFromTag)
      .filter((n): n is number => n !== null);
    if (scenarios.length === 0) return;

    for (const n of scenarios) {
      const t = this.bucket(n);
      if (result.status === "passed") {
        t.passed += 1;
      } else if (result.status === "skipped") {
        t.skipped += 1;
        const reason = test.annotations.find(
          (a) => a.type === "skip" || a.type === "fixme",
        )?.description;
        if (reason) t.notes.add(reason);
      } else {
        t.failed += 1;
        const msg = result.error?.message?.split("\n")[0];
        if (msg) t.notes.add(msg.slice(0, 80));
      }
    }
  }

  private status(n: number): Status {
    const t = this.tally.get(n);
    if (!t) return "none";
    if (t.failed > 0) return "fail";
    if (t.passed > 0) return "pass";
    if (t.skipped > 0) return "na";
    return "none";
  }

  onEnd(_result: FullResult): void {
    const date = new Date().toISOString().slice(0, 10);
    const baseUrl = process.env.BASE_URL ?? "http://127.0.0.1:8000";
    const env = process.env.E2E_ENV ?? "local (config.settings.e2e)";
    const fee = process.env.REGISTRATION_FEE_TIERS ? "paid" : "free";

    const label: Record<Status, string> = {
      pass: "PASS",
      fail: "FAIL",
      na: "n/a",
      none: "— not covered",
    };

    const lines: string[] = [];
    const rule = "=".repeat(72);
    lines.push("");
    lines.push(rule);
    lines.push("  Ambassadeurs — manual-test scenarios (automated Playwright run)");
    lines.push("  Ref: docs/manual-tests/manual-test-script.md");
    lines.push(rule);
    lines.push(`Date:              ${date}`);
    lines.push(`Environment:       ${env}`);
    lines.push(`Base URL:          ${baseUrl}`);
    lines.push(`Runner:            Playwright`);
    lines.push(`Registration fee:  ${fee}`);
    lines.push("");
    lines.push("Scenario                              Result   Notes");

    let pass = 0;
    let fail = 0;
    let na = 0;
    for (const s of SCENARIOS) {
      const st = this.status(s.n);
      if (st === "pass") pass += 1;
      else if (st === "fail") fail += 1;
      else if (st === "na") na += 1;

      const num = String(s.n).padStart(2, " ");
      const title = (s.title + (s.blocker ? " (release blocker)" : "")).padEnd(
        33,
        " ",
      );
      const note = [...(this.tally.get(s.n)?.notes ?? [])].join("; ");
      lines.push(`${num}  ${title} ${label[st].padEnd(8)} ${note}`.trimEnd());
    }

    lines.push("-".repeat(72));
    lines.push(
      `Totals: ${pass} passed, ${fail} failed, ${na} n/a  ` +
        `(${SCENARIOS.length} scenarios)`,
    );
    const blocker = this.status(13);
    if (blocker === "fail") {
      lines.push("");
      lines.push("  ** RELEASE BLOCKER: privacy-invariant scenario FAILED **");
    }
    lines.push(rule);
    lines.push("");

    // Write straight to stdout so it prints regardless of other reporters.
    // Playwright already sets the process exit code from `result.status`.
    process.stdout.write(lines.join("\n"));
  }
}
