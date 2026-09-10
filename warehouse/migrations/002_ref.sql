-- 002_ref.sql — reference data the loader checks incoming rows against.
--
-- A row that names a recruiter or a role not present here is a load failure,
-- not a warning. Reference data is seeded by migration (it is part of the
-- schema's meaning), not by the loader.

CREATE TABLE IF NOT EXISTS ref.roles (
    role        text PRIMARY KEY,
    sort_order  smallint NOT NULL UNIQUE
);

INSERT INTO ref.roles (role, sort_order) VALUES
    ('Setter', 1), ('Closer', 2), ('Coach', 3), ('Full Cycle', 4)
ON CONFLICT (role) DO NOTHING;

CREATE TABLE IF NOT EXISTS ref.recruiters (
    recruiter_id  smallserial PRIMARY KEY,
    name          text NOT NULL,
    email         text NOT NULL UNIQUE CHECK (email = lower(btrim(email)))
);

INSERT INTO ref.recruiters (name, email) VALUES
    ('Ana Duarte',   'ana.duarte@intake.demo'),
    ('Ben Kowalski', 'ben.kowalski@intake.demo'),
    ('Chidi Nwosu',  'chidi.nwosu@intake.demo'),
    ('Dana Reyes',   'dana.reyes@intake.demo')
ON CONFLICT (email) DO NOTHING;

-- Same table as REGIONS in n8n/normalize.js. The parity test
-- (tests/test_warehouse.py::test_normalization_matches_normalize_js) fails if
-- the two drift.
CREATE TABLE IF NOT EXISTS ref.phone_regions (
    region  char(2) PRIMARY KEY,
    cc      text    NOT NULL,
    trunk   text    NULL
);

INSERT INTO ref.phone_regions (region, cc, trunk) VALUES
    ('US', '1',   NULL), ('CA', '1',   NULL), ('GB', '44',  '0'),
    ('IE', '353', '0'),  ('KR', '82',  '0'),  ('JP', '81',  '0'),
    ('SE', '46',  '0'),  ('SG', '65',  NULL), ('NZ', '64',  '0'),
    ('NG', '234', '0'),  ('CL', '56',  '0'),  ('GH', '233', '0')
ON CONFLICT (region) DO NOTHING;

-- Lowercase + trim. Nothing else (no Gmail dot/plus collapsing — see the
-- note in normalize.js).
CREATE OR REPLACE FUNCTION ref.email_norm(raw text)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT lower(btrim(raw))
$$;

CREATE OR REPLACE FUNCTION ref.email_domain(raw text)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT CASE WHEN position('@' IN ref.email_norm(raw)) = 0 THEN ''
                ELSE substring(ref.email_norm(raw) FROM '@([^@]*)$') END
$$;

-- E.164 or '' — never a guess. An empty key never matches another row, so
-- an unparseable number degrades to "not a duplicate", not to a false merge.
CREATE OR REPLACE FUNCTION ref.phone_e164(raw text, default_region text)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE
    s       text := btrim(coalesce(raw, ''));
    digits  text;
    has_plus boolean;
    r       ref.phone_regions%ROWTYPE;
    out_    text;
BEGIN
    IF s = '' THEN RETURN ''; END IF;
    has_plus := s LIKE '+%' OR s LIKE '00%';
    digits := regexp_replace(s, '\D', '', 'g');
    IF s LIKE '00%' THEN digits := substr(digits, 3); END IF;

    IF has_plus THEN
        RETURN CASE WHEN length(digits) BETWEEN 8 AND 15 THEN '+' || digits ELSE '' END;
    END IF;

    SELECT * INTO r FROM ref.phone_regions WHERE region = upper(coalesce(default_region, ''));
    IF NOT FOUND THEN RETURN ''; END IF;

    IF r.trunk IS NOT NULL AND digits LIKE r.trunk || '%' THEN
        digits := substr(digits, length(r.trunk) + 1);
    END IF;
    out_ := r.cc || digits;
    RETURN CASE WHEN length(out_) BETWEEN 8 AND 15 THEN '+' || out_ ELSE '' END;
END
$$;
