# Portfolio copy — every number below is measured, not estimated

## Paste-ready Upwork entry

Measured from the live form, not from a blog post: **title 70, role 100,
description 600, five skills.** The description below is 599 characters, so it
fits with one to spare; the first 253 already carry the whole argument in case
a field truncates earlier than expected.

**Title** (61 chars)

```
Intake pipeline on n8n: dedupe, AI role guard, DocuSign, Xero
```

**Your role** (88 chars)

```
Sole developer: n8n workflows, Notion schema, the guard, the fixtures and the test suite
```

**Description** (599 chars)

```
Recruiting intake on n8n. A form post becomes a deduplicated Notion record, an AI-proposed role a deterministic guard must approve, a role-specific DocuSign contract, and - once signed - a supplier record in Xero. Every node writes one row to an append-only log.

Proven live, not mocked. 20 submissions, 6 planted duplicates: 14 created, 6 flagged, 0 lost. A replayed webhook creates exactly one contact. An induced Xero 5xx gives three logged retries and an alert row, no half-written state.

80 tests, no credentials, on the same JavaScript the n8n nodes run.

github.com/chapse57/intake-pipeline
```

**Skills** — five is the maximum, and two obvious choices do not exist in
Upwork's taxonomy: there is no `Workflow Automation` and nothing matching
`AI Agent Development`. These five are what the autocomplete actually offers:

```
n8n · Automation · API Integration · DocuSign · Notion
```

Swap `DocuSign` or `Notion` for `Xero` when the job post names it. AI is
carried by the description instead, twice, since no skill tag can carry it.

**Attachment order.** The first image is the one most people will judge the
whole piece on, so lead with the outcome, not the architecture:

1. Video (30s) — the chain completing
2. Candidates after the batch — 14 rows, 6 duplicates flagged, 1 Review
3. The signed contract
4. The envelope, completed
5. Events, one candidate's five rows
6. WF-1 in n8n
7. Xero contacts
8. The test run

---

## Scope note, read this first

**All four workflows have run, and they have run as one chain.** A submission
went in at the webhook and came out as a signed contract and a supplier record
in Xero, with each stage triggered by the stage before it. Nothing in the path
was simulated; the only hand-fired webhooks in this project are the T9 replay
and the T10 fault, and both are labelled as such.

**What is still generated: the people.** The 20 candidates are fixtures, the
Xero organisation is a trial, the DocuSign account is a developer sandbox, and
the envelope was signed by the operator rather than by a candidate. Say so if
asked. None of it weakens the claim — the claim is about the pipeline, not
about the traffic — but discovering it after you have implied otherwise does.

---

## Title

```
Intake Pipeline — n8n + Notion + Claude + DocuSign + Xero: from a form post to a signed contract and a supplier record, every step logged
```

Shorter, if the field is tight:

```
Intake Pipeline — webhook → Notion → Claude → DocuSign → Xero, every step logged
```

Alternative, if the guard angle is the one that fits the client:

```
Intake Pipeline — a guard that rejects what the model can't prove, and a webhook you can replay
```

## Body

