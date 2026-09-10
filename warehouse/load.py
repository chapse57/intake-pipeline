"""
Load one candidates CSV into the warehouse. The only write path.

    python -m warehouse.load data/candidates.csv
    python -m warehouse.load data/candidates_v2.csv --supersede

What happens, in order:

  1. hash the file. A manifest row is written and committed first, so a
     load that dies half-way still leaves a record of having started.
  2. every line goes into raw exactly as it arrived, tagged with the load_id.
  3. the raw rows are validated in SQL against ref: recruiter known, role
     known, name and email present, phone parseable for its region. Every
     failing line is collected. One failure fails the load; the caller gets
     all of them, itemized by line number, not the first one.
  4. lines are deduplicated in order on the database-computed keys
     (email_norm, then phone_e164) and written to core + restricted.
  5. if --supersede was given, the previous load of the same source is marked
     superseded by this one.

Steps 2-5 are one transaction. On any failure the transaction is rolled back
and the manifest row is updated to `failed` with the itemized reasons. Rows
from a failed load never exist; rows from a good load are never edited.
"""
import argparse
import csv
import hashlib
import io
import pathlib
import sys
from dataclasses import dataclass, field

from psycopg import sql

from warehouse.conn import connect

# columns the model knows. Anything else in the file goes to raw.extra.
MODELED = ["submission_id", "recruiter_name", "recruiter_email", "name", "email",
           "phone", "country_hint", "application_text", "role", "note"]
# data/candidates.csv calls the role column expected_role because the same
# file drives the n8n tests; for the warehouse it is simply the role.
ALIASES = {"expected_role": "role"}

REQUIRED_SCHEMA_VERSION = 6


class LoadRefused(Exception):
    """Nothing was written. The file, or its relation to earlier loads, is wrong."""


class LoadFailed(Exception):
    def __init__(self, load_id, failures):
        self.load_id, self.failures = load_id, failures
        super().__init__(self.itemized())

    def itemized(self):
        head = f"load {self.load_id} failed: {len(self.failures)} line(s) rejected"
        return "\n".join([head] + [f"  line {ln} ({sid or '-'}): {why}"
                                   for ln, sid, why in self.failures])


@dataclass
class LoadResult:
    load_id: str
    source_name: str
    file_sha256: str
    rows: int
    created: int
    duplicates: int
    superseded: str | None = None
    flagged: list = field(default_factory=list)  # (line_no, submission_id, matched_on, matched_submission_id)

    def summary(self):
        s = (f"load {self.load_id}: {self.source_name} sha256 {self.file_sha256[:12]}… "
             f"{self.rows} rows → {self.created} candidates, {self.duplicates} duplicates flagged")
        if self.superseded:
            s += f"; supersedes {self.superseded}"
        return s


# --------------------------------------------------------------------------
def read_csv(data: bytes):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    rows = []
    for i, r in enumerate(reader, start=1):
        row = {k: None for k in MODELED}
        extra = {}
        for k, v in r.items():
            k2 = ALIASES.get(k, k)
            if k2 in row:
                row[k2] = v
            else:
                extra[k] = v
        row["extra"] = extra
        rows.append((i, row))
    return rows


VALIDATE_SQL = """
WITH r AS (SELECT * FROM raw.candidate_submissions WHERE load_id = %(load_id)s)
SELECT line_no, submission_id, why FROM (
    SELECT line_no, submission_id, 'name is empty' AS why FROM r
     WHERE coalesce(btrim(name), '') = ''
  UNION ALL
    SELECT line_no, submission_id, 'email is empty or has no @' FROM r
     WHERE ref.email_domain(coalesce(email, '')) = ''
  UNION ALL
    SELECT r.line_no, r.submission_id,
           'unknown recruiter ' || coalesce(quote_literal(r.recruiter_email), 'NULL')
      FROM r LEFT JOIN ref.recruiters rc ON rc.email = ref.email_norm(r.recruiter_email)
     WHERE rc.recruiter_id IS NULL
  UNION ALL
    SELECT r.line_no, r.submission_id, 'unknown role ' || quote_literal(r.role)
      FROM r LEFT JOIN ref.roles ro ON ro.role = r.role
     WHERE coalesce(r.role, '') <> '' AND ro.role IS NULL
  UNION ALL
    SELECT line_no, submission_id,
           'phone ' || quote_literal(phone) || ' not parseable for region ' || coalesce(quote_literal(country_hint), 'NULL')
      FROM r
     WHERE coalesce(btrim(phone), '') <> '' AND ref.phone_e164(phone, country_hint) = ''
) f
ORDER BY line_no, why
"""

