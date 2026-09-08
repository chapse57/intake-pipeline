"""
Offline tier — the dedupe rules themselves, run through n8n/normalize.js.

These are the assertions that make T1/T2/T3 mean something. The integration
tests prove the workflow is wired to this logic; these prove the logic is
right, and they run without a single credential.
"""
import pytest

pytestmark = pytest.mark.offline


# --- T1: email ------------------------------------------------------------
@pytest.mark.T1
@pytest.mark.parametrize("a,b", [
    ("marcus.webb@northline.io", "  MARCUS.WEBB@NORTHLINE.IO "),
    ("yuki.tanaka@kaigan.jp", "Yuki.Tanaka@Kaigan.JP"),
    ("d.okafor@bluecrestpartners.com", "D.Okafor@BlueCrestPartners.com"),
])
def test_email_case_and_whitespace_collapse(normalize, a, b):
    assert normalize({"email": a})["email_norm"] == normalize({"email": b})["email_norm"]


@pytest.mark.T1
def test_gmail_dot_aliasing_is_not_collapsed(normalize):
    """
    Documented non-behaviour. Gmail treats a.b@ and ab@ as one mailbox; almost
    nothing else does. Collapsing dots would merge two different real people
    on every other host, which is the one failure this pipeline must not have.
    """
    assert (normalize({"email": "a.b@gmail.com"})["email_norm"]
            != normalize({"email": "ab@gmail.com"})["email_norm"])


# --- T2: phone ------------------------------------------------------------
@pytest.mark.T2
@pytest.mark.parametrize("raw,region,expected", [
    ("010-3456-7890",     "KR", "+821034567890"),
    ("+82 10 3456 7890",  "KR", "+821034567890"),
    ("0082 10 3456 7890", "KR", "+821034567890"),
    ("(020) 7946 0318",   "GB", "+442079460318"),
    ("+44 20 7946 0318",  "GB", "+442079460318"),
    ("020 7946 0318",     "GB", "+442079460318"),
    ("+1 (415) 555-0142", "US", "+14155550142"),
    ("415.555.0142",      "US", "+14155550142"),
])
def test_phone_reaches_e164(normalize, raw, region, expected):
    assert normalize({"phone": raw, "country_hint": region})["phone_e164"] == expected


@pytest.mark.T2
@pytest.mark.parametrize("raw,region", [
    ("ask me", "US"),
    ("", "US"),
    ("555-0142", "ZZ"),      # unknown region
    ("+1", "US"),            # too short to be anything
])
def test_unparseable_phone_becomes_empty_not_a_guess(normalize, raw, region):
    """
    An empty key never matches, so a number we cannot read degrades to 'not a
    duplicate'. A wrong E.164 string would silently merge two people instead.
    """
    assert normalize({"phone": raw, "country_hint": region})["phone_e164"] == ""


@pytest.mark.T2
def test_empty_keys_never_match_each_other(classify):
    existing = [{"id": "X", "name": "Someone Else",
                 "email_norm": "someone@else.com", "email_domain": "else.com",
                 "phone_e164": ""}]
    incoming = {"name": "Nobody Related", "email": "nobody@other.com",
                "phone": "ask me", "country_hint": "US"}
    assert classify(incoming, existing)["verdict"] == "new"


# --- T3: fuzzy name on a shared domain ------------------------------------
def _row(rid, name, email, domain, phone=""):
    return {"id": rid, "name": name, "email_norm": email,
            "email_domain": domain, "phone_e164": phone}


@pytest.mark.T3
def test_similar_name_same_domain_goes_to_review_not_merge(classify):
    existing = [_row("S04", "Sofia Lindqvist", "sofia.l@harborlight.se", "harborlight.se")]
    incoming = {"name": "Sophia Lindqvist", "email": "sophia.lindqvist@harborlight.se",
                "phone": "+46 8 987 654 32", "country_hint": "SE"}
    v = classify(incoming, existing)
    assert v["verdict"] == "fuzzy"
    assert v["matchId"] == "S04"
    assert v["score"] == pytest.approx(0.875, abs=0.001)


@pytest.mark.T3
def test_below_threshold_on_same_domain_is_a_plain_new_row(classify):
    """0.786 — Nathan and Nathaniel Cole are two people at one firm."""
    existing = [_row("S09", "Nathan Cole", "nathan.cole@bluecrestpartners.com",
                     "bluecrestpartners.com")]
    incoming = {"name": "Nathaniel Cole", "email": "nathaniel.cole@bluecrestpartners.com",
                "phone": "+1 617 555 0201", "country_hint": "US"}
    assert classify(incoming, existing)["verdict"] == "new"


@pytest.mark.T3
def test_similar_name_different_domain_is_a_plain_new_row(classify):
    """
    0.889 on the name alone, and still not a match. The domain condition is
    load-bearing: without it every Grace Lin on earth collides with Grace Lim.
    """
    existing = [_row("S08", "Grace Lim", "grace.lim@pacificrim.sg", "pacificrim.sg")]
    incoming = {"name": "Grace Lin", "email": "grace.lin@tasman.nz",
                "phone": "+64 9 555 0147", "country_hint": "NZ"}
    assert classify(incoming, existing)["verdict"] == "new"


@pytest.mark.T3
def test_diacritics_do_not_hide_an_identical_name(classify):
    existing = [_row("S07", "Tomás Herrera", "t.herrera@andesreach.cl", "andesreach.cl")]
    incoming = {"name": "Tomas Herrera", "email": "tomas.herrera@andesreach.cl",
                "phone": "+56 2 2555 0999", "country_hint": "CL"}
    v = classify(incoming, existing)
    assert v["verdict"] == "fuzzy" and v["score"] == 1.0


@pytest.mark.T3
def test_exact_email_wins_over_a_fuzzy_name(classify):
    """Ordering matters: an exact key must never be reported as a near-match."""
    existing = [
        _row("A", "Sophia Lindqvist", "sofia.l@harborlight.se", "harborlight.se"),
        _row("B", "Sofia Lindqvist", "other@harborlight.se", "harborlight.se"),
    ]
    incoming = {"name": "Sofia Lindqvist", "email": "SOFIA.L@harborlight.se",
                "phone": "", "country_hint": "SE"}
    v = classify(incoming, existing)
    assert v["verdict"] == "exact" and v["on"] == "email" and v["matchId"] == "A"
