/**
 * guard.js — WF-1 "guard" node. The deterministic check that stands between
 * the model's proposal and the Notion write.
 *
 * The model is asked for {role, evidence_quote, confidence}. Nothing it
 * returns is trusted: the role must be one of four strings, the quote must
 * actually occur in the application text, and the confidence must be a real
 * number in range. A rejected proposal is logged with its reason and the
 * candidate is left for a human — it is never written through with a
 * "best guess" role.
 *
 * Self-contained on purpose: an n8n Code node cannot `require` a sibling
 * file, so the copy pasted into n8n is byte-identical to the copy the tests
 * run. Dual-use like normalize.js.
 */

'use strict';

const ALLOWED_ROLES = ['Setter', 'Coach', 'Closer', 'Full Cycle'];
const CONFIDENCE_FLOOR = 0.7;
const MIN_EVIDENCE_CHARS = 12;

// Upper bound on the quote. Without one, "is the quote in the source?" is
// vacuous: a model that quotes the whole application always passes. The
// first live call did exactly that — two of four sentences, 85% of the text.
// A quote has to be a part that points at the role, not the document.
const MAX_EVIDENCE_CHARS = 300;
const MAX_EVIDENCE_RATIO = 0.4;   // of the folded source length
const RATIO_APPLIES_FROM = 100;   // sources shorter than this only get the char cap

/**
 * Fold the text just enough that quoting is robust to formatting but not so
 * much that a different sentence starts matching. Case, whitespace runs, and
 * the typographic variants word processors substitute are folded; word order
 * and word choice are not.
 */
function foldForQuote(s) {
  return String(s == null ? '' : s)
    .normalize('NFKC')
    .replace(/[‘’ʼ]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[‐-―]/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function quoteFoundIn(quote, source) {
  const q = foldForQuote(quote);
  const s = foldForQuote(source);
  if (!q) return false;
  return s.indexOf(q) !== -1;
}

function reject(reason, detail) {
  return { verdict: 'reject', role: null, evidence: null, confidence: null,
           reason, detail: detail === undefined ? null : detail };
}

/**
 * @param {object|string} llmOutput  parsed object, or the raw model string
 * @param {string} applicationText   the source the quote must come from
 * @returns {{verdict:'accept'|'review'|'reject', role, evidence, confidence,
 *            reason, detail}}
 */
function guard(llmOutput, applicationText) {
  // --- structure ---
  let out = llmOutput;
  if (typeof out === 'string') {
    try {
      out = JSON.parse(out);
    } catch (e) {
      return reject('not_json', String(out).slice(0, 200));
    }
  }
  if (out === null || typeof out !== 'object' || Array.isArray(out)) {
    return reject('not_an_object', typeof out);
  }

  const { role, evidence_quote: evidence, confidence } = out;

  // --- role must be one of four exact strings ---
  // No trimming, no case-folding, no "Closer " and no "closer". The prompt
  // states the four literals; anything else means the model improvised, and
  // silently repairing it would hide exactly the failure this node exists
  // to catch.
  if (typeof role !== 'string' || ALLOWED_ROLES.indexOf(role) === -1) {
    return reject('role_not_allowed', role === undefined ? null : role);
  }

  // --- evidence must be a real quote from the source ---
  if (typeof evidence !== 'string' || foldForQuote(evidence).length < MIN_EVIDENCE_CHARS) {
    return reject('evidence_missing_or_too_short',
                  evidence === undefined ? null : String(evidence).slice(0, 200));
  }
  const qLen = foldForQuote(evidence).length;
  const sLen = foldForQuote(applicationText).length;
  const ratio = sLen ? qLen / sLen : null;
  if (qLen > MAX_EVIDENCE_CHARS ||
      (sLen >= RATIO_APPLIES_FROM && qLen > MAX_EVIDENCE_RATIO * sLen)) {
    return reject('evidence_too_long',
                  { chars: qLen, ratio: ratio === null ? null : Math.round(ratio * 100) / 100 });
  }
  if (!quoteFoundIn(evidence, applicationText)) {
    return reject('evidence_not_in_source', String(evidence).slice(0, 200));
  }

  // --- confidence must be a usable number ---
  if (typeof confidence !== 'number' || !isFinite(confidence)) {
    return reject('confidence_not_a_number',
                  confidence === undefined ? null : String(confidence));
  }
  if (confidence < 0 || confidence > 1) {
    return reject('confidence_out_of_range', confidence);
  }

  // --- everything structural passed; only now does confidence route ---
  if (confidence < CONFIDENCE_FLOOR) {
    return { verdict: 'review', role, evidence, confidence,
             reason: 'below_confidence_floor', detail: CONFIDENCE_FLOOR };
  }

  return { verdict: 'accept', role, evidence, confidence,
           reason: 'ok', detail: null };
}

module.exports = { guard, foldForQuote, quoteFoundIn,
                   ALLOWED_ROLES, CONFIDENCE_FLOOR, MIN_EVIDENCE_CHARS,
                   MAX_EVIDENCE_CHARS, MAX_EVIDENCE_RATIO, RATIO_APPLIES_FROM };

// --- CLI -------------------------------------------------------------------
// node guard.js < cases.json
// cases.json: [{"id": "...", "llm_output": {...}, "application_text": "..."}]
// stdout:     [{"id": "...", ...guard result...}]
if (require.main === module) {
  let buf = '';
  process.stdin.on('data', (d) => (buf += d));
  process.stdin.on('end', () => {
    const cases = JSON.parse(buf);
    const results = (Array.isArray(cases) ? cases : [cases]).map((c) =>
      Object.assign({ id: c.id }, guard(c.llm_output, c.application_text))
    );
    process.stdout.write(JSON.stringify(results, null, 2));
  });
}

/* === n8n Code node (WF-1 "guard") ==========================================
   Paste everything above `module.exports` into the node, then append:

     const out = [];
     for (const item of $input.all()) {
       const r = guard(item.json.llm_output, item.json.application_text);
       out.push({ json: Object.assign({}, item.json, { guard: r }) });
     }
     return out;

   Do NOT branch on the verdict after this node. Every verdict goes on to
   create the Candidates row; the verdict only decides what that row says:
     accept -> Status = New, Role set
     review -> Status = Review, proposal kept so the reviewer sees it
     reject -> Status = Review, Role empty
   The guard rejects a label. Nothing downstream may reject a person. The
   Events row written right after this node carries guard.reason and
   guard.detail, so a rejected label stays auditable.
   ========================================================================= */
