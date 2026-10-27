/**
 * queue.js — hold inquiries while n8n is down, deliver them when it is back.
 *
 * The n8n runs on a PC that is off at night. Before this, an inquiry sent
 * while it was off got "the form is offline, try later" and was gone unless
 * the sender came back. Now the Worker keeps it in KV (the same STATUS
 * namespace as the uptime record) and tells the sender it was received.
 * The 5-minute health check delivers held inquiries once /healthz answers.
 *
 * Delivery is at-least-once: if n8n saves an inquiry but its answer is lost
 * (timeout), the inquiry is sent again on the next run. A duplicate row is
 * the accepted failure; a lost inquiry is not.
 *
 * KV free tier: 1,000 writes, deletes and lists a day each. The monitor
 * already writes once per check (288 a day); a held inquiry costs two
 * writes and one delete, at most MAX_HELD wait at a time, and delivery lists
 * once per check while n8n is up. /api/status reads the counts, never lists.
 */

import { forward } from './forward.js';

export const QUEUE_PREFIX = 'q:';
export const DEAD_PREFIX = 'dead:';
export const MAX_DELIVER_PER_RUN = 25;
// An inquiry n8n keeps refusing while it is up (401 after a secret change,
// 400) would block everything behind it. After this many refusals it is
// moved aside to dead:<key>, still readable, and the queue moves on.
export const MAX_REFUSALS = 5;
// Each held inquiry costs KV writes, and the free tier's 1,000 writes a day
// are shared with the uptime record. A bot posting all night would otherwise
// use them up. Real traffic is a handful a day; past this many waiting, the
// sender gets the old "offline, try later" answer instead.
export const MAX_HELD = 20;

const STATS_KEY = 'queue_stats';

function newKey(now) {
  // KV lists keys in lexicographic order, so a zero-padded time keeps it FIFO
  return QUEUE_PREFIX + String(now).padStart(15, '0') + ':' + crypto.randomUUID().slice(0, 8);
}

export async function readStats(env) {
  const base = { queued_total: 0, delivered_total: 0, dead_total: 0, longest_wait_minutes: 0,
                 last_queued_at: null, last_delivered_at: null };
  return Object.assign(base, JSON.parse((await env.STATUS.get(STATS_KEY)) || 'null') || {});
}

/** @returns {Promise<string|null>} the key, or null when the queue is full */
export async function enqueue(env, payload, now = Date.now()) {
  const st = await readStats(env);
  if (waitingFrom(st) >= MAX_HELD) return null;
  const key = newKey(now);
  await env.STATUS.put(key, JSON.stringify({ payload, queued_at: now, refusals: 0 }));
  st.queued_total += 1;
  st.last_queued_at = now;
  await env.STATUS.put(STATS_KEY, JSON.stringify(st));
  return key;
}

// Counted from the stats, not with a KV list: /api/status is public, and
// lists are capped at 1,000 a day. Anyone reloading it a thousand times
// would otherwise use up the lists the health check needs to deliver.
export function waitingFrom(st) {
  return Math.max(0, st.queued_total - st.delivered_total - st.dead_total);
}

/**
 * Oldest first. Stops at the first inquiry n8n does not take, so a tunnel
 * that drops again mid-run leaves the rest where they are.
 */
export async function deliverQueue(env, now = Date.now(), upstream = fetch) {
  const l = await env.STATUS.list({ prefix: QUEUE_PREFIX, limit: MAX_DELIVER_PER_RUN });
  if (!l.keys.length) return { delivered: 0, left: 0, dead: 0 };

  const st = await readStats(env);
  let delivered = 0, dead = 0, stopped = false;
  for (const { name } of l.keys) {
    const raw = await env.STATUS.get(name);
    if (!raw) continue;                       // delivered by an overlapping run (KV list can lag)
    const item = JSON.parse(raw);
    const r = await forward(env, item.payload, upstream, now);

    if (r.delivered) {
      await env.STATUS.delete(name);
      delivered += 1;
      st.delivered_total += 1;
      st.last_delivered_at = now;
      st.longest_wait_minutes = Math.max(st.longest_wait_minutes, Math.round((now - item.queued_at) / 60000));
      continue;
    }
    if (!r.down) {                            // n8n is up and refused it
      item.refusals = (item.refusals || 0) + 1;
      item.last_status = r.status;
      if (item.refusals >= MAX_REFUSALS) {
        await env.STATUS.put(DEAD_PREFIX + name, JSON.stringify(item));
        await env.STATUS.delete(name);
        dead += 1;
        st.dead_total += 1;
        continue;
      }
      await env.STATUS.put(name, JSON.stringify(item));
    }
    stopped = true;
    break;
  }
  if (delivered || dead) await env.STATUS.put(STATS_KEY, JSON.stringify(st));
  return { delivered, dead, left: l.keys.length - delivered - dead, stopped };
}