> Recruiting intake where several recruiters add candidates at once and nobody
> chases the same one twice.
>
> n8n takes a candidate from a webhook, normalizes the email and phone into
> stored match keys, and checks Notion for duplicates — exact on either key,
> plus a name-similarity check that only fires inside the same email domain and
> routes to Review instead of merging. Duplicates stop there: the row that
> already exists gets its Duplicate Attempts incremented and keeps its original
> recruiter, and the model is never called.
>
> New candidates go to Claude for a role proposal. Nothing it returns is
> trusted. A deterministic guard checks that the role is one of four exact
> strings, that the quoted evidence actually appears in the application text
> and is short enough to mean something, and that the confidence is a real
> number in range. A rejected proposal rejects the label, never the person —
> the candidate is still created, with Status = Review and no role, and the
> reason is in the log.
>
> Every node writes one row to an Events table. One candidate's whole path is
> five rows: normalize → dedupe → llm-role → guard → notion-create. A duplicate
> is two.
>
> Approving a candidate sends them the contract for their role. Four DocuSign
> templates, one per role, created by API with the signature fields anchored to
> text in the document rather than to coordinates — reword the contract,
> regenerate, and the fields still land on the signature line.
>
> When the signed envelope comes back, the vendor record is created in Xero
> exactly once. DocuSign Connect redelivers completions, so the completion
> webhook was replayed against a live Xero organisation to prove it: the second
> delivery stopped at the idempotency check and never reached the Xero API.
> When Xero returns a 5xx, the run retries three times, writes an Events row per
> attempt, then writes an alert row — and leaves the candidate's Xero Contact ID
> empty rather than half-onboarded. The alert node is wired to a webhook URL that
> is deliberately unset in this demo, so nothing is delivered to a channel.
>
> **The whole chain has run end to end.** One candidate went from a webhook
> post to a signed contract and a Xero supplier record with every stage
> triggered by the one before it — the envelope was sent because a human
> approved the row, and the Xero contact exists because someone actually signed.
>
> **Verified live: 20 submissions with 6 planted duplicates → 14 created, 6
> flagged, 0 lost.** 80 automated tests run with no credentials and no network;
> they drive the same JavaScript the n8n Code nodes run, so the tests and the
> workflow cannot drift apart. Workflows exported as JSON: github.com/chapse57/intake-pipeline

## Skill tags

`n8n` · `Notion` · `DocuSign` · `Xero` · `API Integration` · `AI Agent Development` · `Workflow Automation`

## Screenshot captions

Use these; they say what the reader should look at rather than restating the
image.

1. **Candidates, after the batch** — 20 submissions in, 14 rows out. The six
   with Duplicate Attempts = 1 are the planted duplicates; no second row was
   created for any of them. Sophia Lindqvist is the only Review: 0.875 name
   similarity to Sofia Lindqvist on the same domain, flagged for a human
   instead of merged.
2. **Events, full log** — every node writes exactly one row. Nothing in the
   pipeline runs without leaving a trace.
3. **One candidate, five rows** — normalize, dedupe, llm-role, guard,
   notion-create. The llm-role row holds the model's raw reply; the guard row
   holds the verdict and why. This is the whole justification for a role label,
   after the fact.
4. **One duplicate, two rows** — dedupe returns `exact` on the normalized
   email and the run ends. No LLM call, no cost, no attribution change.
5. **WF-1 in n8n** — 21 nodes. The branch off Route going up is the duplicate
   path; it terminates immediately.
6. **A single execution** — 11.1s for a new candidate. Note the durations in
   the list beside it: the 2.6–3.6s runs are duplicates that never reached the
   model.
7. **80 tests, no credentials** — the offline tier. Test names are the spec:
   `test_gmail_dot_aliasing_is_not_collapsed`,
   `test_wf1_has_no_branch_that_drops_a_candidate_after_the_guard`.
8. **The same webhook, delivered twice** — first delivery creates the Xero
   contact, second stops at `idempotency / skipped` and never calls Xero.
   DocuSign Connect really does redeliver; this is what stops it becoming a
   duplicate supplier.
9. **Xero's side of the same story** — two envelopes completed, two contacts.
   `ENV-T9-DEMO-0001` was delivered twice and Marcus Webb appears once. Tomás
   Herrera, whose run failed all three attempts, is absent entirely: the failed
   run left nothing behind in the accounting system.
10. **Xero returning 500** — three attempts, each its own Events row, then an
    alert. The candidate's Xero Contact ID stays empty: a failed run leaves no
    half-written state.
11. **The envelope, completed** — DocuSign's own record of the run. The subject
    line is the one WF-2 composes, the document is `Contractor Agreement -
    Closer`, chosen from the candidate's role, and the status is Completed.
    Nothing here was set up by hand.