KEYS_SQL = """
SELECT r.line_no, r.submission_id, r.name, r.role, r.email, r.phone, r.country_hint,
       ref.email_norm(r.email)                 AS email_norm,
       ref.email_domain(r.email)               AS email_domain,
       ref.phone_e164(r.phone, r.country_hint) AS phone_e164,
       rc.recruiter_id
  FROM raw.candidate_submissions r
  JOIN ref.recruiters rc ON rc.email = ref.email_norm(r.recruiter_email)
 WHERE r.load_id = %(load_id)s
 ORDER BY r.line_no
"""


def _schema_ok(cur):
    cur.execute("SELECT coalesce(max(version), 0) FROM raw.schema_migrations")
    v = cur.fetchone()[0]
    if v < REQUIRED_SCHEMA_VERSION:
        raise LoadRefused(f"schema is at migration {v}, loader needs {REQUIRED_SCHEMA_VERSION}; "
                          "run python -m warehouse.migrate")


def _open_manifest(conn, path, data, supersede):
    sha = hashlib.sha256(data).hexdigest()
    source_name = path.name
    with conn.transaction(), conn.cursor() as cur:
        _schema_ok(cur)
        cur.execute("SELECT load_id, source_name FROM raw.load_manifest "
                    "WHERE file_sha256 = %s AND status = 'loaded'", (sha,))
        if (hit := cur.fetchone()):
            raise LoadRefused(f"these exact bytes are already loaded as {hit[0]} "
                              f"({hit[1]}); nothing to do")
        cur.execute("SELECT load_id FROM raw.load_manifest "
                    "WHERE source_name = %s AND status = 'loaded'", (source_name,))
        prev = cur.fetchone()
        prev_id = prev[0] if prev else None
        if prev_id and not supersede:
            raise LoadRefused(f"{source_name} is already loaded as {prev_id} with different "
                              "content; pass --supersede to replace it")
        if supersede and not prev_id:
            raise LoadRefused(f"--supersede given but no current load of {source_name} exists")
        cur.execute(
            "INSERT INTO raw.load_manifest (source_name, source_path, file_sha256, file_bytes, supersedes) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING load_id",
            (source_name, str(path), sha, len(data), prev_id))
        load_id = cur.fetchone()[0]
    return load_id, sha, prev_id


def _fail(conn, load_id, failures):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("UPDATE raw.load_manifest SET status = 'failed', error = %s, "
                    "finished_at = now() WHERE load_id = %s",
                    ("\n".join(f"line {ln} ({sid or '-'}): {why}" for ln, sid, why in failures),
                     load_id))


