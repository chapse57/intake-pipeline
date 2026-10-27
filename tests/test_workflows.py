"""
Offline tier — the exported n8n workflow files.

n8n will happily import a workflow whose expressions are broken and tell you
about it at runtime, one node per execution. These checks run the same
JavaScript n8n would, with the n8n globals stubbed, so a typo in a Code node
or a `{{ }}` expression fails here rather than in a demo.
"""
import json
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF_DIR = ROOT / "n8n" / "workflows"
WORKFLOWS = sorted(WF_DIR.glob("*.json"))

pytestmark = pytest.mark.offline

# What the Notion Events schema actually offers. Kept in the test rather than
# read live so this stays offline; update both if the schema changes.
EVENT_STEPS = {"normalize", "dedupe", "llm-role", "guard", "notion-create",
               "dispatch-docusign", "docusign-webhook", "idempotency", "xero-contact", "error",
               "auth", "spam-screen", "llm-label", "notify", "digest"}
EVENT_RESULTS = {"ok", "rejected", "error", "skipped"}
EVENT_WORKFLOWS = {"WF-1 Intake", "WF-2 Dispatch", "WF-3 Complete", "Error Handler", "WF-4 Inquiry",
                   "WF-5 Spam Digest"}
CANDIDATE_STATUSES = {"New", "Review", "Approved", "Sent", "Onboarded", "Rejected"}

TRIGGERS = {"n8n-nodes-base.webhook", "n8n-nodes-base.errorTrigger",
            "n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.manualTrigger"}

