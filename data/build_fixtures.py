"""
Generates data/candidates.csv, data/recruiters.csv and data/guard_cases.json,
then re-derives every expected_* column by running the real n8n dedupe code
(n8n/normalize.js) over the roster. If the generator's expectations and the
node's behaviour ever disagree, this script fails instead of writing a
golden file that quietly encodes the wrong answer.

    python3 data/build_fixtures.py
"""
import csv, json, subprocess, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
NORMALIZE_JS = ROOT / "n8n" / "normalize.js"

RECRUITERS = [
    ("Ana Duarte",    "ana.duarte@intake.demo"),
    ("Ben Kowalski",  "ben.kowalski@intake.demo"),
    ("Chidi Nwosu",   "chidi.nwosu@intake.demo"),
    ("Dana Reyes",    "dana.reyes@intake.demo"),
]

# Application text carries one clearly quotable clause per role. The guard
# checks the model's quote against this exact string, so it has to read like
# something a person would actually write, not like a keyword list.
# Application text carries one clearly quotable clause per role, buried in a
# realistic amount of surrounding prose (~500 chars). The length matters: the
# guard's 40% ratio cap only means something when the application is long
# enough that a one-clause quote is a small fraction of it. With 200-char
# texts the model's natural 20-word quote is already half the document.
APP = {
    "setter": (
        "I came into sales through a customer-support desk, which is where I learned that most people "
        "who say no are really saying not yet. Most of my week is cold outreach. {q}, and I hand every "
        "qualified one to a closer with notes on what the prospect said, what they objected to and what "
        "they seemed to want. I keep my own tracking sheet because the CRM never quite matches how I work. "
        "I have never run the money conversation myself and I am not trying to; I like the front of the "
        "funnel and I am good at it. Available from the first of next month, remote or hybrid."
    ),
    "closer": (
        "Eight years in high-ticket coaching sales, the last three of them fully remote. I take over "
        "once a call is booked. {q}, and last year I carried a personal quota of 1.2M with a 31 percent "
        "close rate on qualified opportunities. I do not prospect and I am not interested in roles where "
        "closers are expected to fill their own calendar; that is a different job. What I bring is the "
        "conversation at the end - handling the objection about money, the objection about time, and the "
        "one about the spouse. References from two previous programme owners available on request."
    ),
    "coach": (
        "Former client, then head coach. My work starts after the sale, and I think that is where the "
        "reputation of a programme is actually made. {q}, and I run the weekly accountability calls that "
        "keep them from churning in month two. I have done this for cohorts of forty and for one-to-one "
        "premium tiers, and I am comfortable with both. I do not sell, and I would rather not be on "
        "sales calls even as the 'meet your coach' guest. Looking for a programme that treats delivery "
        "as the product rather than as the thing that happens after the product is sold."
    ),
    "full": (
        "I have run my own coaching offer for four years and I am looking to fold it into a larger "
        "operation. I work the whole funnel alone. {q}, which means I am prospecting on Monday and taking "
        "payment on Friday without handing anyone off. That has kept my numbers honest but it does not "
        "scale, and I would rather bring the whole loop to a team than keep doing all of it myself. I am "
        "equally happy in any single part of it, but the truth is I am strongest when I own the outcome "
        "end to end. Open to commission-heavy structures."
    ),
}
QUOTE = {
    "setter": "I book between fifteen and twenty discovery calls a week from cold lists",
    "closer": "I run the closing call and take payment on the call itself",
    "coach":  "I deliver the twelve-week onboarding programme to clients one to one",
    "full":   "I source my own leads, book them, close them and then onboard them",
}
def app(kind):
    return APP[kind].format(q=QUOTE[kind])

ROLE_OF = {"setter": "Setter", "closer": "Closer", "coach": "Coach", "full": "Full Cycle"}

# (sid, recruiter_idx, name, email, phone, country, kind, expect, expect_role, note)
NEW, EXACT, FUZZY = "created", "duplicate_flagged", "created_review"

