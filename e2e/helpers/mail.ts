/**
 * Mailpit client — how the suite solves "the email problem".
 *
 * The app delivers confirmation, magic-link login, and match-notification URLs
 * ONLY by email (with DEBUG off there is no on-page shortcut). In CI the app's
 * SMTP is pointed at Mailpit, a disposable mail sink that also exposes an HTTP
 * API. These helpers poll that API for the message just sent to a given address
 * and pull the signed link out of the body — the automated equivalent of a
 * human opening their inbox and clicking the link.
 *
 * Mailpit API reference: https://mailpit.axllent.org/docs/api-v1/
 */
import { expect, type APIRequestContext, request } from "@playwright/test";

const MAILPIT_URL = process.env.MAILPIT_URL ?? "http://127.0.0.1:8025";

interface MailpitSummary {
  ID: string;
  Subject: string;
  To: { Address: string }[];
  Created: string;
}

interface MailpitMessage {
  ID: string;
  Subject: string;
  Text: string;
  HTML: string;
}

/** A thin, disposable wrapper around the Mailpit HTTP API. */
export class Mailbox {
  private constructor(private readonly api: APIRequestContext) {}

  /** Build a Mailbox with its own API request context. */
  static async create(): Promise<Mailbox> {
    const api = await request.newContext({ baseURL: MAILPIT_URL });
    return new Mailbox(api);
  }

  /** Release the underlying request context. Call in test teardown. */
  async dispose(): Promise<void> {
    await this.api.dispose();
  }

  /** Delete every message in the sink so a run starts from a clean inbox. */
  async clear(): Promise<void> {
    const res = await this.api.delete("/api/v1/messages");
    expect(res.ok(), "Mailpit clear failed").toBeTruthy();
  }

  /**
   * Wait for an email to `address` that contains a link matching `pattern`,
   * and return that link.
   *
   * This is deliberately link-driven rather than "newest message": a single
   * flow can drop several mails to one address (a confirmation, then a match
   * notice), and they can arrive out of order relative to when the test looks.
   * Polling for the specific link the caller needs — newest message first —
   * removes that race and the need to reason about subjects or copy.
   */
  async waitForLink(
    address: string,
    pattern: RegExp,
    opts: { timeoutMs?: number } = {},
  ): Promise<string> {
    const { timeoutMs = 15_000 } = opts;
    const deadline = Date.now() + timeoutMs;
    let seen = 0;

    while (Date.now() < deadline) {
      const res = await this.api.get("/api/v1/search", {
        params: { query: `to:${address}`, limit: 20 },
      });
      if (res.ok()) {
        const body = (await res.json()) as { messages: MailpitSummary[] };
        const newestFirst = body.messages.sort(
          (a, b) => Date.parse(b.Created) - Date.parse(a.Created),
        );
        seen = newestFirst.length;
        for (const summary of newestFirst) {
          const full = await this.getMessage(summary.ID);
          try {
            return extractLink(full, pattern);
          } catch {
            // This message has no matching link; try the next one.
          }
        }
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error(
      `No email to ${address} containing a link matching ${pattern} ` +
        `within ${timeoutMs}ms (saw ${seen} message(s) to this address).`,
    );
  }

  /** Fetch a full message (text + HTML) by Mailpit id. */
  private async getMessage(id: string): Promise<MailpitMessage> {
    const res = await this.api.get(`/api/v1/message/${id}`);
    expect(res.ok(), `Mailpit message ${id} fetch failed`).toBeTruthy();
    return (await res.json()) as MailpitMessage;
  }
}

/**
 * Extract the first URL matching `pathPattern` from a message body.
 *
 * We search the plain-text part first (unambiguous), falling back to HTML.
 * `pathPattern` is matched against the URL path so callers stay host-agnostic —
 * e.g. /\/register\/confirm\/[\w:-]+\//.
 */
export function extractLink(
  message: MailpitMessage,
  pathPattern: RegExp,
): string {
  const haystacks = [message.Text, message.HTML];
  const urlRe = /https?:\/\/[^\s"'<>)]+/g;
  for (const body of haystacks) {
    if (!body) continue;
    for (const url of body.match(urlRe) ?? []) {
      if (pathPattern.test(new URL(url).pathname)) return url;
    }
  }
  throw new Error(
    `No link matching ${pathPattern} in message "${message.Subject}".`,
  );
}
