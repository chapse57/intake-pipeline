"""
Offline tier — T5, T6, T7 and the robustness cases around them.

Forcing a real model to emit a bad label on demand is not a test, it is a
coin flip. The model's output is the input to this node, so the cases in
data/guard_cases.json are the model outputs, and n8n/guard.js is the code
under test — the same file pasted into the n8n Code node.
"""
import collections

import pytest

pytestmark = pytest.mark.offline


@pytest.fixture(scope="module")
def results(run_guard, guard_cases):
    return {r["id"]: r for r in run_guard(guard_cases)}


def _cases_for(guard_cases, test_id):
    return [c for c in guard_cases if c["test"] == test_id]


@pytest.mark.T5
def test_role_outside_the_allowed_set_is_rejected(guard_cases, results):
    cases = _cases_for(guard_cases, "T5")
    assert cases, "no T5 cases in the fixture"
    for c in cases:
        r = results[c["id"]]
        assert r["verdict"] == "reject", c["id"]
        assert r["reason"] == "role_not_allowed", c["id"]
        assert r["role"] is None, "a rejected proposal must not leak a role downstream"
        assert r["detail"] == c["llm_output"]["role"], "the rejected value must be logged"


@pytest.mark.T6
def test_evidence_not_present_in_the_source_is_rejected(guard_cases, results):
    cases = _cases_for(guard_cases, "T6")
    assert cases, "no T6 cases in the fixture"
    for c in cases:
        r = results[c["id"]]
        assert r["verdict"] == "reject", c["id"]
        assert r["reason"] == c["expect"]["reason"], c["id"]
        assert r["reason"] in ("evidence_not_in_source", "evidence_too_long"), c["id"]


@pytest.mark.T6
def test_quote_length_cap_is_ratio_based_and_has_a_floor(run_guard):
    """
    A 40% cap on a 250-char source is 100 chars; on a 60-char source the ratio
    rule is skipped and only the absolute cap applies, so a short application
    can still be classified.
    """
    long_src = "x " * 125                             # 250 chars folded
    short_src = "I run the closing call and take payment on it."  # < 100 chars
    out = {r["id"]: r for r in run_guard([
        {"id": "under-cap", "application_text": long_src,
         "llm_output": {"role": "Setter", "evidence_quote": "x " * 45, "confidence": 0.9}},
        {"id": "over-cap", "application_text": long_src,
         "llm_output": {"role": "Setter", "evidence_quote": "x " * 60, "confidence": 0.9}},
        {"id": "short-source-whole-quote", "application_text": short_src,
         "llm_output": {"role": "Closer", "evidence_quote": short_src, "confidence": 0.9}},
    ])}
    assert out["under-cap"]["verdict"] == "accept"
    assert out["over-cap"]["reason"] == "evidence_too_long"
    assert out["over-cap"]["detail"]["ratio"] > 0.4
    assert out["short-source-whole-quote"]["verdict"] == "accept", \
        "ratio rule must not apply below RATIO_APPLIES_FROM"


@pytest.mark.T7
def test_low_confidence_routes_to_review_rather_than_rejecting(guard_cases, results):
    r = results["G-T7-low-confidence"]
    assert r["verdict"] == "review"
    assert r["reason"] == "below_confidence_floor"
    assert r["role"] == "Setter", "a Review row still carries the proposal for a human"
    assert r["confidence"] == 0.5


@pytest.mark.T7
def test_the_confidence_floor_is_inclusive(results):
    """Pinned so nobody turns 0.7 into a rejection by changing < to <=."""
    assert results["G-T7-at-floor"]["verdict"] == "accept"


@pytest.mark.T5
@pytest.mark.T6
def test_structural_failures_outrank_confidence(run_guard):
    """
    A bad role at 0.99 must still be rejected, and a good proposal at 0.4 must
    reach Review rather than being thrown away. Check order is behaviour.
    """
    src = "I run the closing call and take payment on the call itself, every week."
    out = {r["id"]: r for r in run_guard([
        {"id": "bad-role-high-conf", "application_text": src,
         "llm_output": {"role": "VP Sales", "evidence_quote": "I run the closing call",
                        "confidence": 0.99}},
        {"id": "good-role-low-conf", "application_text": src,
         "llm_output": {"role": "Closer", "evidence_quote": "I run the closing call",
                        "confidence": 0.4}},
    ])}
    assert out["bad-role-high-conf"]["verdict"] == "reject"
    assert out["good-role-low-conf"]["verdict"] == "review"


def test_every_fixture_case_lands_where_the_fixture_says(guard_cases, results):
    """The golden-file comparison: 13 cases, expected verdict and reason."""
    mismatches = [
        (c["id"], c["expect"], {"verdict": results[c["id"]]["verdict"],
                                "reason": results[c["id"]]["reason"]})
        for c in guard_cases
        if (results[c["id"]]["verdict"], results[c["id"]]["reason"])
        != (c["expect"]["verdict"], c["expect"]["reason"])
    ]
    assert not mismatches, mismatches


def test_rejection_rate_for_the_portfolio_number(guard_cases, results):
    """
    Emits the {N} of {M} figure quoted in the write-up, so the claim is
    regenerated from the suite instead of being retyped from memory.
    """
    counts = collections.Counter(results[c["id"]]["verdict"] for c in guard_cases)
    total = sum(counts.values())
    print(f"\nguard: {counts['reject']} of {total} model outputs rejected, "
          f"{counts['review']} routed to Review, {counts['accept']} accepted")
    assert counts["reject"] + counts["review"] + counts["accept"] == total
    assert counts["reject"] > 0