12. **The signed contract** — the signature, the date and the printed name each
    sit where they belong. Those three fields were placed by anchor strings in
    the PDF, not by coordinates, which is why the layout survives an edit to
    the contract text.

## Proposal opener

Two lines, then the specifics:

```
Attached: an n8n → Notion → Claude intake pipeline built on the same shape as
your spec — github.com/chapse57/intake-pipeline. The part that matters for you: [their
requirement] — here is how it is handled: [node name / test number].
```

Concrete fills, by the requirement they name:

| They ask for | Point at |
|---|---|
| "duplicate detection" | The Duplicate Attempts column and Events shot 4. Exact on two keys, fuzzy only within a domain, never a silent merge. |
| "AI classification" | The guard. 11 of 16 recorded model outputs are rejected by it in the fixture suite. |
| "reliability" / "error handling" | Events. One row per node, an error workflow that catches failures from every workflow, and a rule that a failed log write never fails the run. Then shot 10: three retries and an alert row, all countable. |
| "Xero" / "accounting integration" | OAuth 2.0 authorization-code against a live Xero org, and shot 8 — the replayed webhook that creates exactly one contact. |
| "DocuSign" / "e-signature" | Shots 11 and 12. Four templates created by API with text-anchored tabs, the right one chosen by role, and a signed envelope that carried itself into the accounting system through Connect. |
| "end-to-end" / "full workflow" | The T8 chain: one candidate from webhook post to Xero contact with no step fired by hand. Most portfolios show pieces; this one shows the seam between them holding. |
| "n8n experience" | The exported JSON, generated from a script so the Code nodes and the tested source cannot diverge. |
| "Notion as a database" | The three-table schema: attribution that cannot be typed over, stored match keys, an append-only event log. |

## Numbers you can defend if asked

| Claim | Where it comes from |
|---|---|
| 20 → 14 created, 6 flagged, 0 lost | Live run against the deployed webhook, 2026-09-08. Candidates screenshot. |
| 80 automated tests | `pytest -m offline -v`. Runs on any machine, no credentials. |
| 11 of 16 model outputs rejected | `data/guard_cases.json` — recorded outputs, not live traffic. Say "fixture suite" if pressed; do not imply it is a production rejection rate. |
| 0.875 / 0.786 / 0.889 similarity | Normalized Levenshtein, computed by the same code the workflow runs. In `tests/test_normalize.py`. |
| "the model is never called for a duplicate" | Execution durations: 2.6–3.6s vs 6–11s. Visible in the executions list. |
| "the vendor record is created exactly once" | Live: two deliveries of `ENV-T9-DEMO-0001`, one Xero contact `c5a31759-…`, second run logged `idempotency / skipped`. The claim is one contact **per envelope**, not one contact in total — Xero holds two, from two different completed envelopes. |
| "retries three times then writes an alert" | Live: run 38, three `xero-contact` error rows then an `error` row. The retry loop is explicit precisely so this is countable. Say "alert row", not "alert" — the Slack node is wired but its webhook URL is unset, so no message is delivered anywhere. Claiming a delivered alert is the one sentence in this project that a client could disprove in ten seconds. |
| "the chain runs end to end" | Live, twice, with different roles. Priya Raghunathan (Closer): envelope `10542476-…` → run 42 `mode: webhook` → Xero `d4441ca3-…`. Daniel Okafor (Coach): envelope `7e9b2813-…` → run 44 `mode: webhook` → Xero `6c9ef4a4-…`. `mode: webhook` is the part that proves nobody fired it by hand; in the n8n executions list the manual runs carry a flask icon and these two do not. |
| "four templates, chosen by role" | The Switch node has four role branches and a fallback; the fallback writes a `dispatch skipped` Events row rather than sending the wrong contract. Template ids live only in `Config`. |

## What not to say

- Do not say the guard "catches hallucinations in production." In the final run
  it rejected nothing — every one of the 14 proposals was well-formed. The
  rejections are from the fixture suite and from an earlier build. The honest
  claim is that the guard is tested, not that it has fired in anger.
