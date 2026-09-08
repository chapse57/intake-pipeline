# Portfolio copy — every number below is measured, not estimated

## Scope note, read this first

**Xero is live and proven.** WF-3 runs against a real Xero organisation:
a completion webhook creates a supplier contact, a replay of the same webhook
creates nothing, and an induced 5xx produces three logged attempts and an alert
without leaving half-written state.

**DocuSign is not.** WF-2 is imported and structurally tested but has never run
— it needs a sandbox and four templates. Nothing below claims otherwise. Say
"DocuSign-ready" or leave it out; do not imply it has sent an envelope.

---

## Title

```
Intake Pipeline — n8n + Notion + Claude + Xero: a guard that rejects what the model can't prove, and a webhook you can replay
```

Shorter, if the field is tight:

```
Intake Pipeline — n8n → Notion → Claude → Xero, every step logged
```

Alternative, if the guard angle is too abstract for the client you are pitching:

```
Recruiting intake on n8n: duplicate detection, AI role tagging, every step logged to Notion
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
> When a candidate is approved and the signed envelope comes back, the vendor
> record is created in Xero exactly once. DocuSign Connect redelivers
> completions, so the completion webhook was replayed against a live Xero
> organisation to prove it: the second delivery stopped at the idempotency check
> and never reached the Xero API. When Xero returns a 5xx, the run retries three
> times, writes an Events row per attempt, then alerts — and leaves the
> candidate's Xero Contact ID empty rather than half-onboarded.
>
> **Verified live: 20 submissions with 6 planted duplicates → 14 created, 6
> flagged, 0 lost.** 72 automated tests run with no credentials and no network;
> they drive the same JavaScript the n8n Code nodes run, so the tests and the
> workflow cannot drift apart. Workflows exported as JSON on GitHub.

## Skill tags

`n8n` · `Notion` · `Xero` · `API Integration` · `AI Agent Development` · `Workflow Automation`

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
7. **72 tests, no credentials** — the offline tier. Test names are the spec:
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

## Proposal opener

Two lines, then the specifics:

```
Attached: an n8n → Notion → Claude intake pipeline built on the same shape as
your spec (screenshots + GitHub). The part that matters for you: [their
requirement] — here is how it is handled: [node name / test number].
```

Concrete fills, by the requirement they name:

| They ask for | Point at |
|---|---|
| "duplicate detection" | The Duplicate Attempts column and Events shot 4. Exact on two keys, fuzzy only within a domain, never a silent merge. |
| "AI classification" | The guard. 11 of 16 recorded model outputs are rejected by it in the fixture suite. |
| "reliability" / "error handling" | Events. One row per node, an error workflow that logs and alerts, and a rule that a failed log write never fails the run. Then shot 10: three retries and an alert, logged. |
| "Xero" / "accounting integration" | OAuth 2.0 authorization-code against a live Xero org, and shot 8 — the replayed webhook that creates exactly one contact. |
| "n8n experience" | The exported JSON, generated from a script so the Code nodes and the tested source cannot diverge. |
| "Notion as a database" | The three-table schema: attribution that cannot be typed over, stored match keys, an append-only event log. |

## Numbers you can defend if asked

| Claim | Where it comes from |
|---|---|
| 20 → 14 created, 6 flagged, 0 lost | Live run against the deployed webhook, 2026-09-08. Candidates screenshot. |
| 72 automated tests | `pytest -m offline -v`. Runs on any machine, no credentials. |
| 11 of 16 model outputs rejected | `data/guard_cases.json` — recorded outputs, not live traffic. Say "fixture suite" if pressed; do not imply it is a production rejection rate. |
| 0.875 / 0.786 / 0.889 similarity | Normalized Levenshtein, computed by the same code the workflow runs. In `tests/test_normalize.py`. |
| "the model is never called for a duplicate" | Execution durations: 2.6–3.6s vs 6–11s. Visible in the executions list. |
| "the vendor record is created exactly once" | Live: two deliveries of `ENV-T9-DEMO-0001`, one Xero contact `c5a31759-…`, second run logged `idempotency / skipped`. The claim is one contact **per envelope**, not one contact in total — Xero holds two, from two different completed envelopes. |
| "retries three times then alerts" | Live: run 38, three `xero-contact` error rows then an `error` row. The retry loop is explicit precisely so this is countable. |

## What not to say

- Do not say the guard "catches hallucinations in production." In the final run
  it rejected nothing — every one of the 14 proposals was well-formed. The
  rejections are from the fixture suite and from an earlier build. The honest
  claim is that the guard is tested, not that it has fired in anger.
- Do not imply DocuSign is running. Xero is; DocuSign is not.
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
