-- 006_roles.sql — two group roles, and what each may touch.
--
--   wh_loader   writes loads. INSERT only; UPDATE only on the manifest's
--               status columns; no DELETE or TRUNCATE anywhere.
--   wh_analyst  reads ref and core. No USAGE on raw or restricted, so every
--               object in those schemas is "permission denied for schema",
--               whatever grants a table might accidentally carry.
--
-- Both are NOLOGIN. Login identities are made members:
--   local test:  CREATE ROLE loader_test LOGIN PASSWORD '...'; GRANT wh_loader TO loader_test;
--   Azure:       SELECT * FROM pgaadauth_create_principal('<entra principal>', false, false);
--                GRANT wh_loader TO "<entra principal>";
-- The migration owner (the role running migrate.py) stays the owner of every
-- object and is the only role that can drop anything. It is not used by the
-- loader or by analysts.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wh_loader') THEN
        CREATE ROLE wh_loader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wh_analyst') THEN
        CREATE ROLE wh_analyst NOLOGIN;
    END IF;
END
$$;

-- Start from nothing. Re-running the migration re-derives the grants.
REVOKE ALL ON SCHEMA raw, ref, core, restricted FROM wh_loader, wh_analyst;
REVOKE ALL ON ALL TABLES IN SCHEMA raw, ref, core, restricted FROM wh_loader, wh_analyst;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA raw, ref, core, restricted FROM wh_loader, wh_analyst;

-- ---- loader -------------------------------------------------------------
GRANT USAGE ON SCHEMA raw, ref, core, restricted TO wh_loader;
GRANT SELECT ON ALL TABLES IN SCHEMA ref TO wh_loader;
GRANT SELECT, INSERT ON raw.candidate_submissions TO wh_loader;
GRANT SELECT, INSERT ON raw.load_manifest TO wh_loader;
GRANT UPDATE (status, row_count, finished_at, error, superseded_by)
    ON raw.load_manifest TO wh_loader;
GRANT SELECT ON raw.schema_migrations TO wh_loader;   -- so it can refuse to run against an old schema
GRANT SELECT, INSERT ON core.candidates, core.submissions TO wh_loader;
GRANT SELECT ON core.v_candidates, core.v_submissions, core.v_loads TO wh_loader;
GRANT SELECT, INSERT ON restricted.candidate_contact TO wh_loader;

-- ---- analyst ------------------------------------------------------------
GRANT USAGE ON SCHEMA ref, core TO wh_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA ref, core TO wh_analyst;   -- views included
ALTER DEFAULT PRIVILEGES IN SCHEMA ref, core GRANT SELECT ON TABLES TO wh_analyst;
-- deliberately nothing on raw or restricted.
