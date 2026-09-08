"""
Integration tier — WF-1 against a live n8n and Notion.

The offline tests prove the dedupe rules are right. These prove the workflow
is actually wired to them, and that the Notion side effects are what the
schema promises: a flagged duplicate never creates a second row, and never
moves attribution.
"""
import pytest

from conftest import prop

pytestmark = pytest.mark.integration


def _by_email(notion, db, email):
    rows = notion.query(db["candidates"], {
        "property": "Email Norm", "rich_text": {"equals": email.strip().lower()}
    })
    return rows[0] if rows else None


def _row(roster, sid):
    return next(r for r in roster if r["submission_id"] == sid)


def _events(notion, db, step=None, result=None):
    f = {"and": []}
    if step:
        f["and"].append({"property": "Step", "select": {"equals": step}})
    if result:
        f["and"].append({"property": "Result", "select": {"equals": result}})
    return notion.query(db["events"], f if f["and"] else None)


@pytest.mark.T4
def test_new_candidate_is_created_and_attributed(clean, submit, notion, db,
                                                 eventually, roster):
    row = _row(roster, "S01")
    submit(row)

    page = eventually(lambda: _by_email(notion, db, row["email"]),
                      what="S01 to appear in Candidates")
    assert prop(page, "Status") in ("New", "Review", "Approved")
    assert prop(page, "Email Norm") == "marcus.webb@northline.io"
    assert prop(page, "Phone E164") == "+14155550142"
    assert prop(page, "Email Domain") == "northline.io"
    assert prop(page, "Recruiter"), "the submitting recruiter must be linked"
    assert prop(page, "Duplicate Attempts") in (None, 0)

    steps = {prop(e, "Step") for e in _events(notion, db)}
    assert {"normalize", "dedupe", "notion-create"} <= steps


@pytest.mark.T1
def test_same_email_different_case_is_flagged_not_duplicated(clean, submit, notion,
                                                             db, eventually, roster):
    submit(_row(roster, "S01"))
    eventually(lambda: _by_email(notion, db, "marcus.webb@northline.io"),
               what="the original Marcus Webb row")

    submit(_row(roster, "S15"))  # "  MARCUS.WEBB@NORTHLINE.IO "

    page = eventually(
        lambda: (lambda p: p if p and prop(p, "Duplicate Attempts") == 1 else None)(
            _by_email(notion, db, "marcus.webb@northline.io")),
        what="Duplicate Attempts to reach 1")

    all_marcus = notion.query(db["candidates"], {
        "property": "Email Norm",
        "rich_text": {"equals": "marcus.webb@northline.io"}})
    assert len(all_marcus) == 1, "the duplicate must not create a second row"

    dedupe = _events(notion, db, step="dedupe", result="rejected")
    assert dedupe, "the rejected attempt must be logged"
    assert any("email" in (prop(e, "Detail") or "") for e in dedupe)


@pytest.mark.T2
def test_same_phone_different_format_is_flagged(clean, submit, notion, db,
                                                eventually, roster):
    """
    S11 submits 010-3456-7890, S18 submits +82 10 3456 7890 under a different
    email address. Only the normalized phone connects them.
    """
    submit(_row(roster, "S11"))
    eventually(lambda: _by_email(notion, db, "jiwon.park@hanulsoft.kr"),
               what="the original Jiwon Park row")

    submit(_row(roster, "S18"))

    page = eventually(
        lambda: (lambda p: p if p and prop(p, "Duplicate Attempts") == 1 else None)(
            _by_email(notion, db, "jiwon.park@hanulsoft.kr")),
        what="the phone duplicate to be flagged")
    assert prop(page, "Phone E164") == "+821034567890"

    assert _by_email(notion, db, "jw.park@hanulsoft.kr") is None, \
        "the second email address must not have produced its own row"


@pytest.mark.T3
def test_similar_name_same_domain_lands_in_review_linked_to_the_original(
        clean, submit, notion, db, eventually, roster):
    submit(_row(roster, "S04"))
    original = eventually(lambda: _by_email(notion, db, "sofia.l@harborlight.se"),
                          what="Sofia Lindqvist")

    submit(_row(roster, "S12"))
    review = eventually(lambda: _by_email(notion, db, "sophia.lindqvist@harborlight.se"),
                        what="Sophia Lindqvist")

    assert prop(review, "Status") == "Review", "a near match is a human decision"
    assert original["id"] in (prop(review, "Duplicate Of") or []), \
        "the Review row must point at what it might be a duplicate of"
    assert prop(original, "Duplicate Attempts") in (None, 0), \
        "a fuzzy match is not a duplicate attempt; nothing was merged"


@pytest.mark.T3
def test_below_threshold_neighbour_is_not_sent_to_review(clean, submit, notion, db,
                                                         eventually, roster):
    """The control for the test above: same domain, 0.786, must pass straight through."""
    submit(_row(roster, "S09"))
    eventually(lambda: _by_email(notion, db, "nathan.cole@bluecrestpartners.com"),
               what="Nathan Cole")

    submit(_row(roster, "S13"))
    page = eventually(lambda: _by_email(notion, db, "nathaniel.cole@bluecrestpartners.com"),
                      what="Nathaniel Cole")
    assert prop(page, "Status") != "Review"
    assert not prop(page, "Duplicate Of")


@pytest.mark.T11
def test_a_second_recruiter_cannot_take_over_attribution(clean, submit, notion, db,
                                                         eventually, roster):
    """
    Ana submits Daniel Okafor; Ben submits the same person with different
    capitalisation. Ana keeps the candidate. Ben's attempt is recorded but
    earns nothing — that is the whole point of the attribution rule.
    """
    ana, ben = _row(roster, "S03"), _row(roster, "S17")
    assert ana["recruiter_email"] != ben["recruiter_email"]

    submit(ana)
    page = eventually(lambda: _by_email(notion, db, "d.okafor@bluecrestpartners.com"),
                      what="Daniel Okafor")
    owner_before = prop(page, "Recruiter")
    submitted_by_before = prop(page, "Submitted By")
    assert owner_before

    submit(ben)
    page = eventually(
        lambda: (lambda p: p if p and prop(p, "Duplicate Attempts") == 1 else None)(
            _by_email(notion, db, "d.okafor@bluecrestpartners.com")),
        what="Ben's attempt to be counted")

    assert prop(page, "Recruiter") == owner_before, "attribution moved"
    assert prop(page, "Submitted By") == submitted_by_before, "Created by changed"

    logged = _events(notion, db, step="dedupe", result="rejected")
    assert any(ben["recruiter_email"] in (prop(e, "Detail") or "") for e in logged), \
        "the losing recruiter's attempt must still be visible in the log"
