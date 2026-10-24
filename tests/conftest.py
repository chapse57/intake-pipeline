"""
Shared fixtures.

Two tiers of test live side by side:

  * offline  — drive n8n/normalize.js and n8n/guard.js through `node`.
               No credentials, no network, runs in CI. These are the tests
               that pin the rules themselves.
  * integration — post real webhooks at a running n8n and read the result
               back out of Notion. Skipped automatically when the
               environment is not configured.

The offline tier calls the same JavaScript the n8n nodes run, rather than a
Python restatement of it. A Python copy would pass forever while the node
drifted.
"""
import json
import os
import pathlib
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NORMALIZE_JS = ROOT / "n8n" / "normalize.js"
GUARD_JS = ROOT / "n8n" / "guard.js"
INQUIRY_JS = ROOT / "n8n" / "inquiry.js"

NOTION_VERSION = "2022-06-28"

REQUIRED_INTEGRATION_ENV = [
    "N8N_INTAKE_WEBHOOK_URL",
    "NOTION_TOKEN",
    "NOTION_CANDIDATES_DB",
    "NOTION_EVENTS_DB",
]


# --------------------------------------------------------------------------
# offline: run the real node code
# --------------------------------------------------------------------------
def _run_node(script: pathlib.Path, payload) -> object:
    proc = subprocess.run(
        ["node", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"{script.name} exited {proc.returncode}:\n{proc.stderr}")
    return json.loads(proc.stdout)


@pytest.fixture(scope="session")
def normalize():
    """normalize_submission(dict) -> dict of stored dedupe keys."""
    def _call(incoming):
        return _run_node(NORMALIZE_JS, {"op": "normalize", "incoming": incoming})
    return _call


@pytest.fixture(scope="session")
def classify():
    """classify(incoming, existing_rows) -> {verdict, matchId, on, reason, score}."""
    def _call(incoming, existing):
        return _run_node(NORMALIZE_JS, {"op": "classify", "incoming": incoming,
                                        "existing": existing})
    return _call


@pytest.fixture(scope="session")
def run_guard():
    """run_guard([case, ...]) -> [result, ...] keyed by the case's id."""
    def _call(cases):
        return _run_node(GUARD_JS, cases)
    return _call


@pytest.fixture(scope="session")
def run_inquiry():
    """run_inquiry([{"id":..,"op":..}, ...]) -> {id: result}. Runs n8n/inquiry.js."""
    def _call(reqs):
        return {r["id"]: r for r in _run_node(INQUIRY_JS, reqs)}
    return _call


@pytest.fixture(scope="session")
def guard_cases():
    with open(ROOT / "data" / "guard_cases.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def roster():
    """data/candidates.csv as a list of dicts, in submission order."""
    import csv
    with open(ROOT / "data" / "candidates.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------
# integration: live n8n + Notion
# --------------------------------------------------------------------------
def _missing_env():
    return [k for k in REQUIRED_INTEGRATION_ENV if not os.environ.get(k)]


@pytest.fixture(scope="session")
def live_env():
    missing = _missing_env()
    if missing:
        pytest.skip("integration env not set: " + ", ".join(missing))
    return {k: os.environ[k] for k in os.environ}


class Notion:
    """The three calls this suite needs. Not a general-purpose client."""

    def __init__(self, token):
        import requests
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })

    def query(self, database_id, filter_=None, page_size=100):
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        body, out, cursor = {"page_size": page_size}, [], None
        if filter_:
            body["filter"] = filter_
        while True:
            if cursor:
                body["start_cursor"] = cursor
            r = self.s.post(url, json=body, timeout=30)
            r.raise_for_status()
            data = r.json()
            out.extend(data["results"])
            if not data.get("has_more"):
                return out
            cursor = data["next_cursor"]

    def update(self, page_id, properties):
        r = self.s.patch(f"https://api.notion.com/v1/pages/{page_id}",
                         json={"properties": properties}, timeout=30)
        r.raise_for_status()
        return r.json()

    def archive(self, page_id):
        r = self.s.patch(f"https://api.notion.com/v1/pages/{page_id}",
                         json={"archived": True}, timeout=30)
        r.raise_for_status()

    def empty(self, database_id):
        for page in self.query(database_id):
            self.archive(page["id"])


def prop(page, name):
    """Read a Notion property down to a plain Python value."""
    p = page["properties"].get(name)
    if p is None:
        return None
    t = p["type"]
    v = p[t]
    if t in ("title", "rich_text"):
        return "".join(x["plain_text"] for x in v)
    if t in ("select", "status"):
        return v["name"] if v else None
    if t in ("number", "email", "phone_number", "checkbox", "url"):
        return v
    if t == "relation":
        return [x["id"] for x in v]
    if t in ("people", "created_by"):
        if isinstance(v, list):
            return [x.get("id") for x in v]
        return v.get("id")
    if t == "rollup":
        return v.get("number")
    return v


@pytest.fixture(scope="session")
def notion(live_env):
    return Notion(os.environ["NOTION_TOKEN"])


@pytest.fixture(scope="session")
def db(live_env):
    return {
        "candidates": os.environ["NOTION_CANDIDATES_DB"],
        "events": os.environ["NOTION_EVENTS_DB"],
        "recruiters": os.environ.get("NOTION_RECRUITERS_DB"),
    }


@pytest.fixture
def clean(notion, db):
    """
    Archive every Candidate and Event before the test.

    Deliberately before rather than after: when a test fails, the rows it
    produced are the evidence, and a teardown that deletes them destroys the
    only record of what actually happened.
    """
    notion.empty(db["candidates"])
    notion.empty(db["events"])
    return None


@pytest.fixture
def submit(live_env):
    """POST one submission at the WF-1 intake webhook."""
    import requests
    url = os.environ["N8N_INTAKE_WEBHOOK_URL"]

    def _submit(row):
        payload = {
            "submission_id": row.get("submission_id"),
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "country_hint": row.get("country_hint", "US"),
            "application_text": row["application_text"],
            "recruiter_email": row["recruiter_email"],
        }
        r = requests.post(url, json=payload, timeout=60)
        r.raise_for_status()
        return r
    return _submit


@pytest.fixture
def docusign_webhook(live_env):
    """POST a DocuSign Connect 'envelope completed' notification at WF-3."""
    import requests
    url = os.environ.get("N8N_DOCUSIGN_WEBHOOK_URL")
    if not url:
        pytest.skip("N8N_DOCUSIGN_WEBHOOK_URL not set")

    def _post(envelope_id, status="completed"):
        r = requests.post(url, json={"envelopeId": envelope_id, "status": status},
                          timeout=60)
        r.raise_for_status()
        return r
    return _post


@pytest.fixture
def eventually():
    """
    Poll until `fn()` returns something truthy, or fail with what it last saw.

    Every integration assertion goes through this. The pipeline is
    asynchronous, and a bare assert immediately after the POST tests the
    speed of n8n rather than the behaviour of the workflow.
    """
    def _wait(fn, timeout=45.0, interval=1.0, what="condition"):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = fn()
            if last:
                return last
            time.sleep(interval)
        raise AssertionError(f"timed out after {timeout}s waiting for {what}; "
                             f"last observed: {last!r}")
    return _wait
