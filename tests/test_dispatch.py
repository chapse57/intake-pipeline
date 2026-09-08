"""
Integration tier — WF-2 (dispatch) and WF-3 (completion).

T9 is the one that matters most here. DocuSign Connect retries on any
non-2xx and will happily deliver the same completion twice; a pipeline that
creates a vendor record per delivery quietly duplicates suppliers in the
accounting system. The idempotency key is the envelope ID, and this replays
the webhook to prove it holds.
"""
import os
import uuid

import pytest

from conftest import prop

pytestmark = pytest.mark.integration


def _by_email(notion, db, email):
    rows = notion.query(db["candidates"], {
        "property": "Email Norm", "rich_text": {"equals": email.strip().lower()}})
    return rows[0] if rows else None


def _row(roster, sid):
    return next(r for r in roster if r["submission_id"] == sid)


def _events_for_envelope(notion, db, envelope_id, step=None):
    f = {"and": [{"property": "Envelope ID", "rich_text": {"equals": envelope_id}}]}
    if step:
        f["and"].append({"property": "Step", "select": {"equals": step}})
    return notion.query(db["events"], f)


@pytest.fixture
def approved_closer(clean, submit, notion, db, eventually, roster):
    """Get one candidate to Status=Approved with Role=Closer, and hand back the page."""
    row = _row(roster, "S02")  # Priya Raghunathan, Closer
    submit(row)
    page = eventually(lambda: _by_email(notion, db, row["email"]), what="the candidate row")
    notion.update(page["id"], {
        "Role": {"select": {"name": "Closer"}},
        "Status": {"select": {"name": "Approved"}},
    })
    return page


@pytest.mark.T8
def test_approved_candidate_gets_the_role_specific_template(approved_closer, notion,
                                                            db, eventually):
    expected_template = os.environ.get("DOCUSIGN_TEMPLATE_CLOSER")
    page_id = approved_closer["id"]

    page = eventually(
        lambda: (lambda p: p if prop(p, "Envelope ID") else None)(
            notion.query(db["candidates"], {
                "property": "Email Norm",
                "rich_text": {"equals": prop(approved_closer, "Email Norm")}})[0]),
        what="an envelope to be created")

    assert prop(page, "Status") == "Sent"
    envelope = prop(page, "Envelope ID")
    assert envelope

    sent = _events_for_envelope(notion, db, envelope, step="dispatch-docusign")
    assert sent and prop(sent[0], "Result") == "ok"
    if expected_template:
        assert expected_template in (prop(sent[0], "Detail") or ""), \
            "the Closer template must be the one that was used"


@pytest.mark.T9
def test_a_replayed_completion_creates_exactly_one_xero_contact(
        approved_closer, notion, db, eventually, docusign_webhook):
    email = prop(approved_closer, "Email Norm")
    page = eventually(
        lambda: (lambda p: p if prop(p, "Envelope ID") else None)(
            notion.query(db["candidates"], {
                "property": "Email Norm", "rich_text": {"equals": email}})[0]),
        what="an envelope to be created")
    envelope = prop(page, "Envelope ID")

    docusign_webhook(envelope)
    onboarded = eventually(
        lambda: (lambda p: p if prop(p, "Xero Contact ID") else None)(
            notion.query(db["candidates"], {
                "property": "Email Norm", "rich_text": {"equals": email}})[0]),
        what="a Xero contact id to be written")
    contact_id = prop(onboarded, "Xero Contact ID")
    assert prop(onboarded, "Status") == "Onboarded"

    # DocuSign Connect redelivers. So do we.
    docusign_webhook(envelope)
    skipped = eventually(
        lambda: _events_for_envelope(notion, db, envelope, step="idempotency") or None,
        what="the replay to be recognised")
    assert any(prop(e, "Result") == "skipped" for e in skipped)

    after = notion.query(db["candidates"], {
        "property": "Email Norm", "rich_text": {"equals": email}})[0]
    assert prop(after, "Xero Contact ID") == contact_id, "the contact id was overwritten"

    created = [e for e in _events_for_envelope(notion, db, envelope, step="xero-contact")
               if prop(e, "Result") == "ok"]
    assert len(created) == 1, f"expected exactly one Xero contact, saw {len(created)}"


@pytest.mark.T9
def test_a_completion_for_an_unknown_envelope_is_refused(clean, notion, db,
                                                         eventually, docusign_webhook):
    """The idempotency check must not double as a create-anything endpoint."""
    stranger = f"unknown-{uuid.uuid4()}"
    docusign_webhook(stranger)
    events = eventually(lambda: _events_for_envelope(notion, db, stranger) or None,
                        what="the unknown envelope to be logged")
    assert all(prop(e, "Result") in ("rejected", "skipped", "error") for e in events)
    assert not notion.query(db["candidates"], {
        "property": "Envelope ID", "rich_text": {"equals": stranger}})


@pytest.mark.T10
def test_xero_5xx_retries_then_alerts_without_half_finishing(
        clean, submit, notion, db, eventually, docusign_webhook, roster):
    """
    Needs the fault seam: WF-3 routes at XERO_FAULT_URL when
    XERO_FAULT_DOMAIN matches the candidate's email domain. The seam is
    env-gated so it cannot fire in a deployment that has not set it.
    """
    fault_domain = os.environ.get("XERO_FAULT_DOMAIN")
    if not fault_domain:
        pytest.skip("XERO_FAULT_DOMAIN not set; fault injection seam disabled")

    row = dict(_row(roster, "S06"))
    row["email"] = f"aisha.bello@{fault_domain}"
    submit(row)
    page = eventually(lambda: _by_email(notion, db, row["email"]), what="the candidate row")
    notion.update(page["id"], {"Role": {"select": {"name": "Setter"}},
                               "Status": {"select": {"name": "Approved"}}})
    page = eventually(
        lambda: (lambda p: p if prop(p, "Envelope ID") else None)(
            _by_email(notion, db, row["email"])),
        what="an envelope to be created")
    envelope = prop(page, "Envelope ID")

    docusign_webhook(envelope)

    attempts = eventually(
        lambda: (lambda es: es if len(es) >= 3 else None)(
            [e for e in _events_for_envelope(notion, db, envelope, step="xero-contact")
             if prop(e, "Result") == "error"]),
        timeout=90,
        what="at least three failed Xero attempts")
    assert len(attempts) >= 3

    alerts = eventually(
        lambda: [e for e in _events_for_envelope(notion, db, envelope, step="error")] or None,
        what="an alert to be raised")
    assert alerts

    final = _by_email(notion, db, row["email"])
    assert not prop(final, "Xero Contact ID"), "a failed run must not write a contact id"
    assert prop(final, "Status") != "Onboarded", \
        "the candidate must not be marked onboarded on a failed run"
