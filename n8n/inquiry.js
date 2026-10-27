/**
 * inquiry.js — WF-4 "Verify", "Screen" and "Guard" nodes for the public
 * Ether inquiry form.
 *
 * Same dual-use contract as normalize.js and guard.js: require() it from the
 * tests, and the generator pastes everything above the export line into the
 * n8n Code nodes. In n8n the body of guard.js is pasted in front of this one,
 * so foldForQuote and the evidence limits are the exact same functions WF-1
 * uses; in Node they are pulled in with require. One quote rule, two callers.
 *
 * Three questions, answered in this order and never merged:
 *   1. Did this request come from our form relay?    verifySignature
 *   2. Does it look like a bot before we pay a model? screenSpam
 *   3. Is the model's label one we can trust?         guardLabel
 */

'use strict';

/* global foldForQuote, quoteFoundIn, CONFIDENCE_FLOOR, MIN_EVIDENCE_CHARS,
          MAX_EVIDENCE_CHARS, MAX_EVIDENCE_RATIO, RATIO_APPLIES_FROM */
const G = (typeof foldForQuote === 'function')
  ? { foldForQuote, quoteFoundIn, CONFIDENCE_FLOOR, MIN_EVIDENCE_CHARS,
      MAX_EVIDENCE_CHARS, MAX_EVIDENCE_RATIO, RATIO_APPLIES_FROM }
  : require('./guard.js');

const CRYPTO = require('crypto');   // n8n: NODE_FUNCTION_ALLOW_BUILTIN=crypto

const INQUIRY_LABELS = ['data-collection', 'pipeline', 'automation', 'ai-llm', 'other', 'spam'];

const SIGNATURE_MAX_AGE_MS = 5 * 60 * 1000;
const MIN_FILL_MS = 3000;                  // a person does not fill four fields in 3 s
const MAX_FILL_MS = 24 * 60 * 60 * 1000;   // a form open for a day is a replayed form
const MIN_MESSAGE_CHARS = 10;
const MAX_MESSAGE_CHARS = 5000;
// More links than this is a suspicion, not a verdict: the page's own example
// is "prices from 30 supplier sites", and a real client may paste the URLs.
const MAX_LINKS = 2;
const MAX_FROM_CLIENT_10MIN = 3;           // a 4th send inside 10 minutes is flooding
const NOTION_TEXT_LIMIT = 1900;
// Inquiries are short. guard.js applies its 40% quote cap from 100 chars,
// tuned for multi-paragraph applications; on a two-line inquiry it rejects
// a perfectly good clause (first live run: 66 of 102 chars). The char cap
// always applies; from 100 to 249 chars a looser 80% ratio (so quoting the
// whole message fails); from 250 chars guard.js's 40%. Below 100 chars only
// the char cap applies, so a one-line message can be quoted almost whole.
const INQUIRY_RATIO_APPLIES_FROM = 250;
const INQUIRY_SHORT_MAX_RATIO = 0.8;

// ---------------------------------------------------------------------------
// 1. signature
// ---------------------------------------------------------------------------
/**
 * The page is static, so it cannot keep a secret. The Cloudflare Worker that
 * relays the form can. It signs `${timestamp}.${payload}` with HMAC-SHA256
 * and this node recomputes it. A request that did not go through the relay —
 * someone who found the n8n URL and posts to it directly — fails here and
 * never reaches the model, so it cannot spend Anthropic credit.
 *
 * The payload is signed as the exact string the Worker sent (body.payload),
 * not a re-serialisation of parsed JSON, so key order and escaping cannot
 * make a genuine request fail.
 */
function hmacHex(secret, message) {
  return CRYPTO.createHmac('sha256', secret).update(message, 'utf8').digest('hex');
}

function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function verifySignature(secret, timestamp, payload, signature, nowMs) {
  if (!secret) return { ok: false, reason: 'server_secret_missing' };
  if (timestamp == null || timestamp === '' || signature == null || signature === '') {
    return { ok: false, reason: 'missing_headers' };
  }
  const ts = Number(timestamp);
  if (!Number.isFinite(ts)) return { ok: false, reason: 'bad_timestamp' };
  if (Math.abs(nowMs - ts) > SIGNATURE_MAX_AGE_MS) return { ok: false, reason: 'stale_timestamp' };
  if (typeof payload !== 'string' || payload === '') return { ok: false, reason: 'payload_not_string' };
  const expected = hmacHex(secret, String(timestamp) + '.' + payload);
  const got = String(signature).trim().toLowerCase();
  if (!constantTimeEqual(got, expected)) return { ok: false, reason: 'bad_signature' };
  return { ok: true, reason: 'ok' };
}

