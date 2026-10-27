'use strict';
/**
 * digest.js — WF-5: once a day, tell a person what the spam screen kept out.
 *
 * Spam rows never reach Slack, which is the point, and also the risk: a real
 * client screened by mistake is silent. Once a day this lists every row that
 * became Spam since the last digest, grouped by reason, each with a Notion
 * link, so a false positive costs one glance instead of a lost client.
 *
 * Runs in the WF-5 Code nodes (the generator embeds this file) and under
 * node for the offline tests.
 *
 * The window starts where the last *sent* digest ended (n8n static data), so
 * a morning the PC was off is covered by the next one instead of skipped.
 * With no record it is the last 24 h, and it never reaches back more than 7
 * days. Rows are selected by Notion's created time, not "Received": an
 * inquiry held overnight by the relay is created when it is delivered, and
 * selecting by send time would let one fall between two digests.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_WINDOW_MS = 7 * DAY_MS;
const MAX_LISTED = 15;
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

/**
 * [since, until), both on a whole minute. Notion stores created_time to the
 * minute, so a row made at 09:00:50 reads 09:00:00; with a window ending at
 * 09:00:45 it would be after this digest and before the next one — never
 * listed. Ending on the minute puts it in the next window instead.
 */
function digestWindow(lastSentAt, nowMs) {
  const until = Math.floor(nowMs / 60000) * 60000;
  const last = Number(lastSentAt);
  let since = until - DAY_MS;
  if (Number.isFinite(last) && last > 0 && last <= until) since = Math.max(last, until - MAX_WINDOW_MS);
  return { since, until };
}

function kst(ms) {
  // n8n's Node may lack full ICU, so no toLocaleString: shift and format by hand
  const d = new Date(ms + KST_OFFSET_MS).toISOString();
  return d.slice(5, 10) + ' ' + d.slice(11, 16) + ' KST';
}

const plain = (p) => {
  if (!p) return '';
  const parts = p.title || p.rich_text || [];
  return parts.map((t) => t.plain_text || (t.text && t.text.content) || '').join('');
};

/** "too_fast (1200)" -> "too_fast"; "model: spam" stays as it is. */
function reasonKey(spamReason) {
  const r = String(spamReason || '').trim();
  if (!r) return 'unknown';
  return r.replace(/\s*\(.*$/, '');
}

/**
 * @param {object[]} rows    Notion query results (Status = Spam, created in window)
 * @param {{since:number, until:number}} win
 * @param {boolean} hasMore  Notion said there are more than one page of rows
 */
function summarizeSpam(rows, win, hasMore) {
  const list = (rows || []).map((r) => {
    const p = r.properties || {};
    return {
      name: plain(p.Name) || '(no name)',
      reason: plain(p['Spam Reason']),
      url: r.url || '',
    };
  });
  const counts = {};
  for (const x of list) counts[reasonKey(x.reason)] = (counts[reasonKey(x.reason)] || 0) + 1;
  const byReason = Object.entries(counts).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));

  const n = list.length;
  const span = 'since ' + kst(win.since);
  let text;
  if (n === 0) {
    text = ':wastebasket: Spam digest - nothing screened ' + span + '. Nothing to check.';
  } else {
    const head = ':wastebasket: Spam digest - ' + n + (hasMore ? '+' : '') + ' screened ' + span
      + '. Anything real in here?';
    const tally = byReason.map(([k, c]) => k + ' ' + c).join(' · ');
    const lines = list.slice(0, MAX_LISTED).map((x) =>
      '• ' + x.name + ' - ' + (x.reason || 'no reason') + (x.url ? ' - <' + x.url + '|open>' : ''));
    const more = n > MAX_LISTED ? ['… and ' + (n - MAX_LISTED) + ' more in Notion (Status = Spam)'] : [];
    text = [head, tally].concat(lines, more).join('\n');
  }
  return { count: n, has_more: Boolean(hasMore), by_reason: byReason, text };
}

module.exports = { DAY_MS, MAX_WINDOW_MS, MAX_LISTED, digestWindow, kst, reasonKey, summarizeSpam };

// --- CLI -------------------------------------------------------------------
// echo '[{"id":"..","op":"window"|"summarize", ...}]' | node digest.js
if (require.main === module) {
  let buf = '';
  process.stdin.on('data', (d) => (buf += d));
  process.stdin.on('end', () => {
    const reqs = JSON.parse(buf);
    const res = (Array.isArray(reqs) ? reqs : [reqs]).map((r) => {
      let out;
      if (r.op === 'window') out = digestWindow(r.last, r.now);
      else if (r.op === 'summarize') out = summarizeSpam(r.rows, r.win, r.has_more);
      else out = { error: 'unknown op ' + r.op };
      return Object.assign({ id: r.id }, out);
    });
    // ASCII only (\uXXXX escapes): a Windows console pipe may be read as cp949
    process.stdout.write(JSON.stringify(res).replace(/[\u007f-\uffff]/g,
      (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')));
  });
}
