-- 003_raw.sql — the load manifest and the as-received rows.
--
-- Loads are immutable. A row in raw is never updated or deleted; a
-- correction is a new load that supersedes the old one. The manifest is the
-- only place a status changes, and it changes in one direction:
--   started -> loaded -> superseded
--   started -> failed
--
-- NOTE: raw holds email and phone exactly as they arrived, so raw is as
-- restricted as `restricted`. Analysts get no USAGE on either.

CREATE TABLE IF NOT EXISTS raw.load_manifest (
    load_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_name    text        NOT NULL,           -- file basename, the identity of a feed
    source_path    text        NOT NULL,           -- where it was read from, for the record
    file_sha256    text        NOT NULL CHECK (length(file_sha256) = 64),
    file_bytes     bigint      NOT NULL,
    row_count      integer     NULL,               -- set when loaded
    status         text        NOT NULL DEFAULT 'started'
                               CHECK (status IN ('started', 'loaded', 'failed', 'superseded')),
    superseded_by  uuid        NULL REFERENCES raw.load_manifest (load_id),
    supersedes     uuid        NULL REFERENCES raw.load_manifest (load_id),
    error          text        NULL,               -- itemized failures, when status = failed
    loaded_by      text        NOT NULL DEFAULT current_user,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz NULL,
    CHECK ((status = 'superseded') = (superseded_by IS NOT NULL)),
    CHECK ((status = 'failed') = (error IS NOT NULL))
);

-- The same bytes are never loaded twice while the earlier load is current.
CREATE UNIQUE INDEX IF NOT EXISTS load_manifest_one_live_hash
    ON raw.load_manifest (file_sha256) WHERE status = 'loaded';

-- One current load per source. Superseding is the only way to replace it.
CREATE UNIQUE INDEX IF NOT EXISTS load_manifest_one_live_source
    ON raw.load_manifest (source_name) WHERE status = 'loaded';

-- One row per input line. Every column is text: raw records what the file
-- said, not what we made of it. Typing and normalization happen on the way
-- into core/restricted, where a failure is attributable to a line number.
CREATE TABLE IF NOT EXISTS raw.candidate_submissions (
    load_id             uuid    NOT NULL REFERENCES raw.load_manifest (load_id),
    line_no             integer NOT NULL,           -- 1 = first data row
    submission_id       text,
    recruiter_name      text,
    recruiter_email     text,
    name                text,
    email               text,
    phone               text,
    country_hint        text,
    application_text    text,
    role                text,                       -- the role the intake pipeline settled on
    note                text,
    extra               jsonb   NOT NULL DEFAULT '{}'::jsonb, -- any column the loader does not model
    PRIMARY KEY (load_id, line_no)
);