// ---------------------------------------------------------------------------
// 2. normalise + first-pass spam screen (no model, no cost)
// ---------------------------------------------------------------------------
function clean(s, max) {
  return String(s == null ? '' : s).replace(/\u0000/g, '').trim().slice(0, max);
}

function normalizeInquiry(form) {
  const f = form || {};
  const message = String(f.message == null ? '' : f.message).replace(/\u0000/g, '').trim();
  return {
    name: clean(f.name, 200),
    email: clean(f.email, 320).toLowerCase(),
    message,
    message_stored: message.slice(0, NOTION_TEXT_LIMIT),
    message_truncated: message.length > NOTION_TEXT_LIMIT,
    budget: clean(f.budget, 200),
    timeline: clean(f.timeline, 200),
    honeypot: clean(f.website, 500),
    opened_at: f.opened_at == null ? null : Number(f.opened_at),
    client: clean(f.client, 64),
    // set by the relay when it got the form; an inquiry held while n8n was
    // down arrives hours later and must not look like a day-old replayed form
    received_at: Number.isFinite(Number(f.received_at)) && f.received_at != null ? Number(f.received_at) : null,
  };
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const LINK_RE = /(https?:\/\/|www\.)/gi;

/**
 * First matching reason wins, and the order is cheapest-to-trust first: a
 * filled honeypot is certain, a link count is a heuristic. Spam is not
 * dropped — it is stored with this reason so it can be counted.
 *
 * A link-heavy message is not spam here. It comes back { spam: false,
 * reason: 'suspect_links', detail: n }: it still goes to the model, is
 * stored as Review, and Slack hears about it with a warning. Only the model
 * calling it spam (or a hard signal above) keeps it out of Slack.
 *
 * Fill time is measured to when the relay received the form (received_at),
 * falling back to now for a request without it.
 *
 * @param {object} inq            output of normalizeInquiry
 * @param {number} nowMs
 * @param {number} recentFromClient rows from the same client hash in the last 10 min
 */
function screenSpam(inq, nowMs, recentFromClient) {
  const hit = (reason, detail) => ({ spam: true, reason, detail: detail === undefined ? null : detail });
  if (inq.honeypot) return hit('honeypot');
  if (!EMAIL_RE.test(inq.email)) return hit('bad_email');
  if (inq.opened_at == null || !Number.isFinite(inq.opened_at)) return hit('no_open_time');
  const at = Number.isFinite(inq.received_at) ? inq.received_at : nowMs;
  const fill = at - inq.opened_at;
  if (fill < MIN_FILL_MS) return hit('too_fast', fill);
  if (fill > MAX_FILL_MS) return hit('stale_form', fill);
  if (inq.message.length < MIN_MESSAGE_CHARS) return hit('message_too_short', inq.message.length);
  if (inq.message.length > MAX_MESSAGE_CHARS) return hit('message_too_long', inq.message.length);
  if ((recentFromClient || 0) >= MAX_FROM_CLIENT_10MIN) return hit('rate_limited', recentFromClient);
  const links = (inq.message.match(LINK_RE) || []).length;
  if (links > MAX_LINKS) return { spam: false, reason: 'suspect_links', detail: links };
  return { spam: false, reason: 'ok', detail: null };
}

/** "3 links" for a suspect screen, null otherwise. Used in the Slack line. */
function suspectNote(screen) {
  if (!screen || screen.spam || !screen.reason || screen.reason === 'ok') return null;
  if (screen.reason === 'suspect_links') return screen.detail + ' links';
  return screen.reason;
}

// ---------------------------------------------------------------------------
// 3. label guard — guard.js rules, inquiry labels
// ---------------------------------------------------------------------------
function rejectLabel(reason, detail) {
  return { verdict: 'reject', label: null, evidence: null, confidence: null,
           reason, detail: detail === undefined ? null : detail };
}

/**
 * Same contract as guard(): the label must be one of the exact strings, the
 * quote must be a real, bounded part of the message, confidence must be a
 * number in range, and only then does the confidence floor route to review.
 */
function guardLabel(llmOutput, message) {
  let out = llmOutput;
  if (typeof out === 'string') {
    try { out = JSON.parse(out); } catch (e) { return rejectLabel('not_json', String(out).slice(0, 200)); }
  }
  if (out === null || typeof out !== 'object' || Array.isArray(out)) {
    return rejectLabel('not_an_object', typeof out);
  }
  const { label, evidence_quote: evidence, confidence } = out;

  if (typeof label !== 'string' || INQUIRY_LABELS.indexOf(label) === -1) {
    return rejectLabel('label_not_allowed', label === undefined ? null : label);
  }
  if (typeof evidence !== 'string' || G.foldForQuote(evidence).length < G.MIN_EVIDENCE_CHARS) {
    return rejectLabel('evidence_missing_or_too_short',
                       evidence === undefined ? null : String(evidence).slice(0, 200));
  }
  const qLen = G.foldForQuote(evidence).length;
  const sLen = G.foldForQuote(message).length;
  if (qLen > G.MAX_EVIDENCE_CHARS ||
      (sLen >= INQUIRY_RATIO_APPLIES_FROM && qLen > G.MAX_EVIDENCE_RATIO * sLen) ||
      (sLen >= G.RATIO_APPLIES_FROM && qLen > INQUIRY_SHORT_MAX_RATIO * sLen)) {
    return rejectLabel('evidence_too_long',
                       { chars: qLen, ratio: sLen ? Math.round((qLen / sLen) * 100) / 100 : null });
  }
  if (!G.quoteFoundIn(evidence, message)) {
    return rejectLabel('evidence_not_in_source', String(evidence).slice(0, 200));
  }
  if (typeof confidence !== 'number' || !isFinite(confidence)) {
    return rejectLabel('confidence_not_a_number', confidence === undefined ? null : String(confidence));
  }
  if (confidence < 0 || confidence > 1) return rejectLabel('confidence_out_of_range', confidence);

  if (confidence < G.CONFIDENCE_FLOOR) {
    return { verdict: 'review', label, evidence, confidence,
             reason: 'below_confidence_floor', detail: G.CONFIDENCE_FLOOR };
  }
  return { verdict: 'accept', label, evidence, confidence, reason: 'ok', detail: null };
}

/**
 * What the Inquiries row says. A rejected label rejects the label, never the
 * inquiry: the row is still written, as Review, for a person to read.
 */
function decideStatus(screen, g) {
  if (screen && screen.spam) return 'Spam';
  if (!g) return 'Review';
  if (g.verdict === 'accept' && g.label === 'spam') return 'Spam';
  if (suspectNote(screen)) return 'Review';      // a person looks before replying
  if (g.verdict === 'accept') return 'New';
  return 'Review';
}

/**
 * The "Spam Reason" column: why a row is not plain New. Every reason that
 * applies is kept, so a suspect inquiry whose label was also rejected says both.
 */
function spamReasonFor(screen, g) {
  if (screen && screen.spam) return screen.reason + (screen.detail !== null && screen.detail !== undefined ? ' (' + screen.detail + ')' : '');
  const parts = [];
  if (g && g.verdict === 'accept' && g.label === 'spam') parts.push('model: spam');
  const note = suspectNote(screen);
  if (note) parts.push('suspect: ' + note);
  if (g && g.verdict !== 'accept') parts.push('guard: ' + g.reason);
  return parts.join('; ');
}

module.exports = {
  INQUIRY_LABELS, SIGNATURE_MAX_AGE_MS, MIN_FILL_MS, MAX_FILL_MS, MIN_MESSAGE_CHARS,
  MAX_MESSAGE_CHARS, MAX_LINKS, MAX_FROM_CLIENT_10MIN,
  hmacHex, verifySignature, normalizeInquiry, screenSpam, guardLabel, decideStatus,
  suspectNote, spamReasonFor,
};

// --- CLI -------------------------------------------------------------------
// echo '[{"id":"..","op":"verify"|"screen"|"guard"|"status", ...}]' | node inquiry.js
if (require.main === module) {
  let buf = '';
  process.stdin.on('data', (d) => (buf += d));
  process.stdin.on('end', () => {
    const reqs = JSON.parse(buf);
    const res = (Array.isArray(reqs) ? reqs : [reqs]).map((r) => {
      let out;
      if (r.op === 'verify') out = verifySignature(r.secret, r.timestamp, r.payload, r.signature, r.now);
      else if (r.op === 'sign') out = { signature: hmacHex(r.secret, String(r.timestamp) + '.' + r.payload) };
      else if (r.op === 'normalize') out = normalizeInquiry(r.form);
      else if (r.op === 'screen') out = screenSpam(normalizeInquiry(r.form), r.now, r.recent || 0);
      else if (r.op === 'guard') out = guardLabel(r.llm_output, r.message);
      else if (r.op === 'status') out = { status: decideStatus(r.screen, r.guard) };
      else if (r.op === 'reason') out = { reason: spamReasonFor(r.screen, r.guard), suspect: suspectNote(r.screen) };
      else throw new Error('unknown op ' + r.op);
      return Object.assign({ id: r.id }, out);
    });
    process.stdout.write(JSON.stringify(res));
  });
}
