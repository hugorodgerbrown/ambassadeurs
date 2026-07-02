/**
 * Run a Django management command against the same instance the browser drives.
 *
 * A few scenarios need a server-side action a user cannot take from a page — the
 * expiry sweep (the `expire_matches` cron) and creating an admin user. Rather
 * than wait out a 72-hour window or seed the DB by hand, the test shells out to
 * the real command, so it exercises the exact code the cron/admin path runs.
 *
 * Inherits the process environment (DJANGO_SETTINGS_MODULE, DATABASE_URL, … are
 * already set by run-local.sh / the CI job); `extraEnv` adds command-specific
 * vars such as DJANGO_SUPERUSER_*.
 */
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Run `manage.py <args...>` from the repo root; returns stdout. */
export async function runManage(
  args: string[],
  extraEnv: Record<string, string> = {},
): Promise<string> {
  const { stdout } = await execFileAsync(
    "uv",
    ["run", "python", "manage.py", ...args],
    { cwd: REPO_ROOT, env: { ...process.env, ...extraEnv } },
  );
  return stdout;
}
