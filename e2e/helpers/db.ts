/**
 * Per-test database reset.
 *
 * The matching engine matches across the ENTIRE pool of verified registrations,
 * so any registration a test leaves behind can be cross-matched by a later test
 * — tests are not independent unless the pool is emptied between them. (This
 * bites within a single suite run, so a fresh CI database does not fix it.)
 *
 * We truncate the domain tables directly over a Postgres connection — far faster
 * than booting Django per test, and the suite already targets Postgres. When
 * `DATABASE_URL` is unset (the SQLite no-container fallback) this is a no-op and
 * matching tests may be non-deterministic; that is the documented reason to run
 * the suite against Postgres.
 */
import { Client } from "pg";

// Every table holding per-run state. CASCADE clears dependent rows
// (auth_user_groups, admin log, …); RESTART IDENTITY keeps pks predictable.
const TABLES = [
  "matching_match",
  "matching_registration",
  "billing_payment",
  "auth_user",
  "django_session",
];

/** Open a connected client, or null when there is no Postgres `DATABASE_URL`. */
async function connect(): Promise<Client | null> {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) return null;
  const client = new Client({ connectionString });
  await client.connect();
  return client;
}

/** Truncate all per-run tables. No-op without a Postgres `DATABASE_URL`. */
export async function resetDatabase(): Promise<void> {
  const client = await connect();
  if (!client) return;
  try {
    const list = TABLES.map((t) => `"${t}"`).join(", ");
    await client.query(`TRUNCATE ${list} RESTART IDENTITY CASCADE`);
  } finally {
    await client.end();
  }
}

/**
 * Move the contact window of every active (PROPOSED/PENDING) match into the
 * past, so the next `expire_matches` run treats it as lapsed. This is how the
 * expiry scenario "manually expires" a match instead of waiting out the real
 * 72-hour window.
 */
export async function expireContactWindowNow(): Promise<void> {
  const client = await connect();
  if (!client) throw new Error("expireContactWindowNow requires DATABASE_URL");
  try {
    await client.query(
      `UPDATE matching_match
         SET expires_at = now() - interval '1 hour'
       WHERE status IN ('PROPOSED', 'PENDING')`,
    );
  } finally {
    await client.end();
  }
}

/** Return the registration status for an email, or null if absent. */
export async function registrationStatus(email: string): Promise<string | null> {
  const client = await connect();
  if (!client) throw new Error("registrationStatus requires DATABASE_URL");
  try {
    const { rows } = await client.query(
      `SELECT r.status
         FROM matching_registration r
         JOIN auth_user u ON u.id = r.user_id
        WHERE u.email = $1`,
      [email],
    );
    return rows[0]?.status ?? null;
  } finally {
    await client.end();
  }
}

/** Return the status of the most recently created match, or null if none. */
export async function latestMatchStatus(): Promise<string | null> {
  const client = await connect();
  if (!client) throw new Error("latestMatchStatus requires DATABASE_URL");
  try {
    const { rows } = await client.query(
      `SELECT status FROM matching_match ORDER BY created_at DESC LIMIT 1`,
    );
    return rows[0]?.status ?? null;
  } finally {
    await client.end();
  }
}
