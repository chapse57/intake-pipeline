"""
Integration tier — T12, the number the write-up quotes.

Twenty submissions, six of them planted duplicates. The assertion is not
just the arithmetic: every submission must be accounted for. A pipeline that
creates 14 rows by dropping 6 on the floor produces the same two counts as
one that flags them, and only the Events log tells them apart.
"""
import collections

import pytest

from conftest import prop

pytestmark = [pytest.mark.integration, pytest.mark.T12]


def test_batch_of_twenty_with_six_planted_duplicates(clean, submit, notion, db,
                                                     eventually, roster):
    assert len(roster) == 20

    expected = collections.Counter(r["expected_outcome"] for r in roster)
    expected_created = expected["created"] + expected["created_review"]
    expected_flagged = expected["duplicate_flagged"]
    assert (expected_created, expected_flagged) == (14, 6), \
        "the fixture itself drifted; rebuild it with data/build_fixtures.py"

    for row in roster:
        submit(row)

    rows = eventually(
        lambda: (lambda rs: rs if len(rs) >= expected_created else None)(
            notion.query(db["candidates"])),
        timeout=180, interval=3,
        what=f"{expected_created} candidate rows")

    assert len(rows) == expected_created, \
        f"expected {expected_created} rows, found {len(rows)}"

    by_email = {prop(r, "Email Norm"): r for r in rows}
    assert len(by_email) == len(rows), "two rows share a normalized email"

    # every submission that should have produced a row, did
    for r in roster:
        if r["expected_outcome"] == "duplicate_flagged":
            continue
        assert r["email"].strip().lower() in by_email, \
            f"{r['submission_id']} {r['name']} was lost"

    # every submission that should not have, did not
    for r in roster:
        if r["expected_outcome"] != "duplicate_flagged":
            continue
        assert r["email"].strip().lower() not in by_email, \
            f"{r['submission_id']} created a row it should not have"

    total_attempts = sum(prop(r, "Duplicate Attempts") or 0 for r in rows)
    assert total_attempts == expected_flagged, \
        f"{expected_flagged} duplicates submitted, {total_attempts} recorded"

    review = [r for r in rows if prop(r, "Status") == "Review"]
    assert len(review) == expected["created_review"]
    for r in review:
        assert prop(r, "Duplicate Of"), "a Review row must say what it resembles"

    # nothing was silently dropped: 20 in, 20 accounted for in the log
    dedupe_events = notion.query(db["events"], {
        "property": "Step", "select": {"equals": "dedupe"}})
    assert len(dedupe_events) == len(roster), \
        f"20 submissions, {len(dedupe_events)} dedupe decisions logged"

    print(f"\nT12: {len(roster)} submitted -> {len(rows)} created "
          f"({len(review)} Review), {total_attempts} flagged, 0 lost")
