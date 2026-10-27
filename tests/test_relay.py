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
console.log = (...a) => process.stderr.write(a.join(' ') + '\n');
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
  STATUS: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => { kv.set(k, v); },
            delete: async (k) => { kv.delete(k); },
            list: async ({ prefix = '' } = {}) => ({ keys: [...kv.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })) }) },
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


# --------------------------------------------------------------------------
# queue: inquiries held while n8n is down (worker/queue.js)
# --------------------------------------------------------------------------
QUEUE_HARNESS = r"""
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
console.log = (...a) => process.stderr.write(a.join(' ') + '\n');
const relay = (await import(process.env.WORKER)).default;
const q = await import(process.env.QUEUE);
const m = await import(process.env.MONITOR);
const inquiry = require(process.env.INQUIRY);
const MIN = 60_000, HOUR = 60 * MIN, t0 = 1_800_000_000_000;
const SECRET = 's3cret-test';

function makeEnv() {
  const kv = new Map();
  const ops = { lists: 0 };
  return { kv, ops, env: {
    INQUIRY_SECRET: SECRET, N8N_WEBHOOK_URL: 'https://x.ngrok-free.dev/webhook/inquiry',
    SLACK_WEBHOOK_URL: 'https://hooks.slack.com/T/x',
    STATUS: {
      get: async (k) => kv.get(k) ?? null,
      put: async (k, v) => { kv.set(k, v); },
      delete: async (k) => { kv.delete(k); },
      list: async ({ prefix = '', limit = 1000 } = {}) => (ops.lists += 1, {
        keys: [...kv.keys()].filter((k) => k.startsWith(prefix)).sort().slice(0, limit).map((name) => ({ name })),
        list_complete: true }),
    } } };
}
const qkeys = (kv) => [...kv.keys()].filter((k) => k.startsWith('q:')).sort();
const form = (msg) => ({ name: 'Dana', email: 'dana@example.com', message: msg, opened_at: 1792819613122 });
const post = (body) => new Request('https://ether-inquiry.example.workers.dev/api/inquiry', {
  method: 'POST', body: JSON.stringify(body),
  headers: { 'Content-Type': 'application/json', 'CF-Connecting-IP': '203.0.113.9',
             Origin: 'https://ether-inquiry.example.workers.dev' } });
const out = {};

// R4: tunnel down -> held, sender told it arrived
{
  const { kv, env } = makeEnv();
  const down = async () => new Response('tunnel offline', { status: 404 });
  const res = await relay.fetch(post(form('Need a scraper for supplier prices.')), env, {}, down);
  const keys = qkeys(kv);
  const held = JSON.parse(kv.get(keys[0]));
  out.R4 = { status: res.status, body: await res.json(), held: keys.length,
             payload: JSON.parse(held.payload), stats: JSON.parse(kv.get('queue_stats')) };
  const res2 = await relay.fetch(post(form('Second one while still down.')), env, {}, async () => { throw new Error('ETIMEDOUT'); });
  out.R4.timeout_status = res2.status; out.R4.held_after_timeout = qkeys(kv).length;
  const refused = await relay.fetch(post(form('n8n up but refusing.')), env, {}, async () => new Response('', { status: 401 }));
  out.R4.refused_status = refused.status; out.R4.held_after_refusal = qkeys(kv).length;
}

// R4c: a bot posting all night cannot spend the KV write quota
{
  const { kv, env } = makeEnv();
  const down = async () => new Response('tunnel offline', { status: 404 });
  const statuses = [];
  for (let i = 0; i < q.MAX_HELD + 3; i++)
    statuses.push((await relay.fetch(post(form('Spam message number ' + i + ' ok')), env, {}, down)).status);
  out.R4c = { max: q.MAX_HELD, statuses, held: qkeys(kv).length };
}

// R5: delivered six hours later, signed at delivery time, oldest first
{
  const { kv, env } = makeEnv();
  await q.enqueue(env, JSON.stringify({ message: 'first', received_at: t0 }), t0);
  await q.enqueue(env, JSON.stringify({ message: 'second', received_at: t0 + MIN }), t0 + MIN);
  const sent = [];
  const n8n = async (url, init) => { sent.push(init); return new Response('{"ok":true}', { status: 202 }); };
  const at = t0 + 6 * HOUR;
  const r = await q.deliverQueue(env, at, n8n);
  out.R5 = { result: r, left: qkeys(kv).length, stats: JSON.parse(kv.get('queue_stats')),
    order: sent.map((s) => JSON.parse(JSON.parse(s.body).payload).message),
    ts: sent.map((s) => Number(s.headers['X-Ether-Timestamp'])),
    verify_now: sent.map((s) => inquiry.verifySignature(SECRET, s.headers['X-Ether-Timestamp'],
                  JSON.parse(s.body).payload, s.headers['X-Ether-Signature'], at).reason),
    verify_old: inquiry.verifySignature(SECRET, String(t0), JSON.parse(sent[0].body).payload,
                  sent[0].headers['X-Ether-Signature'], t0).reason };
}

// R5b: tunnel drops again mid-run -> stop, keep the rest
{
  const { kv, env } = makeEnv();
  for (let i = 0; i < 3; i++) await q.enqueue(env, JSON.stringify({ message: 'm' + i }), t0 + i);
  let n = 0;
  const flaky = async () => (++n === 1 ? new Response('', { status: 202 }) : new Response('', { status: 404 }));
  const r = await q.deliverQueue(env, t0 + HOUR, flaky);
  out.R5b = { result: r, left: qkeys(kv).length, calls: n };
}

// R5c: n8n up but refusing one inquiry -> moved aside after 5 refusals, queue moves on
{
  const { kv, env } = makeEnv();
  await q.enqueue(env, JSON.stringify({ message: 'poison' }), t0);
  await q.enqueue(env, JSON.stringify({ message: 'fine' }), t0 + 1);
  const n8n = async (url, init) => new Response('', {
    status: JSON.parse(JSON.parse(init.body).payload).message === 'poison' ? 401 : 202 });
  const runs = [];
  for (let i = 0; i < 6; i++) runs.push(await q.deliverQueue(env, t0 + HOUR + i * 5 * MIN, n8n));
  out.R5c = { runs, left: qkeys(kv).length, dead: [...kv.keys()].filter((k) => k.startsWith('dead:')).length,
              stats: JSON.parse(kv.get('queue_stats')) };
}

// M3: the overnight case end to end: n8n down, an inquiry held, recovery delivers it.
// The relay stamps with the real clock, so this scenario runs on it too.
{
  const t0 = Date.now();
  const { kv, ops, env } = makeEnv();
  let n8nUp = true; const slack = []; const delivered = [];
  const net = async (url, init) => {
    if (url.startsWith('https://hooks.slack.com')) { slack.push(JSON.parse(init.body).text); return new Response('ok'); }
    if (!n8nUp) return new Response('tunnel offline', { status: 404 });
    if (url.endsWith('/healthz')) return new Response('{"status":"ok"}', { status: 200 });
    delivered.push(JSON.parse(JSON.parse(init.body).payload).message);
    return new Response('{"ok":true}', { status: 202 });
  };
  await m.runCheck(env, t0, net);
  n8nUp = false;
  await m.runCheck(env, t0 + 5 * MIN, net);
  await m.runCheck(env, t0 + 10 * MIN, net);
  const res = await relay.fetch(post(form('Sent at night while the PC was off.')), env, {}, net);
  const heldWhileDown = qkeys(kv).length;
  n8nUp = true;
  const rec = await m.runCheck(env, t0 + 8 * HOUR, net);
  const listsBefore = ops.lists;
  const status = (await m.status(env)).queue;
  out.M3 = { sender_status: res.status, held_while_down: heldWhileDown, delivered, queue: rec.queue,
             slack, status, status_lists: ops.lists - listsBefore };
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def queue_run():
    env = dict(os.environ, WORKER=(ROOT / "worker" / "inquiry-relay.js").as_uri(),
               QUEUE=(ROOT / "worker" / "queue.js").as_uri(),
               MONITOR=(ROOT / "worker" / "monitor.js").as_uri(),
               INQUIRY=str(ROOT / "n8n" / "inquiry.js"))
    proc = subprocess.run(["node", "--input-type=module", "-e", QUEUE_HARNESS],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_R4_an_inquiry_sent_while_n8n_is_down_is_held_not_refused(queue_run):
    r = queue_run["R4"]
    assert r["status"] == 202 and r["body"] == {"ok": True, "queued": True}, "the sender is told it arrived"
    assert r["held"] == 1 and r["stats"]["queued_total"] == 1
    assert r["payload"]["message"] == "Need a scraper for supplier prices."
    assert isinstance(r["payload"]["received_at"], int), "n8n screens and dates it by when the relay got it"
    assert "203.0.113.9" not in json.dumps(r["payload"]), "a held inquiry carries the hash, never the IP"


def test_R4_a_timeout_is_held_but_a_refusal_is_not(queue_run):
    r = queue_run["R4"]
    assert (r["timeout_status"], r["held_after_timeout"]) == (202, 2)
    assert (r["refused_status"], r["held_after_refusal"]) == (400, 2), \
        "n8n up and answering 401 is a config fault; queueing it would only fail again"


def test_R4_the_queue_is_capped_so_a_bot_cannot_use_up_kv(queue_run):
    r = queue_run["R4c"]
    assert r["held"] == r["max"] == 20
    assert r["statuses"] == [202] * 20 + [503] * 3, "past the cap the sender gets the old offline answer"


def test_R5_held_inquiries_are_signed_again_when_delivered(queue_run):
    r = queue_run["R5"]
    assert r["result"]["delivered"] == 2 and r["left"] == 0
    assert r["order"] == ["first", "second"], "oldest first"
    assert r["verify_now"] == ["ok", "ok"], "the n8n verifier accepts them at delivery time"
    assert set(r["ts"]) == {1_800_000_000_000 + 6 * 3_600_000}, "the timestamp is the delivery moment"
    assert r["verify_old"] == "bad_signature", "it is a new signature, not the old one replayed"
    assert r["stats"]["delivered_total"] == 2 and r["stats"]["longest_wait_minutes"] == 360


def test_R5_a_tunnel_that_drops_mid_run_keeps_the_rest(queue_run):
    r = queue_run["R5b"]
    assert (r["result"]["delivered"], r["left"], r["calls"]) == (1, 2, 2)


def test_R5_one_refused_inquiry_cannot_block_the_queue(queue_run):
    r = queue_run["R5c"]
    assert [x["delivered"] for x in r["runs"]] == [0, 0, 0, 0, 1, 0], \
        "after five refusals the poison item is set aside and the next one goes through"
    assert r["runs"][4]["dead"] == 1 and r["left"] == 0 and r["dead"] == 1
    assert r["stats"]["dead_total"] == 1 and r["stats"]["delivered_total"] == 1


def test_M3_overnight_outage_loses_nothing(queue_run):
    r = queue_run["M3"]
    assert r["sender_status"] == 202 and r["held_while_down"] == 1
    assert r["delivered"] == ["Sent at night while the PC was off."]
    assert r["queue"]["delivered"] == 1
    assert len(r["slack"]) == 2 and "1 inquiry(s) held during the outage delivered" in r["slack"][1]
    st = r["status"]
    assert (st["waiting"], st["held_total"], st["delivered_total"]) == (0, 1, 1)
    assert 475 <= st["longest_wait_minutes"] <= 480, "held from the night send until the 8 h recovery check"
    assert r["status_lists"] == 0, \
        "/api/status is public; it must not spend the KV lists (1,000/day) delivery depends on"
