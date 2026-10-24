# Intake Pipeline

Recruiting intake on n8n: webhook → normalize → duplicate check → Claude
proposes a role → deterministic guard → Notion → DocuSign → Xero, with every
step logged to an append-only Events table.

Since 2026-10-24 the same guard pattern also runs **in production** behind a
public contact form (WF-4): a signed relay on Cloudflare, a free spam screen,
Claude proposes a label, the guard decides whether to believe it, Notion stores
every request (spam included), Slack is told about the rest, and an outside
health check records every outage. See [WF-4 in production](#wf-4-in-production).

Everything here is checkable. The workflows are exported as importable JSON,
the JavaScript in this repo *is* the JavaScript the n8n Code nodes run, and the
offline suite exercises it with no credentials and no network.

## What has actually run

| | Status |
|---|---|
| WF-1 intake — dedupe, Claude, guard, Notion | **live**; 20 submissions → 14 created, 6 flagged, 0 lost |
| WF-2 dispatch — DocuSign templates | **live**; envelope sent from the Closer template and signed |
| WF-3 completion — idempotency, retry, alert row | **live** against a real Xero organisation |
| WF-0 error handler | **live**; it caught a real credential failure during setup. Since 2026-10-24 it is WF-4's error workflow and delivers to Slack |
| WF-4 inquiry form — signature, spam screen, Claude label, guard, Notion, Slack | **in production since 2026-10-24** on self-hosted n8n; first live run found a guard rule that rejected good labels on short messages (fixed, regression-tested) |
| Cloudflare relay + uptime monitor | **live**; the form, the HMAC signing and a 5-minute outside health check run on one Worker |
| PostgreSQL warehouse layer — migrations, immutable loads, role isolation | **live on Azure**; 20 rows → 14 candidates + 6 flagged, `pytest -m warehouse` 9 passed against an Entra-only Flexible Server |

The four steps have also run as one chain, which is a different claim: a
submission was posted to the webhook, deduped, labelled, guarded and written
to Notion; approving it sent a real DocuSign envelope; signing that envelope
made DocuSign Connect call WF-3; and WF-3 created the supplier contact in
Xero. No step was simulated and no webhook was fired by hand.

`RUNBOOK.md` has the reproduction steps and what was measured. `PORTFOLIO.md`
has the write-up and, more usefully, the list of claims that would be
overreach.

```
data/
  build_fixtures.py   regenerates the fixtures and verifies them against normalize.js
  candidates.csv      20 submissions, 6 planted duplicates, with expected outcomes
  recruiters.csv      4 recruiters
  guard_cases.json    16 model outputs and the verdict each must receive
n8n/
  normalize.js        email/phone normalization + duplicate classification
  guard.js            the deterministic check on the model's role proposal
  inquiry.js          WF-4: relay signature check, spam screen, label guard
  build_workflows.py  emits the five workflow files below
  workflows/
    WF-0_error_handler.json   Error Trigger → Events row → Slack
    WF-1_intake.json          webhook → normalize → 3 lookups → classify → Claude → guard → Notion
    WF-2_dispatch.json        poll Approved+unsent → Switch(role) → DocuSign → mark Sent
    WF-3_complete.json        DocuSign webhook → idempotency → Xero (explicit 3× retry) → Onboarded
    WF-4_inquiry.json         signed webhook → spam screen → Claude → guard → Notion → Slack
worker/
  public/index.html   the public inquiry form
  inquiry-relay.js    Cloudflare Worker: serves the form, signs and forwards it to n8n
  monitor.js          5-minute health check of n8n; Slack on down/up; /api/status
deploy/
  docker-compose.yml  self-hosted n8n (free Community edition) + ngrok static domain
  *.bat               one-click setup steps on the Windows host; secrets stay in deploy/*.env
docusign/
  build_contracts.py        generates the four role contracts as PDFs
  contracts/*.pdf           Setter / Coach / Closer / Full Cycle, one page each
  build_template_workflow.py  emits a throwaway n8n workflow that creates the
                            four DocuSign templates from those PDFs and then
                            reads the tabs back to prove the anchors landed
warehouse/
  migrations/         001_schemas … 006_roles: four schemas, manifest, model, grants
  migrate.py          applies them once each, hash-checked
  load.py             the loader: hash → manifest → raw → validate → dedupe → core/restricted
  conn.py             admin / loader / analyst identities; password or Entra token
  azure/provision.sh  the Azure resources as az commands
docs/warehouse.md     ERD + data dictionary
tests/                T1–T12, W1–W9, I1–I5 (inquiry), R1–R3 / M1–M2 (relay, monitor),
                      plus structural checks on the workflow files
```

## Running

```bash
pip install -r requirements.txt

# offline: no credentials, no network.
pytest -m offline

# integration: needs a running n8n and the Notion databases
cp .env.example .env && $EDITOR .env
set -a && source .env && set +a
pytest -m integration

# warehouse: needs a PostgreSQL 16 and three identities (see below)
pytest -m warehouse
```

Integration tests skip themselves with a named list of missing variables
rather than failing, so `pytest` on a clean checkout is green.

## Importing the workflows

1. In n8n, create four credentials with **exactly these names** — the nodes
   bind to them by name on import:

   | Name | Type |
   |---|---|
   | `Notion Intake` | Notion API |
   | `Anthropic Intake` | Anthropic |
   | `DocuSign Sandbox` | OAuth2 API (generic; authorization-code against `account-d.docusign.com`, scope `signature`, client auth in the header) |
   | `Xero Demo` | Xero OAuth2 API |

2. Import `WF-0` first, then `WF-1`, `WF-2`, `WF-3`. In each of the three
   pipeline workflows open **Settings → Error workflow** and pick `WF-0 Error
   Handler` — n8n assigns ids on import, so this cannot be pre-wired in the
   file.

3. Open the `Config` node in each workflow and replace every `REPLACE_ME`.
   The same node appears in all four; the values live nowhere else. The
   Notion database ids are already filled in.

4. Activate. WF-1's production URL is `/webhook/intake`, WF-3's is
   `/webhook/docusign-complete` — those are `N8N_INTAKE_WEBHOOK_URL` and
   `N8N_DOCUSIGN_WEBHOOK_URL` in `.env`.

**Credentials do not survive a clipboard paste.** Pasting a workflow onto the
canvas shows the right credential selected on every node, but the selection is
not persisted: the first production run fails with `Credentials not found`
(and, usefully, exercises WF-0). The red triangle on a node is the tell.

Re-picking the credential by hand on every node works but is tedious — WF-3
alone has ten. Faster, from the browser console on any n8n page in the
instance:

```js
const id = '<workflow id from the URL>';
const wf = (await fetch('/rest/workflows/' + id).then(r => r.json())).data;
for (const n of wf.nodes) {
  const t = n.parameters?.nodeCredentialType;          // e.g. 'notionApi'
  if (t === 'notionApi' && !n.credentials?.notionApi) {
    n.credentials = { ...n.credentials, notionApi: { id: '<cred id>', name: '<cred name>' } };
  }
}
await fetch('/rest/workflows/' + id, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name: wf.name, nodes: wf.nodes, connections: wf.connections,
                         settings: wf.settings, versionId: wf.versionId }),
});
```

The credential id is on its Details tab in n8n. Reload the editor afterwards;
the warning triangles are gone.

**Register n8n's redirect URI, not your instance's.** On n8n Cloud the OAuth
callback is a shared proxy — `https://oauth.n8n.cloud/oauth2/callback` — not
`https://<your-instance>/rest/oauth2-credential/callback`. The credential
screen prints the right one; copy it from there into the DocuSign app's
Redirect URIs. Getting this wrong does not produce a useful error: DocuSign
drops you on its own console home, as if you had merely logged in.

**A redirect URI takes a minute to reach DocuSign's auth server.** Immediately
after saving, `/oauth/auth` still answers *"The redirect URI is not registered
properly with Docusign"* for the URI you just registered. Wait and retry
before going looking for a mistake that is not there.

## The DocuSign templates

WF-2 sends an envelope from one of four templates chosen by role. The
templates are created by API rather than in the template builder, from
`docusign/`:

```bash
python3 docusign/build_contracts.py          # four one-page PDFs
python3 docusign/build_template_workflow.py  # a throwaway n8n workflow
```

Import that workflow, bind the DocuSign credential, run it once, copy the four
ids out of the final node into `Config`, and delete it. It reads the account
id from `/oauth/userinfo` rather than assuming one, because a demo account is
not always on `demo.docusign.net`.

**Signature fields are anchored to text, not to coordinates.** Each contract
carries `/sig1/`, `/dt1/` and `/nm1/` drawn in white; DocuSign finds them by
string match and places the tab there. Reword a contract, regenerate, recreate
the templates, and the fields still land beside the signature rule. Positioned
tabs would have to be re-measured every time the text reflows.

**Creation returns 201 whether or not the anchors were found.** An unplaced
tab is kept silently and only surfaces later as an envelope with nowhere to
sign. So the workflow reads each template back and fails loudly unless all
three tabs exist with the expected `anchorString`.

**Tabs are not in the template's `recipients` node.** `GET /templates/{id}`
with `include=recipients` returns the signer — `roleName`, `recipientId`,
routing — and no tabs at all. The tabs are behind
`GET /templates/{id}/recipients?include_tabs=true`. The first version of the
verification asked the wrong endpoint and reported four templates as broken
when all four were fine; the check was rewritten rather than relaxed.

Do not edit the `Normalize`, `Classify` or `Guard` Code nodes inside n8n.
They are generated from `normalize.js` / `guard.js`; edit the file, run
`python3 n8n/build_workflows.py`, re-import. A test fails if the export falls
behind the source.

### What the structural tests check

`tests/test_workflows.py` opens the exported files and, with the n8n globals
stubbed, runs every Code node and every `{{ }}` expression through `node`.
It also asserts that every connection targets a real node, that no node is
unreachable, that every IF and Switch output is wired, that every Events
write uses a Step/Result/Workflow value the Notion schema actually has, and
that every logging node is set to continue on error — a failed log write
must not take the pipeline down.

Then it executes the WF-1 `Classify`, `Guard` and `Build candidate` nodes and
the WF-3 `Parse event`, `Count attempt` and `Prepare Xero request` nodes
against canned Notion responses. The node code, not a copy of it.

### Design choices in the workflows

**WF-2 polls a filtered query instead of using the Notion trigger.** The
filter — `Status = Approved AND Envelope ID is empty` — is itself the
idempotency rule: a row that has an envelope is never picked up again, no
matter how many times someone touches it.

**WF-3 retries Xero in an explicit loop, not with the node's retry setting.**
Node-level retry is invisible; it produces one error after N silent attempts.
The loop writes an Events row per attempt, so "retries 3× then alerts" is a
claim you can verify by reading the log, which is the whole point of the log.

**The Slack URL is a credential, so it is not in `Config`.** The Slack nodes
read `SLACK_WEBHOOK_URL` from the n8n environment (`deploy/inquiry.env`, not
committed) and fall back to `Config.slack_webhook_url`. The node continues on
error and the next Events row records `slack sent` or `slack failed: …`, so
whether an alert was *delivered* is a fact in the log, not an assumption. The
first two WF-4 runs on 2026-10-24 logged `slack failed: URL parameter cannot be
empty`; the third logged `slack sent`. Both are kept.

**Every branch ends in a log node, including the ones that stop.** A rejected
guard verdict, an unknown envelope, a replayed webhook — each writes its
Events row and then ends at a node that does nothing. There is no path
through any workflow that produces no Events row.

## The two modules are the n8n nodes

`normalize.js` and `guard.js` each run three ways: `require()`d from a test,
piped JSON on stdin, or pasted into an n8n Code node. Each file ends with the
exact wrapper to append when pasting.

They are dependency-free and self-contained — `guard.js` does not import from
`normalize.js` even where it would save a few lines — because an n8n Code node
cannot `require` a sibling file. Anything shared would have to be duplicated
by hand into the node, and a hand-duplicated rule is a rule that drifts.

The tests drive these files through `node`. There is no Python restatement of
the logic to keep in sync.

## Live run, 2026-09-08

All 20 rows of `data/candidates.csv` were replayed against the deployed WF-1:

```
20 submitted -> 14 created (13 New, 1 Review), 6 flagged, 0 lost
```

Later the same day, one of those 14 was carried the rest of the way. Priya
Raghunathan was set to `Approved`; WF-2 picked her up on its filtered query,
chose the Closer template from her role, and sent envelope
`10542476-…`, moving her to `Sent`. The envelope was signed in DocuSign.
DocuSign Connect then called `/webhook/docusign-complete`, which is visible in
n8n as a WF-3 execution with `mode: webhook` — no hand-fired request — and
WF-3 created her contact in Xero and moved her to `Onboarded`.

That is the T8 case, and it is also the only run in which every stage was
triggered by the stage before it.

See `RUNBOOK.md` for how to reproduce it, what still needs credentials, and
what has to be captured by hand.

## Test map

| # | Case | Tier | Where |
|---|---|---|---|
| T1 | Same email, different case | offline + live | `test_normalize.py`, `test_dedupe.py` |
| T2 | Same phone, different format (`010-3456-7890` vs `+821034567890`) | offline + live | same |
| T3 | Similar name on a shared domain → Review, never a merge | offline + live | same |
| T4 | New candidate created and attributed | live | `test_dedupe.py` |
| T5 | Model returns a role outside the allowed four → reject | offline | `test_guard.py` |
| T6 | Quote not found in the application text → reject | offline | `test_guard.py` |
| T7 | Confidence below 0.7 → Review | offline | `test_guard.py` |
| T8 | Approved candidate gets the role-specific template, and the signed envelope carries the chain to Xero | live | `test_dispatch.py` |
| T9 | Completion webhook delivered twice → one Xero contact | live | `test_dispatch.py` |
| T10 | Xero 5xx → retries, writes an alert row, writes nothing to Xero | live | `test_dispatch.py` |
| T11 | Second recruiter re-submits → attribution does not move | live | `test_dedupe.py` |
| T12 | Batch of 20 with 6 planted duplicates | live | `test_batch.py` |
| W1–W9 | Warehouse: migrations, atomic load, refusal, supersede, role isolation, normalization parity, pgaudit | warehouse | `test_warehouse.py` |

## Fixture design

**The roster is self-checking.** `build_fixtures.py` writes every
`expected_*` column by running the real `normalize.js` over the roster in
submission order. If the hand-written expectation and the code ever disagree,
the script exits non-zero instead of writing a golden file that encodes the
wrong answer. Current output:

```
candidates.csv : 20 submissions -> 14 created (1 of them Review), 6 flagged, 0 lost
guard_cases.json : 16 cases
```

**Three of the twenty exist to prove the rules do not over-match.** A dedupe
test suite made only of true duplicates passes trivially if you flag
everything:

| Pair | Name similarity | Same domain | Expected |
|---|---|---|---|
| Sofia / Sophia Lindqvist | 0.875 | yes | Review |
| Nathan / Nathaniel Cole | 0.786 | yes | plain New — below threshold |
| Grace Lim / Grace Lin | 0.889 | **no** | plain New — domain condition is load-bearing |

The third is the one that matters. Without the same-domain requirement, every
Grace Lin on earth collides with every Grace Lim.

**Similarity is normalized Levenshtein, not `difflib`.** The score has to mean
the same thing in the n8n node and in the test assertion. Edit distance is
twenty lines in either language; gestalt pattern matching is not, and a
Python-only metric would mean the node and the tests measure different things.

**The guard fixtures are model outputs, not prompts.** Making a real model
emit `"Manager"` on demand is a coin flip, not a test. The node's input is the
model's output, so that is what the fixture holds — including two cases that
would fool a lazier check: a paraphrase of a real sentence, and a plausible
industry sentence that appears nowhere in the application.

Of the 16 fixture cases, 11 are rejected, 1 goes to Review and 4 are accepted.

## Behaviour worth knowing about

**Gmail dot-aliasing is deliberately not collapsed.** `a.b@gmail.com` and
`ab@gmail.com` are one mailbox at Gmail and two different people almost
everywhere else. Case and whitespace folding is universally safe; aliasing is
not, and a rule that is right for one provider merges real candidates at every
other one. There is a test asserting the non-behaviour so nobody "fixes" it.

**An unparseable phone number normalizes to the empty string,** which never
matches anything. Failing to detect a duplicate is recoverable; merging two
people because a bad number produced a plausible-looking E.164 string is not.

**The quote has an upper bound as well as a lower one.** `MAX_EVIDENCE_CHARS`
(300) and `MAX_EVIDENCE_RATIO` (40% of the source, applied to sources of 100+
chars). Without it the containment check is vacuous — quote the whole
application and it is trivially "in the source". The first live call did
exactly that: two of four sentences, 85% of the text, accepted at 0.98. That
is now `evidence_too_long`, and the fixture has both the whole-document case
and the two-sentence case the model actually produced.

**The assistant turn is prefilled with `{`.** The model cannot open with prose
or a code fence because the reply has to continue a JSON object. The guard
wrapper reattaches the brace. If a fence still shows up at the end it is
stripped as a fallback, and `fence_stripped=true` is written to the Events
row — the one repair the node does perform is never silent.

**Fixture application texts are ~600 chars, not ~200.** The first cut used
two-to-four-sentence texts, and against those the 40% cap rejected three of
four live proposals — Haiku quotes about 20 words no matter what the prompt
says, and 20 words is half of a 200-char application. Raising the cap to fit
the model would have been tuning; the real error was that the fixtures were
shorter than any application a recruiter will ever paste. At ~600 chars the
same 20-word quote is 15–20% and passes, the whole-document quote still
fails, and the cap means what it says.

**A rejected proposal never drops the candidate.** The first live
`evidence_too_long` rejection showed the original wiring did exactly that — the
person vanished because the model over-quoted. Now every verdict reaches
`Create candidate`: accept → `New` with the role; review or reject → `Review`
with the role left empty (or the low-confidence proposal kept, for review) and
the reason in the Events row. There is a structural test asserting no branch
between `Guard` and `Create candidate`.

**The guard checks structure before confidence.** A rejected role at 0.99 is
still rejected; a valid proposal at 0.4 reaches Review rather than being
discarded. Check order is behaviour, and there is a test pinning it.

**Roles are matched exactly — no trimming, no case folding.** `"closer"` is
rejected rather than repaired. Notion's Select property would otherwise grow a
second option next to `Closer`, and the whole point of the node is to notice
that the model improvised.

**T10 needs a seam.** `XERO_FAULT_DOMAIN` / `XERO_FAULT_URL` make WF-3 route
Xero calls at a stub that returns 500. It is env-gated and both variables are
blank in `.env.example`, so it cannot fire in a deployment that has not
explicitly turned it on. T10 skips itself when unset.

**`clean` archives rows before a test, not after.** When an integration test
fails, the rows it produced are the evidence; a teardown that deletes them
destroys the only record of what happened.

## PostgreSQL warehouse layer

The same 20 submissions, loaded into a PostgreSQL model where contact details
are in a schema analysts cannot enter. Built to be the small end of a data
warehouse: numbered migrations, an immutable load manifest, one loader with
one entry point, and a role split that a test proves rather than a README
asserts. `docs/warehouse.md` has the ERD and data dictionary.

```
raw         the file as received, one row per line, tagged with a load_id     loader only
ref         recruiters, roles, phone regions, the normalization functions      everyone reads
core        deduplicated candidates — name, role, domain, recruiter; no email, no phone
restricted  email, phone and the dedupe keys, one row per candidate            loader only
```

```bash
python -m warehouse.migrate                    # applies 001…006 once each
python -m warehouse.load data/candidates.csv   # → 20 rows, 14 candidates, 6 duplicates flagged
python -m warehouse.load data/candidates.csv   # → refused: these exact bytes are already loaded
```

What the loader promises, and the test that checks it:

| | test |
|---|---|
| Every line lands in `raw` verbatim; the manifest records hash, size, row count, status, who, when | W2 |
| The same bytes are never loaded twice; the same source is replaced only with `--supersede`, and the old load stays on disk as `superseded` | W3, W5 |
| One bad line fails the whole load, all bad lines are listed by line number, and no row from a failed load exists afterwards | W4 |
| Dedupe keys are computed by the database (`email_norm` is a generated column, `phone_e164` a trigger), and they agree with `n8n/normalize.js` on every row | W2, W8 |
| Attribution stays with the first submitter when a second recruiter re-sends the same person | W2 |
| The `wh_analyst` role gets `permission denied` on every table, sequence and function in `raw` and `restricted`, can read `core`/`ref`, and cannot write anything | W6 |
| The `wh_loader` role cannot `DELETE`, `TRUNCATE`, `DROP` or edit a stored row anywhere | W7 |

```
$ pytest -m warehouse -v            # Azure Database for PostgreSQL Flexible Server, 2026-09-10
test_w1_migrations_apply_once PASSED
test_w2_load_counts_and_flags_match_fixture PASSED
test_w3_same_bytes_are_refused PASSED
test_w4_bad_lines_fail_atomically_and_itemized PASSED
test_w5_supersede_replaces_current_load_and_keeps_history PASSED
test_w6_analyst_is_denied_everything_in_raw_and_restricted PASSED
test_w7_loader_cannot_delete_or_truncate PASSED
test_w8_sql_normalization_matches_normalize_js PASSED
test_w9_pgaudit_is_loaded PASSED
9 passed in 127.57s
```

That run is against a real Azure server, with three different identities
and no password anywhere:

| | |
|---|---|
| server | Azure Database for PostgreSQL Flexible Server, PostgreSQL 16.15, Burstable B1ms (1 vCore, 2 GiB), 32 GiB, 7-day backups, Korea Central |
| authentication | Microsoft Entra only — password authentication disabled at creation. Admin = the portal user; loader and analyst = two service principals |
| secrets | the two service principals' client secrets live in Key Vault (RBAC). `conn.py` reads them at connect time; no secret is in `.env`, the repo, or the shell history |
| network | TLS required (`ssl = on`), public endpoint with a one-IP firewall rule. No private endpoint — see RUNBOOK for why |
| audit | pgaudit 16.0, `pgaudit.log = write,ddl,role` |
| loader run | `data/candidates.csv` → 20 rows, 14 candidates, 6 duplicates flagged; second run refused; 7 s end to end including token and Key Vault round-trips |
| list price | B1ms $0.026/h ($18.98/month) + 32 GiB × $0.131 ($4.19/month) + backup within the free 32 GiB = **$23.2/month, $0.77/day** at Korea Central pay-as-you-go rates, from the retail-prices API. Billed against the free account's 30-day credit; the resource group is deleted when the exercise ends |

`warehouse/azure/provision.sh` is every `az` command that was run, and
RUNBOOK.md lists the six things that did not work the first time and what
changed. The same test suite passes against a local PostgreSQL 16 (8 passed,
W9 skipped because pgaudit is not installed there).

### Running it locally

```bash
# any PostgreSQL 16; this is the local dev script
warehouse/local_dev.sh              # creates db intake_wh + wh_admin/loader_test/analyst_test
set -a && source .env.warehouse.local && set +a
python -m warehouse.migrate && pytest -m warehouse -v
```

## Known limitation: WF-1 and WF-3 webhooks are unauthenticated

WF-4 is signed (see below). WF-1 and WF-3 are not, and neither is deployed on
the production host. `POST /webhook/intake` accepts anything. In this build that is deliberate — it
keeps the test suite and the demo simple — but it means anyone who learns the
URL can create Notion rows and, worse, spend Anthropic credits: every new
candidate is an LLM call. That is why no tracked file in this repository
contains the live host; the URLs live in `.env`.

Before this pipeline handles real submissions, the webhook needs one of:

- **Header auth** on the Webhook node (n8n supports it natively) with the
  secret in the form/backend that posts to it — the smallest change;
- **HMAC signature** over the body, if the sender can sign — better, because it
  also proves the payload was not altered in transit;
- **an allowlist** at the network edge, if submissions come from one place.

The same applies to `/webhook/docusign-complete`, which additionally should
verify DocuSign Connect's HMAC header rather than trusting any body that
carries a known envelope id. Note what the idempotency check does *not* do:
it stops a replay from creating a second contact, but it does not stop a
forged completion for an envelope that was never signed. Those are different
problems and only one of them is solved here.

## WF-4 in production

```
browser ── form ──▶ Cloudflare Worker ── HMAC-signed ──▶ ngrok static domain ──▶ n8n (Docker, Windows PC)
                     │  serves the page                                          │
                     │  hashes the sender IP                                     ├─ Verify   signature + 5-min replay window
                     │  5-min health check ──▶ Slack on down/up                  ├─ Screen   honeypot, fill time, links, repeat sender
                     └─ /api/status (uptime record)                              ├─ Claude   label proposal (untrusted)
                                                                                 ├─ Guard    guard.js rules, inquiry labels
                                                                                 ├─ Notion   every signed request is a row, spam included
                                                                                 └─ Slack    anything that is not spam
```

**Why a relay.** The form is a static page, so it cannot keep a secret, and an
unauthenticated webhook lets anyone who finds the URL spend Anthropic credit.
The Worker keeps `INQUIRY_SECRET`, signs `${timestamp}.${payload}`, and the
Verify node recomputes it; an unsigned or replayed request gets a 401 and never
reaches the model. `test_relay.py` runs the real Worker module and checks its
output with the real n8n verifier, so the two halves cannot drift apart.

**Why spam is stored, not dropped.** A filtered request that vanishes cannot be
counted. Every signed request becomes an Inquiries row with a `Spam Reason`, so
"N requests, M screened, K real" is a query, not an estimate.

**Why the host is a PC.** It is free and it will go down, which is the point:
the Worker's Cron Trigger checks `/healthz` from outside every 5 minutes, two
misses declare an outage, and Slack hears exactly twice per outage — down, and
back up with the duration. `/api/status` serves checks, failed checks, incidents
and total minutes down.

**First live run (2026-10-24).** Claude labelled a 102-character inquiry
correctly and quoted a 66-character clause; the guard rejected it, because
guard.js caps quotes at 40% of the source from 100 characters on — a rule tuned
for multi-paragraph applications. Inquiries now get the 40% cap only from 250
characters, an 80% cap below that (so quoting the whole message still fails),
and the exact message is a regression test (`test_I3_a_short_inquiry…`).

### Operations log

Filled in weekly from the Events table and `/api/status`. Test traffic is
counted separately and never reported as inquiries.

| Week | Requests (signed) | Rejected unsigned | Spam: screen / model | Real inquiries | Workflow errors | Outages / minutes down | Anthropic cost |
|---|---|---|---|---|---|---|---|
| 2026-10-24 (setup) | 5 test | 1 test probe | 0 / 0 | 0 | 0 | monitoring started | < $0.01 |

## Notion schema notes

Attribution is two properties, on purpose. `Submitted By` is a Notion
*Created by* property — system-set, and nothing can type over it. `Recruiter`
is a relation into the Recruiters database, written by WF-1 from the payload,
so rollups and grouped views work.

If a single n8n integration token creates every row, `Submitted By` reads as
that integration for all of them. For a per-recruiter demo, run WF-1 with a
per-recruiter token; otherwise show `Recruiter` and keep `Submitted By` as the
tamper-proof audit trail. T11 asserts on both.