ROSTER = [
    # --- 14 submissions that must produce a row ---------------------------
    ("S01", 0, "Marcus Webb",        "marcus.webb@northline.io",              "+1 415 555 0142",   "US", "setter", NEW,   "Setter",     ""),
    ("S02", 0, "Priya Raghunathan",  "priya.r@meridiangroup.com",             "+44 20 7946 0318",  "GB", "closer", NEW,   "Closer",     ""),
    ("S03", 0, "Daniel Okafor",      "d.okafor@bluecrestpartners.com",        "+1 646 555 0177",   "US", "coach",  NEW,   "Coach",      "T11 baseline: attributed to Ana"),
    ("S04", 1, "Sofia Lindqvist",    "sofia.l@harborlight.se",                "+46 8 123 456 78",  "SE", "full",   NEW,   "Full Cycle", "T3 baseline"),
    ("S05", 1, "James Whitaker",     "jwhitaker@northline.io",                "+1 312 555 0193",   "US", "closer", NEW,   "Closer",     "shares a domain with S01, different person"),
    ("S06", 1, "Aisha Bello",        "aisha.bello@lagosworks.ng",             "+234 802 555 0119", "NG", "setter", NEW,   "Setter",     ""),
    ("S07", 2, "Tomás Herrera",      "t.herrera@andesreach.cl",               "+56 2 2555 0184",   "CL", "coach",  NEW,   "Coach",      "diacritic in name"),
    ("S08", 2, "Grace Lim",          "grace.lim@pacificrim.sg",               "+65 6555 0136",     "SG", "setter", NEW,   "Setter",     ""),
    ("S09", 2, "Nathan Cole",        "nathan.cole@bluecrestpartners.com",     "+1 617 555 0158",   "US", "full",   NEW,   "Full Cycle", "near-miss baseline"),
    ("S10", 3, "Yuki Tanaka",        "yuki.tanaka@kaigan.jp",                 "+81 3 5555 0172",   "JP", "closer", NEW,   "Closer",     ""),
    ("S11", 3, "Jiwon Park",         "jiwon.park@hanulsoft.kr",               "010-3456-7890",     "KR", "coach",  NEW,   "Coach",      "T2 baseline, national format"),
    ("S12", 3, "Sophia Lindqvist",   "sophia.lindqvist@harborlight.se",       "+46 8 987 654 32",  "SE", "full",   FUZZY, "Full Cycle", "T3: name 0.875 vs S04 on the same domain -> Review, not merged"),
    ("S13", 0, "Nathaniel Cole",     "nathaniel.cole@bluecrestpartners.com",  "+1 617 555 0201",   "US", "closer", NEW,   "Closer",     "control: 0.786 vs S09 on the same domain, below threshold -> plain New"),
    ("S14", 1, "Grace Lin",          "grace.lin@tasman.nz",                   "+64 9 555 0147",    "NZ", "setter", NEW,   "Setter",     "control: 0.889 vs S08 but a different domain -> plain New"),

    # --- 6 submissions that must NOT produce a row ------------------------
    ("S15", 1, "Marcus Webb",        "  MARCUS.WEBB@NORTHLINE.IO ",           "+1 415 555 0142",   "US", "setter", EXACT, "",           "T1: case + surrounding whitespace"),
    ("S16", 2, "Priya Raghunathan",  "priya.raghunathan@meridiangroup.com",   "(020) 7946 0318",   "GB", "closer", EXACT, "",           "T2: different email, same phone in national format"),
    ("S17", 1, "Daniel Okafor",      "D.Okafor@BlueCrestPartners.com",        "+1 646 555 0177",   "US", "coach",  EXACT, "",           "T11: Ben re-submits Ana's candidate"),
    ("S18", 2, "Jiwon Park",         "jw.park@hanulsoft.kr",                  "+82 10 3456 7890",  "KR", "coach",  EXACT, "",           "T2: KR national vs E.164"),
    ("S19", 3, "Grace Lim",          "grace.lim@pacificrim.sg",               "+65 6555 0136",     "SG", "setter", EXACT, "",           "T1: byte-identical resubmission"),
    ("S20", 0, "Yuki Tanaka",        "Yuki.Tanaka@Kaigan.JP",                 "+81 3 5555 0172",   "JP", "closer", EXACT, "",           "T1: mixed case in the domain too"),
]

