-- 001_schemas.sql — the four schemas and the migration ledger.
--
-- raw         files as received, one row per input line, tagged with the load
-- ref         reference tables the loader validates against (recruiters, roles)
-- core        the deduplicated candidate model, free of contact details
-- restricted  contact details (email, phone). Analysts have no USAGE here.
--
-- Nothing lives in `public`. It is left in place because extensions and
-- managed-service tooling expect it, but nobody gets CREATE on it.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS restricted;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Applied migrations. migrate.py inserts here inside the same transaction as
-- the file it applied, so a half-applied migration cannot be recorded.
CREATE TABLE IF NOT EXISTS raw.schema_migrations (
    version     integer     PRIMARY KEY,
    filename    text        NOT NULL,
    sha256      text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL DEFAULT current_user
);
