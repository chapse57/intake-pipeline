"""
Offline tier — the Cloudflare relay (worker/inquiry-relay.js).

The relay and WF-4 are two halves of one handshake written in two runtimes
(WebCrypto in the Worker, node:crypto in n8n). These tests run the real
Worker module under node, capture what it would forward, and check it with
the real n8n/inquiry.js verifier. If either side drifts, this fails.
"""
import json
import os
import pathlib
import subprocess

import pytest

pytestmark = [pytest.mark.offline, pytest.mark.inquiry]

ROOT = pathlib.Path(__file__).resolve().parents[1]

HARNESS = r"""
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
// the Worker's console.log is its log stream; keep it off stdout
console.log = (...a) => process.stderr.write(a.join(' ') + '\n');
const relay = (await import(process.env.WORKER)).default;
const inquiry = require(process.env.INQUIRY);
const env = { INQUIRY_SECRET: 's3cret-test', N8N_WEBHOOK_URL: 'https://n8n.test/webhook/inquiry' };
const cases = JSON.parse(process.env.CASES);
const out = [];
for (const c of cases) {
  let forwarded = null;
  const upstream = async (url, init) => {
    forwarded = { url, headers: init.headers, body: init.body };
    return new Response('{"ok":true}', { status: c.upstream_status || 202 });
  };
  const req = new Request('https://ether-inquiry.example.workers.dev' + (c.path || '/api/inquiry'), {
    method: c.method || 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json', 'CF-Connecting-IP': '203.0.113.9' },
                           c.origin === null ? {} : { Origin: c.origin || 'https://ether-inquiry.example.workers.dev' }),
    body: (c.method || 'POST') === 'POST' ? JSON.stringify(c.form || {}) : undefined,
  });
  const res = await relay.fetch(req, c.env === 'empty' ? {} : env, {}, upstream);
  const r = { id: c.id, status: res.status, forwarded: !!forwarded };
  if (forwarded) {
    const payload = JSON.parse(forwarded.body).payload;
    r.verify = inquiry.verifySignature(env.INQUIRY_SECRET, forwarded.headers['X-Ether-Timestamp'],
                                       payload, forwarded.headers['X-Ether-Signature'], Date.now());
    r.payload = JSON.parse(payload);
  }
  out.push(r);
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run():
    def _run(cases):
        env = dict(os.environ, WORKER=(ROOT / "worker" / "inquiry-relay.js").as_uri(),
                   INQUIRY=str(ROOT / "n8n" / "inquiry.js"), CASES=json.dumps(cases))
        proc = subprocess.run(["node", "--input-type=module", "-e", HARNESS],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        return {r["id"]: r for r in json.loads(proc.stdout)}
    return _run


FORM = {"name": "Dana", "email": "dana@example.com", "message": "Need a scraper for supplier prices.",
        "budget": "", "timeline": "", "website": "", "opened_at": 1792819613122, "evil": "dropped"}


def test_R1_what_the_relay_signs_is_what_n8n_verifies(run):
    r = run([{"id": "ok", "form": FORM}])["ok"]
    assert r["status"] == 200 and r["forwarded"]
    assert r["verify"] == {"ok": True, "reason": "ok"}, "Worker HMAC and n8n HMAC disagree"


def test_R1_the_sender_ip_never_leaves_the_worker(run):
    p = run([{"id": "ok", "form": FORM}])["ok"]["payload"]
    assert "203.0.113.9" not in json.dumps(p)
    assert len(p["client"]) == 16, "n8n gets a salted hash, enough for the repeat-sender check"
    assert "evil" not in p, "only the known form fields are forwarded"


def test_R2_a_tunnel_that_is_down_reads_as_unavailable(run):
    out = run([{"id": "off", "form": FORM, "upstream_status": 404},
               {"id": "5xx", "form": FORM, "upstream_status": 502}])
    assert out["off"]["status"] == 503, "ngrok 404 = PC off = downtime, not a bad request"
    assert out["5xx"]["status"] == 503


def test_R3_requests_the_page_did_not_send_are_refused(run):
    out = run([{"id": "foreign", "form": FORM, "origin": "https://evil.example"},
               {"id": "get", "method": "GET"},
               {"id": "path", "path": "/admin", "form": FORM},
               {"id": "noconf", "form": FORM, "env": "empty"}])
    assert (out["foreign"]["status"], out["foreign"]["forwarded"]) == (403, False)
    assert out["get"]["status"] == 405
    assert out["path"]["status"] == 404
    assert (out["noconf"]["status"], out["noconf"]["forwarded"]) == (503, False), \
        "a relay without its secret must refuse, not forward unsigned"


# --------------------------------------------------------------------------
# uptime monitor (worker/monitor.js)
# --------------------------------------------------------------------------
MONITOR_HARNESS = r"""
const m = await import(process.env.MONITOR);
const MIN = 60_000, t0 = 1_800_000_000_000;
const out = {};

// transition(): one blip is not an outage; two misses are; recovery reports minutes
let s = null; const events = [];
for (const [i, ok] of [true, false, true, false, false, false, false, true, true].entries()) {
  const r = m.transition(s, ok, t0 + i * 5 * MIN); s = r.next; if (r.event) events.push(r.event);
}
out.events = events; out.final = s;

// runCheck(): KV + Slack, with a fake n8n that is down for checks 2..4
const kv = new Map(); const env = {
  STATUS: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => { kv.set(k, v); } },
  N8N_WEBHOOK_URL: 'https://x.ngrok-free.dev/webhook/inquiry', SLACK_WEBHOOK_URL: 'https://hooks.slack.com/T/x',
};
const slack = []; const probed = [];
for (let i = 0; i < 6; i++) {
  const up = !(i >= 1 && i <= 3);
  const upstream = async (url, init) => {
    if (url.startsWith('https://hooks.slack.com')) { slack.push(JSON.parse(init.body).text); return new Response('ok'); }
    probed.push(url);
    if (!up) throw new Error('tunnel offline');
    return new Response('{"status":"ok"}', { status: 200 });
  };
  await m.runCheck(env, t0 + i * 5 * MIN, upstream);
}
out.slack = slack; out.probed = probed[0];
out.status = await m.status(env);
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def monitor():
    env = dict(os.environ, MONITOR=(ROOT / "worker" / "monitor.js").as_uri())
    proc = subprocess.run(["node", "--input-type=module", "-e", MONITOR_HARNESS],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_M1_one_missed_check_is_not_an_outage_two_are(monitor):
    ev = monitor["events"]
    assert [e["type"] for e in ev] == ["down", "up"], "the single blip at check 1 must not page anyone"
    down, up = ev
    assert down["down_at"] == 1_800_000_000_000 + 3 * 5 * 60_000, "outage starts at the first miss"
    assert up["minutes"] == 20


def test_M1_counters_feed_the_uptime_figure(monitor):
    assert monitor["final"]["checks"] == 9 and monitor["final"]["failed_checks"] == 5


def test_M2_slack_hears_exactly_twice_per_outage(monitor):
    assert len(monitor["slack"]) == 2
    assert "unreachable" in monitor["slack"][0] and "back up" in monitor["slack"][1]
    assert "15 min" in monitor["slack"][1]


def test_M2_status_endpoint_reports_the_record(monitor):
    st = monitor["status"]
    assert st["incidents"] == 1 and st["total_down_minutes"] == 15
    assert st["checks"] == 6 and st["failed_checks"] == 3
    assert monitor["probed"] == "https://x.ngrok-free.dev/healthz", "health URL derives from the webhook URL"