def node_classify(incoming, existing):
    req = json.dumps({"op": "classify", "incoming": incoming, "existing": existing})
    p = subprocess.run([ "node", str(NORMALIZE_JS) ], input=req, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)

def node_normalize(incoming):
    req = json.dumps({"op": "normalize", "incoming": incoming})
    p = subprocess.run([ "node", str(NORMALIZE_JS) ], input=req, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)

def main():
    rows, errors = [], []
    store = []  # rows the pipeline would have created so far

    for sid, ridx, name, email, phone, cc, kind, expect, exp_role, note in ROSTER:
        sub = {
            "name": name, "email": email, "phone": phone, "country_hint": cc,
            "application_text": app(kind),
            "recruiter_email": RECRUITERS[ridx][1],
        }
        verdict = node_classify(sub, store)
        got = {"new": NEW, "fuzzy": FUZZY, "exact": EXACT}[verdict["verdict"]]
        if got != expect:
            errors.append(f"{sid} {name}: roster says {expect}, normalize.js says {got} ({verdict['reason']})")
        if verdict["verdict"] != "exact":
            n = node_normalize(sub)
            n["id"] = sid
            n["name"] = name
            store.append(n)

        rows.append({
            "submission_id": sid,
            "recruiter_name": RECRUITERS[ridx][0],
            "recruiter_email": RECRUITERS[ridx][1],
            "name": name,
            "email": email,
            "phone": phone,
            "country_hint": cc,
            "application_text": sub["application_text"],
            "expected_outcome": expect,
            "expected_role": exp_role,
            "expected_duplicate_of": verdict["matchId"] or "",
            "expected_match_on": verdict["on"] or "",
            "expected_similarity": f"{verdict['score']:.4f}" if verdict["verdict"] == "fuzzy" else "",
            "note": note,
        })

    if errors:
        print("FIXTURE DISAGREES WITH normalize.js:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        sys.exit(1)

    with open(ROOT / "data" / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    with open(ROOT / "data" / "recruiters.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "email"])
        w.writerows(RECRUITERS)

    # --- guard cases: what the model returns, and what must happen to it ---
    setter_src = app("setter")
    closer_src = app("closer")
    guard_cases = [
        {"id": "G-accept-setter", "test": "T-guard-baseline",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": QUOTE["setter"], "confidence": 0.93},
         "expect": {"verdict": "accept", "reason": "ok"}},

        {"id": "G-accept-quote-reflowed", "test": "T-guard-baseline",
         "application_text": setter_src,
         "llm_output": {"role": "Setter",
                        "evidence_quote": "I book between fifteen   and twenty\n discovery calls a week",
                        "confidence": 0.81},
         "expect": {"verdict": "accept", "reason": "ok"},
         "why": "whitespace and line breaks are folded; the words are unchanged"},

        {"id": "G-T5-role-manager", "test": "T5",
         "application_text": closer_src,
         "llm_output": {"role": "Manager", "evidence_quote": QUOTE["closer"], "confidence": 0.96},
         "expect": {"verdict": "reject", "reason": "role_not_allowed"},
         "why": "a confident, well-evidenced answer outside the vocabulary is still rejected"},

        {"id": "G-T5-role-case", "test": "T5",
         "application_text": closer_src,
         "llm_output": {"role": "closer", "evidence_quote": QUOTE["closer"], "confidence": 0.9},
         "expect": {"verdict": "reject", "reason": "role_not_allowed"},
         "why": "no silent case repair — Notion's Select would create a second option"},

        {"id": "G-T6-quote-invented", "test": "T6",
         "application_text": closer_src,
         "llm_output": {"role": "Closer",
                        "evidence_quote": "I have consistently exceeded my targets in a closing capacity",
                        "confidence": 0.94},
         "expect": {"verdict": "reject", "reason": "evidence_not_in_source"},
         "why": "plausible, on-topic, and nowhere in the application"},

        {"id": "G-T6-quote-paraphrased", "test": "T6",
         "application_text": closer_src,
         "llm_output": {"role": "Closer",
                        "evidence_quote": "I run closing calls and take payment during the call",
                        "confidence": 0.9},
         "expect": {"verdict": "reject", "reason": "evidence_not_in_source"},
         "why": "a paraphrase of a real sentence is not a quote"},

        {"id": "G-T6-quote-is-the-whole-text", "test": "T6",
         "application_text": closer_src,
         "llm_output": {"role": "Closer", "evidence_quote": closer_src, "confidence": 0.97},
         "expect": {"verdict": "reject", "reason": "evidence_too_long"},
         "why": "quoting the whole document is in the source by definition; that proves nothing"},

        {"id": "G-T6-quote-is-most-of-the-text", "test": "T6",
         "application_text": closer_src,
         "llm_output": {"role": "Closer",
                        "evidence_quote": closer_src[: int(len(closer_src) * 0.55)],
                        "confidence": 0.98},
         "expect": {"verdict": "reject", "reason": "evidence_too_long"},
         "why": "over half the document is not a quote, whatever the model thinks"},

        {"id": "G-accept-two-sentence-quote-in-long-text", "test": "T-guard-baseline",
         "application_text": closer_src,
         "llm_output": {"role": "Closer",
                        "evidence_quote": "I take over once a call is booked. " + QUOTE["closer"],
                        "confidence": 0.95},
         "expect": {"verdict": "accept", "reason": "ok"},
         "why": "what Haiku actually returns for this text: ~20 words. Fine in a 500-char application, "
                "a problem only when the application itself is two sentences"},

        {"id": "G-T7-low-confidence", "test": "T7",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": QUOTE["setter"], "confidence": 0.5},
         "expect": {"verdict": "review", "reason": "below_confidence_floor"}},

        {"id": "G-T7-at-floor", "test": "T7",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": QUOTE["setter"], "confidence": 0.7},
         "expect": {"verdict": "accept", "reason": "ok"},
         "why": "the floor is inclusive; pinned so the boundary cannot drift"},

        {"id": "G-malformed-string", "test": "T-guard-robustness",
         "application_text": setter_src,
         "llm_output": "Sure! Here is the JSON you asked for: {\"role\": \"Setter\"}",
         "expect": {"verdict": "reject", "reason": "not_json"},
         "why": "the model prefixing prose to its JSON must not crash the node"},

        {"id": "G-confidence-string", "test": "T-guard-robustness",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": QUOTE["setter"], "confidence": "0.95"},
         "expect": {"verdict": "reject", "reason": "confidence_not_a_number"},
         "why": "'0.95' < 0.7 is false in JS for the wrong reason; typed explicitly instead"},

        {"id": "G-confidence-out-of-range", "test": "T-guard-robustness",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": QUOTE["setter"], "confidence": 12},
         "expect": {"verdict": "reject", "reason": "confidence_out_of_range"}},

        {"id": "G-evidence-empty", "test": "T-guard-robustness",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": "", "confidence": 0.99},
         "expect": {"verdict": "reject", "reason": "evidence_missing_or_too_short"}},

        {"id": "G-evidence-single-word", "test": "T-guard-robustness",
         "application_text": setter_src,
         "llm_output": {"role": "Setter", "evidence_quote": "cold", "confidence": 0.99},
         "expect": {"verdict": "reject", "reason": "evidence_missing_or_too_short"},
         "why": "a word that happens to appear is not evidence"},
    ]
    with open(ROOT / "data" / "guard_cases.json", "w", encoding="utf-8") as f:
        json.dump(guard_cases, f, indent=2, ensure_ascii=False)
        f.write("\n")

    created = sum(1 for r in rows if r["expected_outcome"] != EXACT)
    flagged = sum(1 for r in rows if r["expected_outcome"] == EXACT)
    review  = sum(1 for r in rows if r["expected_outcome"] == FUZZY)
    print(f"candidates.csv : {len(rows)} submissions -> {created} created "
          f"({review} of them Review), {flagged} flagged, 0 lost")
    print(f"guard_cases.json : {len(guard_cases)} cases")

if __name__ == "__main__":
    main()
