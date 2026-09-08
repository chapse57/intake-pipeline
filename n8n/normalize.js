/**
 * normalize.js — WF-1 "normalize" + "dedupe" nodes.
 *
 * Dual-use: `require()` it from tests, or paste the marked block into an
 * n8n Code node. No dependencies, so the pasted copy behaves identically to
 * the copy the test suite exercises. There is exactly one implementation of
 * these rules and the tests run it, not a Python restatement of it.
 */

'use strict';

// Trunk-prefix + country-code table. Deliberately small and explicit:
// libphonenumber cannot be installed into an n8n Code node, and a short
// table that fails loudly on an unknown region beats a regex that silently
// produces a wrong E.164 string.
const REGIONS = {
  US: { cc: '1',   trunk: null }, // NANP has no trunk prefix
  CA: { cc: '1',   trunk: null },
  GB: { cc: '44',  trunk: '0' },
  IE: { cc: '353', trunk: '0' },
  KR: { cc: '82',  trunk: '0' },
  JP: { cc: '81',  trunk: '0' },
  SE: { cc: '46',  trunk: '0' },
  SG: { cc: '65',  trunk: null },
  NZ: { cc: '64',  trunk: '0' },
  NG: { cc: '234', trunk: '0' },
  CL: { cc: '56',  trunk: '0' },
  GH: { cc: '233', trunk: '0' },
};

const ALLOWED_ROLES = ['Setter', 'Coach', 'Closer', 'Full Cycle'];

/** Lowercase + trim. Nothing else. */
function normalizeEmail(raw) {
  if (raw == null) return '';
  return String(raw).trim().toLowerCase();
}

/**
 * Note on what is NOT done here: Gmail's dot-and-plus aliasing is not
 * collapsed. Treating a.b@gmail.com and ab@gmail.com as the same person is
 * true for Gmail and false for most other hosts, and a dedupe rule that is
 * right only for one provider is a rule that merges two real candidates
 * somewhere else. Case and whitespace are universally safe; aliasing is not.
 */

function emailDomain(raw) {
  const e = normalizeEmail(raw);
  const at = e.lastIndexOf('@');
  return at === -1 ? '' : e.slice(at + 1);
}

/**
 * Convert a phone number to E.164. Returns '' when the input cannot be
 * normalized with confidence — an empty key never matches, so an
 * unparseable number degrades to "not a duplicate" rather than to a
 * false merge.
 */
function normalizePhone(raw, defaultRegion) {
  if (raw == null) return '';
  const s = String(raw).trim();
  if (!s) return '';

  const hasPlus = s.startsWith('+') || s.startsWith('00');
  let digits = s.replace(/\D/g, '');
  if (s.startsWith('00')) digits = digits.slice(2);

  if (hasPlus) {
    return digits.length >= 8 && digits.length <= 15 ? '+' + digits : '';
  }

  const region = REGIONS[String(defaultRegion || '').toUpperCase()];
  if (!region) return '';

  if (region.trunk && digits.startsWith(region.trunk)) {
    digits = digits.slice(region.trunk.length);
  }
  const out = region.cc + digits;
  return out.length >= 8 && out.length <= 15 ? '+' + out : '';
}

/** Lowercase, strip diacritics and punctuation, collapse whitespace. */
function normalizeName(raw) {
  if (raw == null) return '';
  return String(raw)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function levenshtein(a, b) {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    const cur = new Array(n + 1);
    cur[0] = i;
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)
      );
    }
    prev = cur;
  }
  return prev[n];
}

/**
 * Normalized Levenshtein similarity in [0, 1].
 * Chosen over difflib/SequenceMatcher because the number has to mean the
 * same thing in the n8n node and in the test assertions; edit distance is
 * trivially reimplementable and order-independent, gestalt matching is not.
 */
