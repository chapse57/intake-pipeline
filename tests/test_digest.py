"""
Offline tier — WF-5, the daily spam digest.

Spam never reaches Slack, so a real client screened by mistake is silent.
Once a day WF-5 lists what the screen kept out. What can go wrong is quiet:
a skipped morning, a row that falls between two windows, a failed post that
still moves the window on. Each is tested against n8n/digest.js and the
exported WF-5 nodes.

  D1  window     — where a digest starts and ends
  D2  summary    — the Slack text
  D3  the exported WF-5 nodes, executed with the n8n globals stubbed
"""
import datetime
import json
import pathlib
import subprocess

import pytest

pytestmark = [pytest.mark.offline, pytest.mark.digest]

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIGEST_JS = ROOT / "n8n" / "digest.js"
WF5 = ROOT / "n8n" / "workflows" / "WF-5_spam_digest.json"

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
NOW = 1_800_000_045_000            # 45 s past a whole minute


# encoding: the digest text has "·", "•" and "…", and Python on a Korean
# Windows would otherwise decode node's UTF-8 output as cp949.
def run(reqs):
    proc = subprocess.run(["node", str(DIGEST_JS)], input=json.dumps(reqs), capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return {r["id"]: r for r in json.loads(proc.stdout)}


def window(last, now=NOW):
    return run([{"id": "w", "op": "window", "last": last, "now": now}])["w"]


def row(name, reason, url="https://www.notion.so/p1"):
    return {"url": url, "properties": {
        "Name": {"title": [{"plain_text": name}]},
        "Spam Reason": {"rich_text": [{"plain_text": reason}]}}}


def summarize(rows, win=None, has_more=False):
    win = win or {"since": NOW - DAY, "until": NOW}
    return run([{"id": "s", "op": "summarize", "rows": rows, "win": win, "has_more": has_more}])["s"]


# --------------------------------------------------------------------------
# D1 window
# --------------------------------------------------------------------------
def test_D1_the_window_ends_on_a_whole_minute():
    """Notion keeps created_time to the minute: a row made at :50 reads :00.
    A window ending at :45 would put it after this digest and before the next."""
    w = window(None)
    assert w["until"] == NOW - 45_000 and w["until"] % MIN == 0


def test_D1_first_run_covers_the_last_24_hours():
    w = window(None)
    assert w["until"] - w["since"] == DAY


def test_D1_a_skipped_morning_is_covered_by_the_next_digest():
    last = NOW - 45_000 - 2 * DAY          # PC was off yesterday at 09:00
    w = window(last)
    assert w["since"] == last, "starts where the last sent digest ended, not 24 h back"


def test_D1_it_never_reaches_back_more_than_a_week():
    w = window(NOW - 30 * DAY)
    assert w["until"] - w["since"] == 7 * DAY


def test_D1_consecutive_windows_meet_exactly():
    first = window(None, NOW)
    second = window(first["until"], NOW + DAY)
    assert second["since"] == first["until"], "no gap and no overlap between digests"


# --------------------------------------------------------------------------
# D2 summary
# --------------------------------------------------------------------------
def test_D2_rows_are_tallied_by_reason_and_listed_with_links():
    s = summarize([row("Bot A", "honeypot"), row("Bot B", "too_fast (1200)", "https://www.notion.so/p2"),
                   row("Bot C", "too_fast (800)"), row("Sam", "model: spam")])
    lines = s["text"].splitlines()
    assert s["count"] == 4
    assert lines[0].startswith(":wastebasket: Spam digest - 4 screened since ")
    assert lines[1] == "too_fast 2 · honeypot 1 · model: spam 1", "detail stripped, most common first"
    assert "• Bot B - too_fast (1200) - <https://www.notion.so/p2|open>" in lines


def test_D2_a_quiet_day_still_says_so():
    """Silence must mean the digest did not run, not that there was nothing."""
    s = summarize([])
    assert s["count"] == 0 and "nothing screened" in s["text"]


def test_D2_a_long_list_is_cut_and_says_how_many_more():
    s = summarize([row(f"Bot {i}", "honeypot") for i in range(20)])
    lines = s["text"].splitlines()
    assert sum(1 for l in lines if l.startswith("• ")) == 15
    assert lines[-1].startswith("… and 5 more")


def test_D2_more_than_one_notion_page_is_marked():
    s = summarize([row("Bot", "honeypot")], has_more=True)
    assert "1+ screened" in s["text"] and s["has_more"] is True


def test_D2_times_are_shown_in_seoul_time():
    since = int(datetime.datetime(2026, 10, 27, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    s = summarize([], win={"since": since, "until": since + DAY})
    assert "since 10-27 09:00 KST" in s["text"]


# --------------------------------------------------------------------------
# D3 exported WF-5 nodes
# --------------------------------------------------------------------------
# Node prints ASCII only (non-ASCII as \\uXXXX), so how Python decodes the
# pipe (cp949 on a Korean Windows) cannot matter.
ASCII_OUT = r"""const __ascii = (s) => s.replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));"""

STUBS = ASCII_OUT + """
const __nodes = globalThis.__nodes || {};
const __static = globalThis.__static || {};
const $ = (name) => ({ first: () => {
  if (!(name in __nodes)) throw new Error('stub: no data for ' + name);
  return { json: __nodes[name] }; } });
const $json = globalThis.__json || {};
const $getWorkflowStaticData = () => __static;
"""


@pytest.fixture(scope="module")
def wf5():
    with open(WF5, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def nodes(wf5):
    return {n["name"]: n for n in wf5["nodes"]}


def _exec(js, nodes=None, static=None, json_=None):
    program = (f"globalThis.__nodes = {json.dumps(nodes or {})};\n"
               f"globalThis.__static = {json.dumps(static or {})};\n"
               f"globalThis.__json = {json.dumps(json_ or {})};\n" + STUBS
               + "\nconst __r = (function(){\n" + js + "\n})();\n"
               "process.stdout.write(__ascii(JSON.stringify({ out: __r[0].json, static: __static })));")
    proc = subprocess.run(["node", "-e", program], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_D3_runs_at_nine_in_seoul(wf5, nodes):
    rule = nodes["Every day 09:00"]["parameters"]["rule"]["interval"][0]
    assert rule == {"field": "cronExpression", "expression": "0 9 * * *"}
    assert wf5["settings"]["timezone"] == "Asia/Seoul", "otherwise 09:00 is the server's zone"


def test_D3_query_selects_spam_rows_created_inside_the_window(nodes):
    body = nodes["Spam since last digest"]["parameters"]["jsonBody"]
    win = {"since": 1_800_000_000_000 - DAY, "until": 1_800_000_000_000 - 0}
    program = (f"globalThis.__nodes = {json.dumps({'Window': win, 'Config': {}})};\n" + STUBS
               + f"\nprocess.stdout.write(__ascii({body[3:-2]}));")
    proc = subprocess.run(["node", "-e", program], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    q = json.loads(proc.stdout)
    status, after, before = q["filter"]["and"]
    assert status == {"property": "Status", "select": {"equals": "Spam"}}
    iso = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert after == {"timestamp": "created_time", "created_time": {"on_or_after": iso(win["since"])}}
    assert before == {"timestamp": "created_time", "created_time": {"before": iso(win["until"])}}, \
        "created time, not Received: a held inquiry is created when it is delivered"


def test_D3_window_node_reads_the_last_sent_digest(nodes):
    import time
    last = int(time.time() * 1000) // MIN * MIN - 2 * DAY      # the node runs on the real clock
    r = _exec(nodes["Window"]["parameters"]["jsCode"], static={"last_digest_at": last})
    assert r["out"]["since"] == last


def test_D3_the_window_moves_on_only_when_slack_took_the_message(nodes):
    js = nodes["Mark sent"]["parameters"]["jsCode"]
    win = {"since": 1, "until": 1_800_000_000_000}
    ok = _exec(js, nodes={"Window": win}, static={"last_digest_at": 1}, json_={"data": "ok"})
    assert ok["out"]["sent"] is True and ok["static"]["last_digest_at"] == win["until"]
    failed = _exec(js, nodes={"Window": win}, static={"last_digest_at": 1},
                   json_={"error": {"message": "invalid_payload"}})
    assert failed["out"]["sent"] is False and failed["static"]["last_digest_at"] == 1, \
        "a failed post repeats these rows tomorrow instead of losing them"


def test_D3_summarize_node_hands_the_notion_rows_to_digest_js(nodes):
    r = _exec(nodes["Summarize"]["parameters"]["jsCode"], nodes={
        "Spam since last digest": {"results": [row("Bot", "honeypot")], "has_more": False},
        "Window": {"since": NOW - DAY, "until": NOW}})
    assert r["out"]["count"] == 1 and "honeypot 1" in r["out"]["text"]


def test_D3_code_nodes_carry_the_current_digest_js(nodes):
    src = DIGEST_JS.read_text(encoding="utf-8")
    head = src[:src.index("module.exports")]
    for marker in ("function digestWindow(", "function summarizeSpam("):
        fn = head[head.index(marker):]
        fn = fn[:fn.index("\n}\n") + 3]
        for name in ("Window", "Summarize"):
            assert fn in nodes[name]["parameters"]["jsCode"], \
                f"{name} is out of date with digest.js; rerun n8n/build_workflows.py"
