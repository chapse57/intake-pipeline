"""
Warehouse tier — migrations, loader and role isolation against a real
PostgreSQL. Skipped unless the three WAREHOUSE_*_DSN variables are set.

    pytest -m warehouse

The suite re-creates the schemas from the migrations at session start (as the
admin) and loads data/candidates.csv through the loader identity. Clean-up
is before, not after: when a test fails the rows are the evidence.

W1  migrations apply once and are a no-op the second time
W2  20 lines → 20 raw rows, 14 candidates, 6 flagged; each flagged line
    matches the expected_duplicate_of / expected_match_on the fixture carries
W3  the same bytes are refused a second time; nothing changes
W4  a file with three bad lines fails atomically: all three reasons come
    back itemized, the manifest says failed, zero rows exist for that load
W5  a corrected file with --supersede replaces the old load in the views and
    leaves the old rows on disk
W6  the analyst identity gets permission denied on every object in raw and
    restricted, can read core/ref, and cannot change anything there
W7  the loader identity cannot DELETE or TRUNCATE anywhere
W8  the SQL normalization functions agree with n8n/normalize.js on all rows
W9  pgaudit is loaded (Azure); skipped on a server without it
"""
import os
import pathlib

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402

from warehouse import load as loader  # noqa: E402
from warehouse.conn import connect  # noqa: E402
from warehouse.migrate import migrate  # noqa: E402

pytestmark = pytest.mark.warehouse

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "candidates.csv"
NEEDED = ["WAREHOUSE_ADMIN_DSN", "WAREHOUSE_LOADER_DSN", "WAREHOUSE_ANALYST_DSN"]


# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def wh_env():
    missing = [k for k in NEEDED if not os.environ.get(k)]
    if missing:
        pytest.skip("warehouse env not set: " + ", ".join(missing))


@pytest.fixture(scope="session")
def fresh_schema(wh_env):
    """Drop the four schemas and re-apply every migration. Returns the list applied."""
    with connect("admin") as c, c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS restricted, core, raw, ref CASCADE")
        c.commit()
    return migrate()


@pytest.fixture(scope="session")
def loaded(fresh_schema):
    return loader.load(CSV)


def q(role, sql_, params=None):
    with connect(role) as c, c.cursor() as cur:
        cur.execute(sql_, params)
        return cur.fetchall()


def one(role, sql_, params=None):
    return q(role, sql_, params)[0][0]


# --------------------------------------------------------------------------
def test_w1_migrations_apply_once(fresh_schema):
    assert len(fresh_schema) == 6, fresh_schema
    assert migrate() == []  # second run: nothing pending
    assert one("admin", "SELECT count(*) FROM raw.schema_migrations") == 6
    assert set(r[0] for r in q("admin",
        "SELECT nspname FROM pg_namespace WHERE nspname IN ('raw','ref','core','restricted')")
    ) == {"raw", "ref", "core", "restricted"}


def test_w2_load_counts_and_flags_match_fixture(loaded, roster):
    assert (loaded.rows, loaded.created, loaded.duplicates) == (20, 14, 6)
    lid = loaded.load_id

    assert one("loader", "SELECT status FROM raw.load_manifest WHERE load_id = %s", (lid,)) == "loaded"
    assert one("loader", "SELECT row_count FROM raw.load_manifest WHERE load_id = %s", (lid,)) == 20
    assert one("loader", "SELECT count(*) FROM raw.candidate_submissions WHERE load_id = %s", (lid,)) == 20
    assert one("loader", "SELECT count(*) FROM core.candidates WHERE load_id = %s", (lid,)) == 14
    assert one("loader", "SELECT count(*) FROM restricted.candidate_contact WHERE load_id = %s", (lid,)) == 14
    assert one("loader", "SELECT count(*) FROM core.submissions WHERE load_id = %s", (lid,)) == 20

    expected = {r["submission_id"]: r for r in roster}
    got = {sid: (on, first) for _, sid, on, first in loaded.flagged}
    want = {sid: (r["expected_match_on"], r["expected_duplicate_of"])
            for sid, r in expected.items() if r["expected_outcome"] == "duplicate_flagged"}
    assert got == want

    # raw kept the file verbatim, including the whitespace we normalize away
    raw_email = one("loader", "SELECT email FROM raw.candidate_submissions "
                    "WHERE load_id = %s AND submission_id = 'S15'", (lid,))
    assert raw_email == "  MARCUS.WEBB@NORTHLINE.IO "
    # and restricted computed the key from it
    assert one("loader", "SELECT email_norm FROM restricted.candidate_contact cc "
               "JOIN core.candidates c USING (candidate_id) WHERE c.submission_id = 'S01' "
               "AND c.load_id = %s", (lid,)) == "marcus.webb@northline.io"
    # a national-format phone became E.164
    assert one("loader", "SELECT phone_e164 FROM restricted.candidate_contact cc "
               "JOIN core.candidates c USING (candidate_id) WHERE c.submission_id = 'S11' "
               "AND c.load_id = %s", (lid,)) == "+821034567890"
    # attribution stays with the first submitter (T11: Ben re-submits Ana's candidate)
    assert one("loader", "SELECT r.name FROM core.candidates c JOIN ref.recruiters r USING (recruiter_id) "
               "WHERE c.submission_id = 'S03' AND c.load_id = %s", (lid,)) == "Ana Duarte"


