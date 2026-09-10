"""
Apply warehouse/migrations/NNN_*.sql in order, once each.

    python -m warehouse.migrate            # uses WAREHOUSE_ADMIN_DSN
    python -m warehouse.migrate --dry-run  # list what would be applied

Each file runs in its own transaction together with its ledger row in
raw.schema_migrations, so a file either applied and was recorded or did
neither. A file whose recorded sha256 no longer matches the one on disk stops
the run: edit history belongs in a new file, not in an applied one.
"""
import hashlib
import pathlib
import sys

from warehouse.conn import connect

HERE = pathlib.Path(__file__).resolve().parent / "migrations"


def pending(conn):
    files = sorted(HERE.glob("[0-9][0-9][0-9]_*.sql"))
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('raw.schema_migrations')")
        applied = {}
        if cur.fetchone()[0]:
            cur.execute("SELECT version, sha256 FROM raw.schema_migrations")
            applied = dict(cur.fetchall())
    out = []
    for f in files:
        version = int(f.name[:3])
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if version in applied:
            if applied[version] != sha:
                sys.exit(f"{f.name} was applied with a different content hash; refusing")
            continue
        out.append((version, f, sha))
    return out


def migrate(dsn=None, dry_run=False):
    with connect(dsn or "admin") as conn:
        todo = pending(conn)
        for version, f, sha in todo:
            print(("would apply " if dry_run else "applying ") + f.name)
            if dry_run:
                continue
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(f.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO raw.schema_migrations (version, filename, sha256) "
                            "VALUES (%s, %s, %s)", (version, f.name, sha))
        return [f.name for _, f, _ in todo]


if __name__ == "__main__":
    applied = migrate(dry_run="--dry-run" in sys.argv)
    print(f"{len(applied)} migration(s) {'pending' if '--dry-run' in sys.argv else 'applied'}")
