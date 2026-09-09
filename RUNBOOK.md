# Runbook — what to run, and what only you can capture

## Live state, 2026-09-08

WF-1 is published and answering at
`https://<your-n8n-host>/webhook/intake`.

T12 was executed against it by replaying all 20 rows of `data/candidates.csv`
in submission order:

```
20 submitted -> 14 created (13 New, 1 Review), 6 flagged, 0 lost
```

Every one of the 14 came back with a role at confidence 0.95–0.98 and a quote
inside the length cap; the guard rejected nothing. Sophia Lindqvist is the one
Review row, linked by `Duplicate Of` to Sofia Lindqvist (0.875 on the shared
`harborlight.se` domain). The six flagged submissions each incremented
`Duplicate Attempts` on the row they collided with and created nothing.

**WF-3 has since run end to end against a real Xero organisation** (a free
developer org, `Intake Demo`, its own tenant id). Two things were proven
against it:

*T9 — idempotency.* Marcus Webb was given `Envelope ID = ENV-T9-DEMO-0001` and
the completion webhook was posted twice. The first run created the Xero contact
`c5a31759-…`; the second stopped at
`idempotency / skipped` — "already produced a Xero contact; replay ignored" —
without calling Xero at all. Xero's contact list shows one Marcus Webb.

*T10 — retry then alert.* The fault seam was pointed at
`https://httpbin.org/status/500` for the `andesreach.cl` domain and Tomás
Herrera's completion was posted. Events shows four rows for that run:

```
xero-contact  attempt 1: The service was not able to process your request
xero-contact  attempt 2: The service was not able to process your request
xero-contact  attempt 3: The service was not able to process your request
error         Xero contact creation failed after 3 attempts: ...
```

His `Xero Contact ID` stayed empty and his Status never became `Onboarded` — a
failed run leaves no half-written state. The seam was switched back off
afterwards; both `xero_fault_*` Config values are blank again.

**WF-2 has since run against a DocuSign developer sandbox, and T8 closed the
chain.** Four templates were created by API from `docusign/contracts/*.pdf`,
each with `signHere`, `dateSigned` and `fullName` tabs anchored to `/sig1/`,
`/dt1/` and `/nm1/`; the creating workflow read all four back and confirmed one
of each tab per template before the ids were written into `Config`.

Priya Raghunathan was then set to `Approved`. WF-2 picked her up on its
`Status = Approved AND Envelope ID is empty` query, took the Closer branch from
her role, and created envelope `10542476-e6e8-847d-81a0-07da059d08e9`, moving
her to `Sent`. The envelope was signed in DocuSign. DocuSign Connect
(configuration `n8n WF-3 completion`, REST v2.1, SIM, `envelope-completed`
only) then called `/webhook/docusign-complete`; n8n run 42 shows `mode:
webhook`, which is what distinguishes this from the T9 and T10 posts. WF-3
created Xero contact `d4441ca3-…` and moved her to
`Onboarded`.

**The chain was then run a second time, on camera.** Daniel Okafor (Coach) was
approved, WF-2 sent envelope `7e9b2813-04eb-8f79-8024-3796459c08bd` from the
Coach template, the envelope was signed, and n8n run 44 — `mode: webhook`
again — created Xero contact `6c9ef4a4-…`. That run
is the 30-second video: Notion `Sent` → sign → a new WF-3 row appearing on its
own → Notion `Onboarded` → the contact in Xero. All four templates except
Setter and Full Cycle have now sent a real envelope.

In the n8n executions list the manual runs carry a small flask icon and the
webhook-triggered ones do not. That icon is the cheapest available proof that
the WF-3 runs were not started by hand, and it is visible in the video.

Two honest notes on the first run. Her `Email` was changed from the fixture address
to a plus-aliased mailbox the operator controls, because an envelope has to
reach someone who can sign it; `Email Norm` was changed with it so the row is
not internally inconsistent. And the signer was the operator, not a candidate —
what the run proves is that the workflow sent the envelope and that the
signature carried the record into Xero without a hand-fired webhook.

## Where the live values live

