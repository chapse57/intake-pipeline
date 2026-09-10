-- 004_core.sql — the deduplicated candidate model, without contact details.
--
-- core has no email and no phone column anywhere. An analyst can count
-- candidates by role, recruiter, country and load without being able to
-- identify or contact one. Contact details live in `restricted`, keyed by
-- candidate_id, and the loader is the only role that reads both.

CREATE TABLE IF NOT EXISTS core.candidates (
    candidate_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    load_id         uuid        NOT NULL REFERENCES raw.load_manifest (load_id),
    line_no         integer     NOT NULL,           -- the raw line that created this candidate
    submission_id   text        NOT NULL,
    name            text        NOT NULL,
    email_domain    text        NOT NULL,           -- derived; the domain alone is not contact data
    country_hint    char(2)     NULL,
    role            text        NULL REFERENCES ref.roles (role),
    recruiter_id    smallint    NOT NULL REFERENCES ref.recruiters (recruiter_id),  -- attribution: first submitter
    created_at      timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (load_id, line_no) REFERENCES raw.candidate_submissions (load_id, line_no),
    UNIQUE (load_id, submission_id)
);

-- Every input line lands here exactly once, as either the line that created
-- a candidate or a flagged duplicate of one. 20 lines in, 20 rows here.
CREATE TABLE IF NOT EXISTS core.submissions (
    load_id         uuid        NOT NULL,
    line_no         integer     NOT NULL,
    submission_id   text        NOT NULL,
    candidate_id    uuid        NOT NULL REFERENCES core.candidates (candidate_id),
    recruiter_id    smallint    NOT NULL REFERENCES ref.recruiters (recruiter_id),
    outcome         text        NOT NULL CHECK (outcome IN ('created', 'duplicate_flagged')),
    matched_on      text        NULL CHECK (matched_on IN ('email', 'phone')),
    matched_submission_id text  NULL,               -- the earlier line it collided with
    PRIMARY KEY (load_id, line_no),
    FOREIGN KEY (load_id, line_no) REFERENCES raw.candidate_submissions (load_id, line_no),
    CHECK ((outcome = 'duplicate_flagged') = (matched_on IS NOT NULL)),
    CHECK ((outcome = 'duplicate_flagged') = (matched_submission_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS submissions_candidate ON core.submissions (candidate_id);

-- What analysts should query: only rows from loads that are current.
-- A superseded load's rows stay on disk (they are the audit trail) but
-- disappear from here.
CREATE OR REPLACE VIEW core.v_candidates AS
SELECT c.candidate_id, c.load_id, c.submission_id, c.name, c.email_domain,
       c.country_hint, c.role, r.name AS recruiter, c.created_at,
       (SELECT count(*) FROM core.submissions s
         WHERE s.candidate_id = c.candidate_id
           AND s.outcome = 'duplicate_flagged') AS duplicate_attempts
FROM core.candidates c
JOIN raw.load_manifest m ON m.load_id = c.load_id AND m.status = 'loaded'
JOIN ref.recruiters r ON r.recruiter_id = c.recruiter_id;

CREATE OR REPLACE VIEW core.v_submissions AS
SELECT s.load_id, s.line_no, s.submission_id, s.candidate_id, r.name AS recruiter,
       s.outcome, s.matched_on, s.matched_submission_id
FROM core.submissions s
JOIN raw.load_manifest m ON m.load_id = s.load_id AND m.status = 'loaded'
JOIN ref.recruiters r ON r.recruiter_id = s.recruiter_id;

-- Analysts may see that loads happened, and when, but not the path or the
-- error text (which can quote a rejected input line).
CREATE OR REPLACE VIEW core.v_loads AS
SELECT load_id, source_name, status, row_count, started_at, finished_at, supersedes
FROM raw.load_manifest;