- Do not say a candidate signed the contract. The operator signed it, from a
  plus-aliased address on his own mailbox. What is proven is that the envelope
  was sent by the workflow, was signed, and that the signature carried the
  record into Xero without anyone touching a webhook. Who held the pen is not
  the claim, but pretending it was someone else is a lie you would be caught
  in by one look at the recipient field in the screenshot.
- Do not call the four contracts real legal documents. They are plausible
  one-page agreements written for this project. They demonstrate template
  routing and anchored signature fields, not employment law.
- Do not say WF-2 runs on a schedule in production. It has a one-minute
  schedule trigger, but every run so far was started by hand so the demo stays
  controlled. "Polls on a schedule" describes the design; "has been polling
  continuously" would not be true.
- The Xero 500 in shot 10 was induced through an env-gated fault seam pointed
  at httpbin, not an outage. Say "induced" if asked; it is a test, and tests
  that only pass when the weather is good are not tests.
- Do not paste the live webhook URL anywhere public. It is unauthenticated and
  every new candidate it accepts costs an Anthropic call. The repository is
  scrubbed of the host for this reason.
- If a client asks "is this production-ready?", the honest answer names the
  webhook auth gap before they find it: the pipeline is, the front door is not,
  and the README says exactly what it needs. Volunteering a known limitation
  reads as competence; having it discovered reads as the opposite.
- Do not say "Xero has exactly one contact." It has two, from two completed
  envelopes. The idempotency claim is per-envelope. Getting this wrong in a
  caption is the kind of small overreach that costs you the whole piece's
  credibility when someone counts the rows.
- Do not call the 20 candidates real data. They are generated; say so if asked.
  It costs nothing and it is the difference between a portfolio piece and a
  liability.

---

## Second Upwork entry: WF-4, the public inquiry form (added 2026-10-24)

A separate project, so it can be pinned to Claude and automation proposals
without the recruiting story. Images are in `upwork-assets/wf4/`, 1600x1200,
in this order; every number on them comes from the Events and Inquiries
tables, the n8n execution list, or `/api/status` on 2026-10-24.

**Title** (67)

```
Live inquiry form on n8n: signed relay, spam screen, Claude + guard
```

**Your role** (85)

```
Sole developer: page, Cloudflare Worker relay and monitor, n8n workflow, guard, tests
```

**Description** (563)

```
A public inquiry form, live since Oct 24, 2026. A Cloudflare Worker signs each request; n8n checks the signature, screens spam with free rules, asks Claude for a label, and a deterministic guard decides whether to trust it. Every request becomes a Notion row, spam included. New inquiries, errors and outages go to Slack.

So far it has seen test traffic only: 4 signed requests and 1 unsigned probe, which got a 401 and never reached the model. The first live run caught a guard bug, now a regression test.

136 offline tests.
github.com/chapse57/intake-pipeline
```

**Skills:** `n8n · Claude API · Cloudflare · API Integration · AI Automation`

**Images**

1. `01-cover.png` - architecture and four measured facts (4.4 s webhook to Slack, 401, 23 nodes, 136 tests)
2. `02-one-request-six-log-rows.png` - run 5, sent through the live page, its six Events rows
3. `03-live-inquiry-page.png` - the page, rendered from `worker/public/index.html`
4. `04-first-live-run-caught-a-bug.png` - runs 1 and 2, same message, guard reject then accept
5. `05-n8n-canvas.png` - the published WF-4 canvas, cut into three rows
6. `06-before-the-model-and-uptime.png` - signature, spam screen, uptime monitor
7. Web link: github.com/chapse57/intake-pipeline (its preview shows the repo About text, so keep that line current)

**Left out on purpose.** The live page address is not in the entry or the
images: it is a contact form, and Upwork treats off-platform contact routes in
a profile as a policy problem. Share it inside a contract or when a client asks
to see it running.

**Update weekly** with the operations log: the description's second paragraph
and the cover's last line are the only places the traffic numbers appear.