The real n8n host, webhook URLs, database ids and tenant id are in `.env`,
which is gitignored. Nothing in the tracked files points at a running
instance — see the note below on why.

## Run the tests

Offline tier — no credentials, no network, runs anywhere:

```bash
pip install -r requirements.txt
pytest -m offline -v
```

Integration tier — **this has to run from your machine.** The sandbox these
were built in cannot reach n8n Cloud, so the only place the live tests have
ever run is your PC:

```bash
set -a && source .env && set +a     # Git Bash / WSL
pytest -m integration -v
```

Windows PowerShell:

```powershell
Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
pytest -m integration -v
```

`.env` already has the webhook URL and all three database ids. Add
`NOTION_TOKEN` (the `ntn_...` value) and T1–T4, T11 and T12 will run. T8–T10
skip themselves until DocuSign and Xero are set up.

The terminal output of `pytest -m offline -v` and `pytest -m integration -v`
is portfolio material in its own right — it is the only artefact that shows
the twelve cases as *tests* rather than as a claim in a README.

## Screenshots

These have to be captured on your machine (Win+Shift+S). Each row below is a
URL plus the state to put it in first.

| # | What | Where | Set up first |
|---|---|---|---|
| 1 | Candidates, 14 rows | [Candidates](https://www.notion.so/4046c96105ea4b469edddf3d152cb1b2) | Hide columns — see below |
| 2 | Events step log | [Events](https://www.notion.so/ec414c5e2999473082f2a0522fe09a89) | Nothing; scroll to the top |
| 3 | WF-1 canvas | WF-1 in your n8n | Press `1` to fit to view |
| 4 | A green end-to-end run | the WF-1 Executions tab | Open the newest successful one |
| 5 | Guard rejection detail | Events | Filter `Step = guard`, `Result = rejected` |
| 6 | Test output | Your terminal | `pytest -m offline -v` |
| 7 | T9 — one envelope, one contact | [Events](https://www.notion.so/ec414c5e2999473082f2a0522fe09a89) | Top three rows: `idempotency`, `xero-contact`, `docusign-webhook` |
| 8 | T9 — Xero's side | Xero → Contacts | The All tab, not Suppliers |
| 9 | T10 — three attempts then an alert | Events | Top five rows of the Tomás Herrera run |
| 10 | The envelope, completed | DocuSign → Agreements → the envelope's detail page | Better than the list view: status, recipient, signing time and the template's document name are all in one frame |
| 11 | The signed contract | The document thumbnail on that page | Crop below the `Printed name` line — a Windows activation watermark sits under the footer and will otherwise be the first thing a reviewer notices |

**Shot 1 needs the view tidied first.** The Candidates table has 20
properties, so Name and Role cannot be on screen at the same time. In Notion:
`⋯` (top right of the table) → **속성 표시 여부** → switch off Application
Text, Created, Email, Email Domain, Email Norm, Envelope ID, Log, Phone,
Phone E164, Duplicates, Submitted By, Xero Contact ID. What is left —
Name, Role, Role Confidence, Role Evidence, Status, Recruiter,
Duplicate Attempts, Duplicate Of — is the whole story in one frame. Takes
about thirty seconds and it is the single most useful image in the set.

**Shot 5 is the differentiator.** Anyone can show a pipeline that worked.
The Events rows reading `reject | evidence_too_long | {"chars":111,"ratio":0.6}`
show a pipeline that refused to write something it could not justify. Those
rows are from the earlier short-text run; if you want a fresh one, post a
candidate whose application is two sentences long and the guard will reject it
again.

## Video (30s)

Order that reads well without narration:

1. Terminal: `pytest -m offline -v` scrolling to `80 passed`
2. n8n WF-1 canvas, then hit the webhook (curl or Postman) with two
   candidates — one new, one a case-changed duplicate of it
3. n8n execution list: two green runs
4. Notion Candidates: one new row appears, the other row's Duplicate
   Attempts goes 0 → 1, no second row
5. Notion Events: scroll the step log for both runs

Nothing in that sequence needs editing or voice-over; it is the claim and the
proof in the same frame.
