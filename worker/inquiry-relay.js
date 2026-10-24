/**
 * inquiry-relay — Cloudflare Worker between the static inquiry page and n8n.
 *
 * Why it exists: the page is static (GitHub Pages), so it cannot keep a
 * secret, and without one the n8n webhook accepts any POST from anyone who
 * finds its URL — each of which would cost an Anthropic call. The Worker
 * keeps INQUIRY_SECRET, signs `${timestamp}.${payload}` with HMAC-SHA256,
 * and forwards. WF-4's Verify node rejects anything not signed this way.
 *
 * What it deliberately does NOT do: spam filtering. That runs in n8n, so
 * every bot attempt is stored and counted rather than vanishing here.
 *
 * The inquiry page itself is a static asset of this same Worker
 * (public/index.html), so the form posts same-origin to /api/inquiry.
 *
 * Secrets / vars (wrangler secret put / dashboard):
 *   INQUIRY_SECRET   same value as deploy/inquiry.env
 *   N8N_WEBHOOK_URL  https://<ngrok domain>/webhook/inquiry
 *   ALLOWED_ORIGINS  optional extra origins, comma-separated
 */

import { runCheck, status } from './monitor.js';

const MAX_BODY = 20_000;
const FIELDS = ['name', 'email', 'message', 'budget', 'timeline', 'website'];

const enc = new TextEncoder();
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');

async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode(message)));
}

async function sha256Hex(s) {
  return hex(await crypto.subtle.digest('SHA-256', enc.encode(s)));
}

function reply(body, status, cors) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', ...cors },
  });
}

export default {
  // Cron Trigger: uptime check of the self-hosted n8n (see monitor.js)
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runCheck(env));
  },

  async fetch(request, env, ctx, upstream = fetch) {
    const url = new URL(request.url);
    if (url.pathname === '/api/status' && request.method === 'GET') {
      const body = env.STATUS ? await status(env) : { state: 'unknown', note: 'monitor not configured' };
      return new Response(JSON.stringify(body, null, 2), {
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
    }
    if (url.pathname !== '/api/inquiry') return new Response('Not found', { status: 404 });
    const origin = request.headers.get('Origin') || '';
    const allowed = [url.origin, ...(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean)];
    const okOrigin = allowed.includes(origin);
    const cors = {
      'Access-Control-Allow-Origin': okOrigin ? origin : (allowed[0] || 'null'),
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      Vary: 'Origin',
    };

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'POST') return reply({ ok: false, error: 'method' }, 405, cors);
    if (!okOrigin) return reply({ ok: false, error: 'origin' }, 403, cors);
    if (!env.INQUIRY_SECRET || !env.N8N_WEBHOOK_URL) {
      console.log('relay misconfigured: secret or webhook url missing');
      return reply({ ok: false, error: 'unavailable' }, 503, cors);
    }

    const text = await request.text();
    if (text.length > MAX_BODY) return reply({ ok: false, error: 'too_large' }, 413, cors);
    let form;
    try { form = JSON.parse(text); } catch { return reply({ ok: false, error: 'bad_json' }, 400, cors); }

    // The sender IP never leaves the Worker: n8n only sees a salted hash,
    // enough to notice one client sending ten times in a minute.
    const ip = request.headers.get('CF-Connecting-IP') || '';
    const client = ip ? (await sha256Hex(env.INQUIRY_SECRET + '|' + ip)).slice(0, 16) : '';

    const clean = {};
    for (const f of FIELDS) clean[f] = typeof form[f] === 'string' ? form[f].slice(0, 6000) : '';
    clean.opened_at = Number.isFinite(Number(form.opened_at)) ? Number(form.opened_at) : null;
    clean.client = client;

    const payload = JSON.stringify(clean);
    const ts = String(Date.now());
    const signature = await hmacHex(env.INQUIRY_SECRET, ts + '.' + payload);

    let res;
    try {
      res = await upstream(env.N8N_WEBHOOK_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Ether-Timestamp': ts,
          'X-Ether-Signature': signature,
          'ngrok-skip-browser-warning': '1',
        },
        body: JSON.stringify({ payload }),
      });
    } catch (e) {
      console.log('upstream unreachable', String(e));
      return reply({ ok: false, error: 'unavailable' }, 503, cors);
    }

    // ngrok answers 404 when the tunnel is down (PC off). That is downtime,
    // not a bad request: the page shows the fallback email address.
    if (res.status === 404 || res.status >= 500) {
      console.log('upstream down', res.status);
      return reply({ ok: false, error: 'unavailable' }, 503, cors);
    }
    return reply({ ok: res.ok }, res.ok ? 200 : 400, cors);
  },
};