def load(path, supersede=False, role="loader"):
    path = pathlib.Path(path)
    data = path.read_bytes()
    rows = read_csv(data)
    if not rows:
        raise LoadRefused(f"{path} has a header and no rows")

    with connect(role) as conn:
        load_id, sha, prev_id = _open_manifest(conn, path, data, supersede)
        try:
            with conn.transaction(), conn.cursor() as cur:
                # 2. raw, verbatim
                cur.executemany(
                    sql.SQL("INSERT INTO raw.candidate_submissions (load_id, line_no, {cols}, extra) "
                            "VALUES (%s, %s, {ph}, %s)").format(
                        cols=sql.SQL(", ").join(map(sql.Identifier, MODELED)),
                        ph=sql.SQL(", ").join(sql.Placeholder() * len(MODELED))),
                    [(load_id, ln, *[r[c] for c in MODELED], _json(r["extra"])) for ln, r in rows])

                # 3. validate against ref, all lines, then decide
                cur.execute(VALIDATE_SQL, {"load_id": load_id})
                failures = cur.fetchall()
                if failures:
                    raise LoadFailed(load_id, failures)

                # 4. dedupe in file order on database-computed keys
                cur.execute(KEYS_SQL, {"load_id": load_id})
                by_email, by_phone, flagged, created = {}, {}, [], 0
                for (ln, sid, name, role_, email, phone, region,
                     email_norm, email_domain, phone_e164, recruiter_id) in cur.fetchall():
                    hit = by_email.get(email_norm)
                    on = "email" if hit else None
                    if not hit and phone_e164:
                        hit = by_phone.get(phone_e164)
                        on = "phone" if hit else None
                    if hit:
                        cand_id, first_sid = hit
                        cur.execute(
                            "INSERT INTO core.submissions (load_id, line_no, submission_id, candidate_id, "
                            "recruiter_id, outcome, matched_on, matched_submission_id) "
                            "VALUES (%s, %s, %s, %s, %s, 'duplicate_flagged', %s, %s)",
                            (load_id, ln, sid, cand_id, recruiter_id, on, first_sid))
                        flagged.append((ln, sid, on, first_sid))
                        continue
                    cur.execute(
                        "INSERT INTO core.candidates (load_id, line_no, submission_id, name, email_domain, "
                        "country_hint, role, recruiter_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                        "RETURNING candidate_id",
                        (load_id, ln, sid, name, email_domain, region or None, role_ or None, recruiter_id))
                    cand_id = cur.fetchone()[0]
                    cur.execute(
                        "INSERT INTO restricted.candidate_contact (candidate_id, load_id, email, phone, phone_region) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (cand_id, load_id, email, phone, region or None))
                    cur.execute(
                        "INSERT INTO core.submissions (load_id, line_no, submission_id, candidate_id, "
                        "recruiter_id, outcome) VALUES (%s, %s, %s, %s, %s, 'created')",
                        (load_id, ln, sid, cand_id, recruiter_id))
                    by_email[email_norm] = (cand_id, sid)
                    if phone_e164:
                        by_phone.setdefault(phone_e164, (cand_id, sid))
                    created += 1

                # 5. supersede, then close the manifest — same transaction
                if prev_id:
                    cur.execute("UPDATE raw.load_manifest SET status = 'superseded', superseded_by = %s "
                                "WHERE load_id = %s AND status = 'loaded'", (load_id, prev_id))
                    if cur.rowcount != 1:
                        raise LoadRefused(f"previous load {prev_id} changed state during this load")
                cur.execute("UPDATE raw.load_manifest SET status = 'loaded', row_count = %s, "
                            "finished_at = now() WHERE load_id = %s", (len(rows), load_id))
        except LoadFailed as e:
            _fail(conn, load_id, e.failures)
            raise
        except Exception as e:  # anything else is still a recorded failure
            _fail(conn, load_id, [(0, None, f"{type(e).__name__}: {e}")])
            raise

    return LoadResult(str(load_id), path.name, sha, len(rows), created, len(flagged),
                      str(prev_id) if prev_id else None, flagged)


def _json(d):
    import json
    from psycopg.types.json import Jsonb
    return Jsonb(d)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--supersede", action="store_true",
                    help="replace the current load of this source with this file")
    ap.add_argument("--role", default="loader", help="which WAREHOUSE_<ROLE>_DSN to use")
    a = ap.parse_args(argv)
    try:
        res = load(a.path, supersede=a.supersede, role=a.role)
    except LoadRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except LoadFailed as e:
        print(e.itemized(), file=sys.stderr)
        return 1
    print(res.summary())
    for ln, sid, on, first in res.flagged:
        print(f"  line {ln} {sid}: duplicate of {first} on {on}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
