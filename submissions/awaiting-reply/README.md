# Awaiting Reply

You sent the mail. You asked for the document, the decision, the date. Nothing came back, and by the time you remember, it is three weeks later and awkward to raise.

This automation watches that gap for you. Every weekday morning it looks at what you sent, works out what is genuinely still waiting on someone else, and tells you how many. It prepares the follow-ups as drafts. It never sends them.

## Trigger

Runs on a schedule, every weekday at 7:30 AM.

## Steps

1. **Work out today's window.** Reads the automation's own last run time and covers everything that crossed the waiting threshold since then, so a laptop that was off does not lose threads.
2. **Gather what is still on their side.** Walks your sent items in that window and keeps only threads where the ball is genuinely in their court.
3. **Record what each thread was asking for.** Recipients, thread link, what you asked, any date they promised, how many times you already chased.
4. **Check whether the answer came elsewhere.** Looks in Teams before flagging anything, because the reply often arrived by chat.
5. **Keep the real asks and decide how to chase.** Filters to threads that actually needed an answer, ranks them, and picks the move: mail, direct message, call, or drop it.
6. **Report the count, grouped by person.** Opens with the number, then one line per thread under each name.
7. **Prepare the drafts, never send them.** One draft per person covering all their threads, in that person's language, waiting for your review.

## What it will not do

It never sends a mail, never replies to a thread, and never posts to Teams. Everything it produces is a draft or a line in the report. That is the whole safety model, and it is deliberate: the worst a bad run can do is waste a minute of your reading.

It also will not chase on your behalf without you seeing it first, will not mark anything urgent, and will not copy a code, password or sign-in link into a draft.

## Two things it gets right that are easy to get wrong

**Silence is not the only way a request dies.** The most common ending is "I'll check and get back to you", followed by nothing. A naive version treats that as answered and drops the thread. This one counts a holding reply as still waiting, and restarts the clock from the promise rather than from your original mail. When someone gave a date and missed it, the draft points at their own commitment instead of complaining about the delay, which works better and reads better.

**The answer often arrived somewhere else.** People reply in chat, in a meeting, in the corridor. Step 4 checks Teams before anything is flagged, and says where the answer came from so a thread does not vanish from the list unexplained. On the first real run this removed four of six candidates, which is the difference between a useful digest and one you stop reading.

## How the window works

Each run only looks at threads that crossed the waiting threshold since the previous run, so nothing is reported twice and no thread is quietly skipped. Because it reads the automation's own last run time rather than the calendar, an outage widens the window by exactly as much as was missed: a weekend, a bank holiday, two weeks of leave. Catch-up is capped at thirty days, and the report says when the window was stretched so an unusually long list explains itself.

If the last run time is unavailable it falls back to a fixed window: mail sent three or four days ago, or three to five on a Monday to cover the weekend.

## Prompt injection

This automation reads mail and chat messages that anyone can send you, then makes decisions from them. That is a real attack surface, and it is worth being plain about.

Every step that ingests content treats it as data and never as instructions. The attack that matters here is not dramatic: a message stating the matter is closed, or asking for no follow-up, would quietly remove a legitimate thread from your list, and you would never notice what was missing. So a message that says so is recorded as having said it, and the thread is kept. Links found in mail are not opened.

The protection you can rely on is structural rather than textual. Since the automation cannot send anything, the worst outcome of a successful injection is a misleading report or a poor draft, both of which you see before anything leaves your account. Read a draft before you send it, as you would any other.

## Permissions

It needs mail and Teams access, and nothing else. Turn off the filesystem, browser and shell servers for this automation: it has no use for them, and a job that ingests untrusted mail should not also hold a shell.

Leave auto-approve for writes switched off. Creating a draft is the only write it performs, and it is worth seeing.

## Tuning it

The waiting threshold is three days, set in step 1. Raise it if your correspondents are slower than that, or you will be chasing people who were never late. Keep it well away from the gap between runs.

The cap of eight threads per report is in step 5, and the report always says how many were left out.