def test_w3_same_bytes_are_refused(loaded):
    before = q("loader", "SELECT load_id, status FROM raw.load_manifest ORDER BY started_at")
    with pytest.raises(loader.LoadRefused, match="already loaded"):
        loader.load(CSV)
    assert q("loader", "SELECT load_id, status FROM raw.load_manifest ORDER BY started_at") == before


def test_w4_bad_lines_fail_atomically_and_itemized(loaded, tmp_path):
    text = CSV.read_text(encoding="utf-8")
    bad = (text
           .replace("ben.kowalski@intake.demo,Sofia Lindqvist", "nobody@intake.demo,Sofia Lindqvist")  # S04 unknown recruiter
           .replace("+56 2 2555 0184,CL", "+56 2 2555 0184,XX")                                          # S07 fine: has +, region ignored
           .replace("010-3456-7890,KR", "010-3456-7890,ZZ")                                              # S11 unparseable region
           .replace("created,Setter,,,,\nS07", "created,Barista,,,,\nS07"))                            # S06 unknown role
    assert bad != text
    p = tmp_path / "candidates_bad.csv"
    p.write_text(bad, encoding="utf-8")

    with pytest.raises(loader.LoadFailed) as ei:
        loader.load(p)
    reasons = {(sid, why.split(" ")[0]) for _, sid, why in ei.value.failures}
    assert reasons == {("S04", "unknown"), ("S06", "unknown"), ("S11", "phone")}, ei.value.itemized()

    lid = ei.value.load_id
    status, error = q("loader", "SELECT status, error FROM raw.load_manifest WHERE load_id = %s", (lid,))[0]
    assert status == "failed"
    assert "S04" in error and "S06" in error and "S11" in error
    for t in ("raw.candidate_submissions", "core.candidates", "core.submissions", "restricted.candidate_contact"):
        assert one("loader", f"SELECT count(*) FROM {t} WHERE load_id = %s", (lid,)) == 0, t
    # the good load is untouched and still the current one
    assert one("analyst", "SELECT count(*) FROM core.v_candidates") == 14


def test_w5_supersede_replaces_current_load_and_keeps_history(loaded, tmp_path):
    # same source name, one row corrected → different bytes
    text = CSV.read_text(encoding="utf-8").replace("Sophia Lindqvist", "Sofia Lindqvist-Berg")
    p = tmp_path / "candidates.csv"
    p.write_text(text, encoding="utf-8")

    with pytest.raises(loader.LoadRefused, match="--supersede"):
        loader.load(p)

    res = loader.load(p, supersede=True)
    assert res.superseded == loaded.load_id
    assert (res.rows, res.created, res.duplicates) == (20, 14, 6)

    st = dict(q("loader", "SELECT load_id::text, status FROM raw.load_manifest"))
    assert st[loaded.load_id] == "superseded" and st[res.load_id] == "loaded"
    assert one("loader", "SELECT superseded_by::text FROM raw.load_manifest WHERE load_id = %s",
               (loaded.load_id,)) == res.load_id

    # views show only the new load; old rows are still on disk
    assert set(r[0] for r in q("analyst", "SELECT DISTINCT load_id::text FROM core.v_candidates")) == {res.load_id}
    assert one("analyst", "SELECT count(*) FROM core.v_candidates WHERE name = 'Sofia Lindqvist-Berg'") == 1
    assert one("loader", "SELECT count(*) FROM core.candidates WHERE load_id = %s", (loaded.load_id,)) == 14

    # put things back so later tests see the original load as current
    with connect("admin") as c, c.cursor() as cur:
        cur.execute("UPDATE raw.load_manifest SET status = 'failed', error = 'test teardown', "
                    "superseded_by = NULL WHERE load_id = %s", (res.load_id,))
        cur.execute("UPDATE raw.load_manifest SET status = 'loaded', superseded_by = NULL "
                    "WHERE load_id = %s", (loaded.load_id,))
        c.commit()


# --------------------------------------------------------------------------
def _objects(schema):
    return q("admin", """
        SELECT c.relkind::text, n.nspname || '.' || quote_ident(c.relname)
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relkind IN ('r','v','m','S','p')
        UNION ALL
        SELECT 'f', n.nspname || '.' || quote_ident(p.proname) || '(' || pg_get_function_identity_arguments(p.oid) || ')'
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = %s AND p.prokind = 'f'
    """, (schema, schema))