function nameSimilarity(a, b) {
  const x = normalizeName(a), y = normalizeName(b);
  if (!x && !y) return 1;
  if (!x || !y) return 0;
  if (x === y) return 1;
  return 1 - levenshtein(x, y) / Math.max(x.length, y.length);
}

/** Build the stored dedupe keys written to the Candidates row. */
function normalizeSubmission(sub) {
  const email = normalizeEmail(sub.email);
  return {
    name: String(sub.name == null ? '' : sub.name).trim(),
    email_raw: sub.email,
    email_norm: email,
    email_domain: emailDomain(email),
    phone_raw: sub.phone,
    phone_e164: normalizePhone(sub.phone, sub.country_hint),
    application_text: sub.application_text == null ? '' : String(sub.application_text),
    recruiter_email: normalizeEmail(sub.recruiter_email),
  };
}

const FUZZY_NAME_THRESHOLD = 0.8;

/**
 * Decide what to do with an incoming submission against existing rows.
 *
 *   exact  -> do not create a row; increment Duplicate Attempts on the match
 *   fuzzy  -> create a row with Status=Review and Duplicate Of -> match,
 *             because a same-domain near-name is as often two siblings at one
 *             company as it is one person typed twice. A human decides.
 *   new    -> create a row with Status=New
 *
 * `existing` rows must already carry email_norm / phone_e164 / email_domain.
 */
function classifyDuplicate(incoming, existing) {
  const inc = normalizeSubmission(incoming);

  for (const row of existing) {
    if (inc.email_norm && row.email_norm && inc.email_norm === row.email_norm) {
      return { verdict: 'exact', matchId: row.id, on: 'email',
               reason: `email_norm == ${inc.email_norm}`, score: 1 };
    }
  }
  for (const row of existing) {
    if (inc.phone_e164 && row.phone_e164 && inc.phone_e164 === row.phone_e164) {
      return { verdict: 'exact', matchId: row.id, on: 'phone',
               reason: `phone_e164 == ${inc.phone_e164}`, score: 1 };
    }
  }

  let best = null;
  for (const row of existing) {
    if (!inc.email_domain || inc.email_domain !== row.email_domain) continue;
    const score = nameSimilarity(inc.name, row.name);
    if (score >= FUZZY_NAME_THRESHOLD && (!best || score > best.score)) {
      best = { verdict: 'fuzzy', matchId: row.id, on: 'name+domain',
               reason: `name similarity ${score.toFixed(4)} >= ${FUZZY_NAME_THRESHOLD} on domain ${inc.email_domain}`,
               score };
    }
  }
  if (best) return best;

  return { verdict: 'new', matchId: null, on: null, reason: 'no match', score: 0 };
}

module.exports = {
  REGIONS, ALLOWED_ROLES, FUZZY_NAME_THRESHOLD,
  normalizeEmail, emailDomain, normalizePhone, normalizeName,
  levenshtein, nameSimilarity, normalizeSubmission, classifyDuplicate,
};

// --- CLI -------------------------------------------------------------------
// echo '{"op":"classify","incoming":{...},"existing":[...]}' | node normalize.js
// echo '{"op":"normalize","incoming":{...}}'                 | node normalize.js
if (require.main === module) {
  let buf = '';
  process.stdin.on('data', (d) => (buf += d));
  process.stdin.on('end', () => {
    const req = JSON.parse(buf);
    const out = req.op === 'classify'
      ? classifyDuplicate(req.incoming, req.existing || [])
      : normalizeSubmission(req.incoming);
    process.stdout.write(JSON.stringify(out));
  });
}

/* === n8n Code node (WF-1 "normalize") ======================================
   Paste everything above `module.exports` into the node, then append:

     const out = [];
     for (const item of $input.all()) {
       out.push({ json: normalizeSubmission(item.json) });
     }
     return out;

   The "dedupe" node runs classifyDuplicate(incoming, existingRows) where
   existingRows comes from the preceding Notion query node.
   ========================================================================= */