# Stubs for the n8n runtime globals so the code can be parsed and, for the
# Code nodes, executed against canned inputs.
N8N_STUBS = """
const __nodes = globalThis.__nodes || {};
const $ = (name) => {
  const v = __nodes[name];
  if (v === undefined) throw new Error('stub: no data for node ' + JSON.stringify(name));
  return { first: () => ({ json: v }), item: { json: v }, all: () => [{ json: v }] };
};
const $json = globalThis.__json || {};
const $input = { all: () => [{ json: $json }], first: () => ({ json: $json }) };
const $runIndex = globalThis.__runIndex || 0;
const $execution = { id: 'exec-test' };
const $now = { toISO: () => new Date().toISOString() };
"""


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _expressions(obj, path=""):
    """Yield (json_path, inner_js) for every '={{ ... }}' string parameter."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _expressions(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _expressions(v, f"{path}[{i}]")
    elif isinstance(obj, str) and obj.startswith("={{") and obj.rstrip().endswith("}}"):
        inner = obj[3:].rstrip()[:-2]
        yield path, inner


def _node_ok(js, stubs=N8N_STUBS, nodes=None, json_=None, run_index=0, execute=False):
    """Run `js` under node with the n8n globals stubbed. Returns stdout."""
    prelude = (f"globalThis.__nodes = {json.dumps(nodes or {})};\n"
               f"globalThis.__json = {json.dumps(json_ or {})};\n"
               f"globalThis.__runIndex = {run_index};\n")
    if execute:
        program = prelude + stubs + "\nconst __r = (function(){\n" + js + "\n})();\n" \
                  "process.stdout.write(JSON.stringify(__r));"
    else:
        program = prelude + stubs + "\n(function(){\n" + js + "\n});"
    proc = subprocess.run(["node", "-e", program], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip().splitlines()[-1] if proc.stderr else "node failed")
    return proc.stdout


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_every_connection_points_at_a_real_node(path):
    wf = _load(path)
    names = {n["name"] for n in wf["nodes"]}
    for src, outs in wf["connections"].items():
        assert src in names, f"connection from unknown node {src!r}"
        for branch in outs["main"]:
            for c in branch:
                assert c["node"] in names, f"{src} -> unknown node {c['node']!r}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_no_node_is_orphaned(path):
    wf = _load(path)
    incoming = {c["node"] for outs in wf["connections"].values()
                for branch in outs["main"] for c in branch}
    for n in wf["nodes"]:
        if n["type"] in TRIGGERS:
            assert n["name"] in wf["connections"], f"trigger {n['name']} leads nowhere"
        else:
            assert n["name"] in incoming, f"{n['name']} is never reached"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_switch_and_if_outputs_are_all_wired(path):
    wf = _load(path)
    for n in wf["nodes"]:
        outs = wf["connections"].get(n["name"], {"main": []})["main"]
        if n["type"] == "n8n-nodes-base.if":
            assert len(outs) == 2 and all(outs), f"{n['name']}: both IF outputs must be wired"
        if n["type"] == "n8n-nodes-base.switch":
            rules = n["parameters"]["rules"]["values"]
            expected = len(rules) + (1 if n["parameters"]["options"].get("fallbackOutput") == "extra" else 0)
            assert len(outs) == expected and all(outs), \
                f"{n['name']}: {expected} outputs expected, {len(outs)} wired"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_code_nodes_parse(path):
    wf = _load(path)
    for n in wf["nodes"]:
        if n["type"] != "n8n-nodes-base.code":
            continue
        try:
            _node_ok(n["parameters"]["jsCode"])
        except AssertionError as e:
            pytest.fail(f"{path.stem} / {n['name']}: {e}")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_expressions_parse(path):
    wf = _load(path)
    bad = []
    for n in wf["nodes"]:
        for jpath, inner in _expressions(n["parameters"]):
            try:
                _node_ok("return (\n" + inner + "\n);")
            except AssertionError as e:
                bad.append(f"{n['name']}{jpath}: {e}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_event_rows_only_use_values_the_schema_has(path):
    wf = _load(path)
    for n in wf["nodes"]:
        body = n["parameters"].get("jsonBody", "")
        if "notion_events_db" not in body:
            continue
        m_wf = re.search(r"Workflow: \{ select: \{ name: '([^']+)'", body)
        m_step = re.search(r"Step: \{ select: \{ name: '([^']+)'", body)
        assert m_wf and m_wf.group(1) in EVENT_WORKFLOWS, f"{n['name']}: workflow {m_wf}"
        assert m_step and m_step.group(1) in EVENT_STEPS, f"{n['name']}: step {m_step}"
        for lit in re.findall(r"Result: \{ select: \{ name: (.+?) \} \}", body):
            lit = re.sub(r"===\s*'[^']*'", "", lit)   # drop comparison operands
            for val in re.findall(r"'([a-z]+)'", lit):
                assert val in EVENT_RESULTS, f"{n['name']}: result {val!r}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_candidate_status_writes_are_valid(path):
    wf = _load(path)
    text = json.dumps(wf)
    for val in re.findall(r"Status: \{ select: \{ name: '([^']+)' \} \}", text):
        assert val in CANDIDATE_STATUSES, val


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_logging_nodes_never_stop_the_pipeline(path):
    wf = _load(path)
    for n in wf["nodes"]:
        if "notion_events_db" in n["parameters"].get("jsonBody", ""):
            assert n.get("onError") == "continueRegularOutput", \
                f"{n['name']}: a failed log write must not fail the run"


def test_code_nodes_carry_the_current_js_files():
    """The generator embeds normalize.js / guard.js. Stale export = stale node."""
    wf = _load(WF_DIR / "WF-1_intake.json")
    by_name = {n["name"]: n for n in wf["nodes"]}
    for node_name, src_file, marker in [
        ("Normalize", "normalize.js", "function classifyDuplicate("),
        ("Classify", "normalize.js", "function classifyDuplicate("),
        ("Guard", "guard.js", "function guard("),
    ]:
        src = (ROOT / "n8n" / src_file).read_text(encoding="utf-8")
        head = src[:src.index("module.exports")]
        fn = head[head.index(marker):]
        fn = fn[:fn.index("\n}\n") + 3]
        assert fn in by_name[node_name]["parameters"]["jsCode"], \
            f"{node_name} node is out of date with {src_file}; rerun n8n/build_workflows.py"


# --------------------------------------------------------------------------
# functional: execute the WF-1 Code nodes against canned Notion responses
# --------------------------------------------------------------------------
def _notion_page(pid, name, email_norm, domain, phone, attempts=0):
    rt = lambda s: {"rich_text": [{"plain_text": s}]}
    return {"id": pid, "properties": {
        "Name": {"title": [{"plain_text": name}]},
        "Email Norm": rt(email_norm), "Email Domain": rt(domain),
        "Phone E164": rt(phone), "Duplicate Attempts": {"number": attempts}}}


@pytest.fixture(scope="module")
def wf1_nodes():
    wf = _load(WF_DIR / "WF-1_intake.json")
    return {n["name"]: n for n in wf["nodes"]}


def _run_normalize(wf1_nodes, body):
    out = _node_ok(wf1_nodes["Normalize"]["parameters"]["jsCode"],
                   nodes={"Webhook": {"body": body}}, execute=True)
    return json.loads(out)[0]["json"]


def _run_classify(wf1_nodes, normalized, by_email=(), by_phone=(), by_domain=()):
    out = _node_ok(wf1_nodes["Classify"]["parameters"]["jsCode"], execute=True, nodes={
        "Normalize": normalized,
        "Find by email": {"results": list(by_email)},
        "Find by phone": {"results": list(by_phone)},
        "Find by domain": {"results": list(by_domain)}})
    return json.loads(out)[0]["json"]


@pytest.mark.T1
def test_wf1_classify_node_flags_an_email_duplicate(wf1_nodes):
    body = {"name": "Marcus Webb", "email": "  MARCUS.WEBB@NORTHLINE.IO ", "phone": "+1 415 555 0142",
            "country_hint": "US", "application_text": "x", "recruiter_email": "ben@intake.demo"}
    n = _run_normalize(wf1_nodes, body)
    assert n["email_norm"] == "marcus.webb@northline.io"
    existing = _notion_page("p1", "Marcus Webb", "marcus.webb@northline.io", "northline.io",
                            "+14155550142", attempts=2)
    c = _run_classify(wf1_nodes, n, by_email=[existing], by_phone=[existing], by_domain=[existing])
    assert c["verdict"] == "exact" and c["match_id"] == "p1" and c["match_on"] == "email"
    assert c["match_attempts"] == 2, "the bump node needs the current count"
    assert c["existing_count"] == 1, "the same page from three lookups is one row, not three"


@pytest.mark.T3
def test_wf1_classify_node_routes_a_near_name_to_review(wf1_nodes):
    body = {"name": "Sophia Lindqvist", "email": "sophia.lindqvist@harborlight.se",
            "phone": "+46 8 987 654 32", "country_hint": "SE", "application_text": "x",
            "recruiter_email": "ana@intake.demo"}
    n = _run_normalize(wf1_nodes, body)
    sofia = _notion_page("p4", "Sofia Lindqvist", "sofia.l@harborlight.se", "harborlight.se",
                         "+46812345678")
    c = _run_classify(wf1_nodes, n, by_domain=[sofia])
    assert c["verdict"] == "fuzzy" and c["match_id"] == "p4"
    assert c["match_score"] == pytest.approx(0.875, abs=0.001)


@pytest.mark.T5
@pytest.mark.T6
def test_wf1_guard_node_strips_fences_but_not_claims(wf1_nodes):
    src = "I run the closing call and take payment on the call itself."
    classify = {"name": "P", "application_text": src, "recruiter_email": "a@b.c", "verdict": "new"}

    def run(text):
        out = _node_ok(wf1_nodes["Guard"]["parameters"]["jsCode"], execute=True, nodes={
            "Classify": classify,
            "Claude: propose role": {"content": [{"type": "text", "text": text}]}})
        return json.loads(out)[0]["json"]["guard"]

    # the request prefills the assistant turn with '{', so the model's text is the remainder
    cont = '"role":"Closer","evidence_quote":"I run the closing call","confidence":0.9}'
    assert run(cont)["verdict"] == "accept"
    assert run("{" + cont)["verdict"] == "accept", "a model that repeats the brace must not break parsing"
    trailing_fence = cont + "\n```"
    assert run(trailing_fence)["verdict"] == "accept", "a trailing fence is transport, not content"
    assert run('"role":"Manager","evidence_quote":"I run the closing call","confidence":0.9}')["reason"] == "role_not_allowed"
    assert run('"role":"Closer","evidence_quote":"I close deals","confidence":0.9}')["reason"] == "evidence_not_in_source"
    assert run("Here is my answer: Closer")["reason"] == "not_json"


def test_wf1_guard_node_records_whether_it_stripped_a_fence(wf1_nodes):
    src = "I run the closing call and take payment on the call itself."
    classify = {"name": "P", "application_text": src, "recruiter_email": "a@b.c", "verdict": "new"}
    cont = '"role":"Closer","evidence_quote":"I run the closing call","confidence":0.9}'

    def run(text):
        out = _node_ok(wf1_nodes["Guard"]["parameters"]["jsCode"], execute=True, nodes={
            "Classify": classify,
            "Claude: propose role": {"content": [{"type": "text", "text": text}]}})
        return json.loads(out)[0]["json"]

    assert run(cont)["llm_fence_stripped"] is False
    assert run(cont + "\n```")["llm_fence_stripped"] is True
    wf = _load(WF_DIR / "WF-1_intake.json")
    log_guard = {n["name"]: n for n in wf["nodes"]}["Log: guard"]
    assert "fence_stripped" in log_guard["parameters"]["jsonBody"], \
        "the repair must be visible in the Events row, not just in node output"


def test_wf1_claude_request_prefills_the_assistant_turn(wf1_nodes):
    body = wf1_nodes["Claude: propose role"]["parameters"]["jsonBody"]
    assert "role: 'assistant', content: '{'" in body


def test_wf1_build_candidate_node_sets_review_for_fuzzy_and_low_confidence(wf1_nodes):
    js = wf1_nodes["Build candidate"]["parameters"]["jsCode"]
    base = {"name": "X", "email_norm": "x@y.z", "phone_raw": "1", "phone_e164": "+1", "email_domain": "y.z",
            "application_text": "t", "recruiter_email": "r@y.z", "match_id": "pM",
            "guard": {"verdict": "accept", "role": "Setter", "evidence": "e", "confidence": 0.9}}
    cfg = {"notion_candidates_db": "DB"}

    def run(**over):
        g = json.loads(json.dumps(base)); g.update(over)
        out = _node_ok(js, execute=True, nodes={"Guard": g, "Config": cfg,
                                                "Find recruiter": {"results": [{"id": "rec1"}]}})
        return json.loads(out)[0]["json"]

    new = run(verdict="new")
    assert new["status"] == "New"
    assert new["payload"]["properties"]["Recruiter"]["relation"] == [{"id": "rec1"}]
    assert new["payload"]["properties"]["Duplicate Of"]["relation"] == []

    fuzzy = run(verdict="fuzzy")
    assert fuzzy["status"] == "Review"
    assert fuzzy["payload"]["properties"]["Duplicate Of"]["relation"] == [{"id": "pM"}]

    low = run(verdict="new", guard={"verdict": "review", "role": "Setter", "evidence": "e", "confidence": 0.5})
    assert low["status"] == "Review"
    assert low["payload"]["properties"]["Role"] == {"select": {"name": "Setter"}}, \
        "a Review row keeps the proposal so the reviewer sees it"

    rejected = run(verdict="new", guard={"verdict": "reject", "role": None, "evidence": None,
                                         "confidence": None, "reason": "evidence_too_long"})
    assert rejected["status"] == "Review", "a rejected label must not drop the person"
    assert rejected["payload"]["properties"]["Role"] == {"select": None}
    assert rejected["payload"]["properties"]["Name"]["title"][0]["text"]["content"] == "X"


def test_wf1_has_no_branch_that_drops_a_candidate_after_the_guard():
    """
    Every path out of the Guard must reach Create candidate. The guard rejects
    labels; nothing after it is allowed to reject people.
    """
    wf = _load(WF_DIR / "WF-1_intake.json")
    conns = wf["connections"]
    node, seen = "Guard", set()
    while node != "Create candidate":
        outs = [c["node"] for branch in conns[node]["main"] for c in branch]
        assert len(outs) == 1, f"{node} branches: {outs}"
        assert node not in seen
        seen.add(node)
        node = outs[0]


def test_wf3_parse_event_accepts_both_docusign_shapes():
    wf = _load(WF_DIR / "WF-3_complete.json")
    js = {n["name"]: n for n in wf["nodes"]}["Parse event"]["parameters"]["jsCode"]

    def run(body):
        return json.loads(_node_ok(js, execute=True, nodes={"Webhook": {"body": body}}))[0]["json"]

    sim = run({"event": "envelope-completed", "data": {"envelopeId": "E1",
                                                        "envelopeSummary": {"status": "completed"}}})
    assert (sim["envelope_id"], sim["status"]) == ("E1", "completed")
    flat = run({"envelopeId": "E2", "status": "Completed"})
    assert (flat["envelope_id"], flat["status"]) == ("E2", "completed")
    sent = run({"event": "envelope-sent", "data": {"envelopeId": "E3",
                                                    "envelopeSummary": {"status": "sent"}}})
    assert sent["status"] == "sent"


@pytest.mark.T10
def test_wf3_attempt_counter_and_fault_seam():
    wf = _load(WF_DIR / "WF-3_complete.json")
    nodes = {n["name"]: n for n in wf["nodes"]}

    count = nodes["Count attempt"]["parameters"]["jsCode"]
    for run_index in range(3):
        r = json.loads(_node_ok(count, execute=True, run_index=run_index,
                                json_={"error": {"message": "502 Bad Gateway"}}))[0]["json"]
        assert r["attempt"] == run_index + 1
        assert "502" in r["error"]

    prep = nodes["Prepare Xero request"]["parameters"]["jsCode"]
    page = {"id": "c1", "properties": {"Name": {"title": [{"plain_text": "Aisha Bello"}]},
                                       "Email": {"email": "aisha@fault.test"},
                                       "Email Domain": {"rich_text": [{"plain_text": "fault.test"}]}}}
    def run(cfg):
        return json.loads(_node_ok(prep, execute=True, nodes={
            "Config": cfg, "Find candidate": {"results": [page]}}))[0]["json"]

    off = run({"xero_fault_domain": "", "xero_fault_url": ""})
    assert off["fault_injected"] is False and "api.xero.com" in off["url"]
    half = run({"xero_fault_domain": "fault.test", "xero_fault_url": ""})
    assert half["fault_injected"] is False, "one blank value must keep the seam closed"
    on = run({"xero_fault_domain": "fault.test", "xero_fault_url": "http://stub:9/500"})
    assert on["fault_injected"] is True and on["url"] == "http://stub:9/500"
    assert on["body"]["Contacts"][0]["IsSupplier"] is True
