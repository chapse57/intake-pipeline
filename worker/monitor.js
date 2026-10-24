/**
 * monitor.js — uptime record for the self-hosted n8n.
 *
 * The n8n runs on a PC, so it will go down: the PC sleeps, reboots, loses
 * network. That is fine. What is not fine is not knowing. A Cron Trigger on
 * this Worker checks /healthz every 5 minutes from outside, keeps the state
 * in KV, and posts to Slack exactly twice per outage: when it goes down and
 * when it comes back, with the duration. /api/status serves the record.
 *
 * transition() is pure so the rules are tested without a network or KV.
 */

export const FAILS_TO_DECLARE_DOWN = 2;   // one missed check is a blip, two is an outage
export const MAX_INCIDENTS = 200;

export function initialState(now) {
  return { state: 'up', since: now, fails: 0, first_fail_at: null,
           checks: 0, failed_checks: 0, started_at: now };
}

/**
 * @returns {{ next: object, event: null | {type:'down'|'up', ...} }}
 */
export function transition(prev, ok, now) {
  const s = Object.assign({}, prev || initialState(now));
  s.checks += 1;
  if (!ok) s.failed_checks += 1;

  if (s.state === 'up') {
    if (ok) { s.fails = 0; s.first_fail_at = null; return { next: s, event: null }; }
    s.fails += 1;
    if (s.fails === 1) s.first_fail_at = now;
    if (s.fails >= FAILS_TO_DECLARE_DOWN) {
      s.state = 'down';
      s.since = s.first_fail_at;          // the outage started at the first miss
      return { next: s, event: { type: 'down', down_at: s.since } };
    }
    return { next: s, event: null };
  }

  // state === 'down'
  if (!ok) return { next: s, event: null };
  const downAt = s.since;
  s.state = 'up'; s.since = now; s.fails = 0; s.first_fail_at = null;
  return { next: s, event: { type: 'up', down_at: downAt, up_at: now,
                             minutes: Math.round((now - downAt) / 60000) } };
}

export function healthUrl(env) {
  if (env.N8N_HEALTH_URL) return env.N8N_HEALTH_URL;
  return String(env.N8N_WEBHOOK_URL || '').replace(/\/webhook\/.*$/, '/healthz');
}

export async function probe(url, upstream = fetch) {
  try {
    const res = await upstream(url, {
      headers: { 'ngrok-skip-browser-warning': '1' },
      signal: AbortSignal.timeout(10_000),
    });
    const body = await res.text();
    return res.status === 200 && body.includes('"ok"');
  } catch {
    return false;
  }
}

const kst = (ms) => new Date(ms + 9 * 3600_000).toISOString().replace('T', ' ').slice(0, 16) + ' KST';

export async function runCheck(env, now = Date.now(), upstream = fetch) {
  const ok = await probe(healthUrl(env), upstream);
  const prev = JSON.parse((await env.STATUS.get('state')) || 'null');
  const { next, event } = transition(prev, ok, now);
  await env.STATUS.put('state', JSON.stringify(next));

  if (event) {
    if (event.type === 'up') {
      const list = JSON.parse((await env.STATUS.get('incidents')) || '[]');
      list.push({ down_at: event.down_at, up_at: event.up_at, minutes: event.minutes });
      await env.STATUS.put('incidents', JSON.stringify(list.slice(-MAX_INCIDENTS)));
    }
    if (env.SLACK_WEBHOOK_URL) {
      const text = event.type === 'down'
        ? `:red_circle: n8n unreachable since ${kst(event.down_at)} (2 missed health checks)`
        : `:large_green_circle: n8n back up at ${kst(event.up_at)} after ${event.minutes} min`;
      try {
        await upstream(env.SLACK_WEBHOOK_URL, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
        });
      } catch { /* the KV record is the source of truth; Slack is the notice */ }
    }
  }
  return { ok, next, event };
}

export async function status(env) {
  const s = JSON.parse((await env.STATUS.get('state')) || 'null');
  const incidents = JSON.parse((await env.STATUS.get('incidents')) || '[]');
  if (!s) return { state: 'unknown', note: 'no checks yet' };
  const downMinutes = incidents.reduce((a, i) => a + i.minutes, 0);
  return {
    state: s.state, since: s.since, monitoring_since: s.started_at,
    checks: s.checks, failed_checks: s.failed_checks,
    check_uptime_pct: s.checks ? Math.round((1 - s.failed_checks / s.checks) * 10000) / 100 : null,
    incidents: incidents.length, total_down_minutes: downMinutes,
    recent_incidents: incidents.slice(-20),
  };
}