def _denied(role, stmt):
    try:
        q(role, stmt)
    except errors.InsufficientPrivilege:
        return True
    return False


def test_w6_analyst_is_denied_everything_in_raw_and_restricted(loaded):
    checked = 0
    for schema in ("raw", "restricted"):
        objs = _objects(schema)
        assert objs, f"no objects found in {schema}"
        for kind, name in objs:
            if kind == "S":
                stmt = f"SELECT nextval('{name}')"
            elif kind == "f":
                # any reference into a schema without USAGE fails before arguments matter
                stmt = f"SELECT {name.split('(')[0]}()"
            else:
                stmt = f"SELECT * FROM {name} LIMIT 1"
            assert _denied("analyst", stmt), f"analyst could run: {stmt}"
            checked += 1
    assert checked >= 5, checked  # manifest, submissions, ledger, contact table, trigger function

    # analyst can read the model
    assert one("analyst", "SELECT count(*) FROM core.v_candidates") == 14
    assert one("analyst", "SELECT count(*) FROM core.v_submissions WHERE outcome = 'duplicate_flagged'") == 6
    assert one("analyst", "SELECT count(*) FROM ref.recruiters") == 4
    # and there is nothing to leak: no email/phone column anywhere in core or ref
    cols = q("admin", """
        SELECT table_schema || '.' || table_name || '.' || column_name
          FROM information_schema.columns
         WHERE table_schema IN ('core', 'ref')
           AND (column_name ILIKE '%%email%%' OR column_name ILIKE '%%phone%%')
    """)
    assert sorted(c[0] for c in cols) == ["core.candidates.email_domain", "core.v_candidates.email_domain",
                                          "ref.recruiters.email"], cols  # recruiters are staff, not candidates
    # analyst cannot change the model
    for stmt in ("DELETE FROM core.candidates", "UPDATE core.candidates SET name = 'x'",
                 "INSERT INTO ref.roles VALUES ('Barista', 9)", "CREATE TABLE public.scratch (x int)",
                 "CREATE TABLE core.scratch (x int)"):
        assert _denied("analyst", stmt), f"analyst could run: {stmt}"


def test_w7_loader_cannot_delete_or_truncate(loaded):
    for stmt in ("DELETE FROM raw.candidate_submissions", "DELETE FROM raw.load_manifest",
                 "DELETE FROM core.candidates", "DELETE FROM core.submissions",
                 "DELETE FROM restricted.candidate_contact",
                 "TRUNCATE raw.candidate_submissions", "TRUNCATE restricted.candidate_contact",
                 "UPDATE raw.candidate_submissions SET email = 'x'",
                 "UPDATE restricted.candidate_contact SET email = 'x'",
                 "UPDATE raw.load_manifest SET file_sha256 = repeat('0', 64)",
                 "DROP TABLE raw.candidate_submissions", "DELETE FROM ref.recruiters"):
        assert _denied("loader", stmt), f"loader could run: {stmt}"


def test_w8_sql_normalization_matches_normalize_js(loaded, roster, normalize):
    """ref.email_norm / ref.phone_e164 vs the JavaScript the n8n node runs."""
    mismatches = []
    for r in roster:
        js = normalize({"name": r["name"], "email": r["email"], "phone": r["phone"],
                        "country_hint": r["country_hint"]})
        e, d, p = q("admin", "SELECT ref.email_norm(%s), ref.email_domain(%s), ref.phone_e164(%s, %s)",
                    (r["email"], r["email"], r["phone"], r["country_hint"]))[0]
        if (e, d, p) != (js["email_norm"], js["email_domain"], js["phone_e164"]):
            mismatches.append((r["submission_id"], (e, d, p), js))
    # a few edge cases beyond the roster
    for phone, region, want in [("0044 20 7946 0318", "US", "+442079460318"),
                                ("020 7946 0318", "ZZ", ""), ("", "GB", ""),
                                ("12345", "GB", ""), ("+1 415 555 0142", None, "+14155550142")]:
        js = normalize({"name": "", "email": "", "phone": phone, "country_hint": region})
        p = one("admin", "SELECT ref.phone_e164(%s, %s)", (phone, region))
        if not (p == js["phone_e164"] == want):
            mismatches.append((phone, region, p, js["phone_e164"], want))
    assert not mismatches, mismatches


def test_w9_pgaudit_is_loaded(wh_env):
    """Azure: pgaudit must be in shared_preload_libraries and logging something.
    Locally (no pgaudit package) this skips; it is not a pass."""
    try:
        libs = one("admin", "SHOW shared_preload_libraries")
    except errors.InsufficientPrivilege:
        libs = ",".join(r[0] for r in q("admin", "SELECT extname FROM pg_extension"))
    if "pgaudit" not in libs:
        pytest.skip(f"pgaudit not loaded ({libs!r}); Azure-only check")
    assert one("admin", "SHOW pgaudit.log") not in ("", "none"), "pgaudit loaded but logging nothing"
