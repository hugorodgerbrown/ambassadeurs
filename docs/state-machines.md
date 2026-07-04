# Where am I in the process?

This page explains, in plain language, the stages you move through when you use
Ski Parrainage — from registering, to being matched with a partner, to swapping
contact details and going to make your application together.

There are **two things that have a status**, and it helps to keep them apart:

1. **Your registration** — your own standing in the system. Are you waiting for a
   partner, paused, or finished?
2. **A match** — a specific pairing between you and one other person. Has it been
   proposed, half-accepted, confirmed, or fallen through?

You always have exactly one registration. Over a season you might be part of
several matches (if an early one doesn't work out, you go back in the queue and
get paired again).

For the engineering detail behind this page, see
[ADR 0011](decisions/0011-two-state-machines.md) (the two state machines),
[ADR 0007](decisions/0007-post-match-confirmation-workflow.md) (the contact
window), [ADR 0013](decisions/0013-paused-registration-state.md) (pausing and
rejoining) and [ADR 0009](decisions/0009-reveal-partner-first-name.md) (what your
partner can see).

---

## Part 1 — Your registration

Your registration is your place in the system. This is the answer to *"am I in
the queue, waiting to be matched?"*

| Status | What it means for you | What happens next |
|--------|-----------------------|-------------------|
| **Unverified** | You've signed up but haven't confirmed your email address yet. You are **not** in the queue. | Click the link in the confirmation email. That moves you to **Verified**. |
| **Verified** | You're active and in the queue, waiting to be matched (or already in an active match). | The system pairs you with an eligible partner as soon as one is available. When it does, a **match** is created (see Part 2). |
| **Paused** | You declined a match, or didn't respond in time, so you've been taken out of the queue. Nothing is lost — your registration is kept. | Go to your account page and choose **"Rejoin the queue"**. That moves you back to **Verified** and you'll be matched again. |
| **Withdrawn** | You chose to leave the program. | Nothing further. You can register again if you change your mind. |
| **Suspended** | You were reported as a no-show after a match was confirmed, so you've been removed from the pool. | This is set by program staff / the report process, not something you undo yourself. Contact the program if you think it's a mistake. |

### The short version

```
Unverified ──confirm email──▶ Verified ◀──rejoin──┐
                                 │                 │
                                 ├── decline / no reply in time ──▶ Paused
                                 │
                                 ├── you leave ──▶ Withdrawn
                                 │
                                 └── reported no-show ──▶ Suspended
```

**Verified** is the "healthy, waiting" state. Everything else is either a step
before it (Unverified) or a step out of the pool (Paused, Withdrawn, Suspended).
Only **Paused** has a self-service way back in.

---

## Part 2 — A match

Once you're **Verified** and a suitable partner is available, the system creates a
**match** — a pairing of one ambassador and one referee. You don't choose your
partner; the system assigns one. Both of you are notified.

From that moment there is a **contact window** (72 hours by default) in which
**both** of you must accept. Accepting is how you say *"yes, I'll go through with
this pairing"*.

Until you **both** accept, you can each see the other person's **first name**, but
**not** their email or phone number. Contact details are only revealed when both
sides have accepted. This is deliberate — it's the core privacy promise of the
service.

| Status | What it means for you | What happens next |
|--------|-----------------------|-------------------|
| **Proposed** | You've just been paired. Neither of you has accepted yet. You can see your partner's first name only. | Accept within the contact window. If your partner accepts first, you'll see the match move to **Pending**. |
| **Pending** | One of you has accepted; the system is waiting on the other. | The second person accepts → **Accepted**. If the window runs out with only one acceptance, it becomes **Expired**. |
| **Accepted** | Both of you accepted. **Contact details are now revealed.** This is the success state. | Get in touch with your partner and go make your application together (this happens off the app — see below). |
| **Declined** | One of you declined the pairing. | The match is over. The person who declined is **Paused**; the other person goes back to the **front of the queue** to be matched again quickly. |
| **Expired** | The contact window ran out before both of you accepted. | Same as declined: whoever didn't respond is **Paused**; the one who did accept goes to the **front of the queue**. |
| **Cancelled** | After a match was accepted, one party reported the other as a no-show. | The person who showed up goes back to the **front of the queue**; the reported person is **Suspended**. |

### The short version

```
                 ┌──────────────────────────────────┐
                 │                                   │
   Proposed ──▶ Pending ──both accept──▶ Accepted ──▶ (make your application)
      │            │                         │
      │            │                         └── no-show reported ──▶ Cancelled
      │            │
      │            └── window runs out ──▶ Expired
      │
      └── either side declines ──▶ Declined
```

**Accepted** is the goal. **Declined**, **Expired** and **Cancelled** all end the
match — but they don't end *you*: unless you were the one who declined, didn't
respond, or was reported, you're put back at the front of the queue and matched
again.

---

## Common questions

**"Where am I right now?"**
Look at your account page. It shows your registration status (Part 1) and, if you
have one, your current match (Part 2).

**"I've been matched — what do I do?"**
Accept the match within the contact window. Once your partner also accepts, you'll
both see each other's contact details and can arrange to meet and apply together.

**"Why can't I see my partner's phone number / email?"**
Because you haven't *both* accepted yet. Contact details are only shared once both
sides have said yes. Until then you see a first name so the pairing feels human.

**"My match expired / my partner declined. Am I out?"**
No. As long as you weren't the one who dropped out, you go straight back to the
**front** of the queue and get matched again soon.

**"I clicked decline / didn't respond in time. How do I get back in?"**
You're **Paused**, not removed. Open your account page and choose **"Rejoin the
queue"**.

**"What's the actual season-ticket application?"**
That happens **off this app**. Once you're matched and have accepted, the two of
you fill in the referral application and buy your passes together at the ticket
office. Ski Parrainage only handles finding you a partner — the discount, the
form, and the purchase all happen afterwards, elsewhere.
