-- 005_restricted.sql — contact details, one row per candidate.
--
-- The dedupe keys (email_norm, phone_e164) are computed by the database from
-- the stored raw value, not supplied by the loader. Whoever writes here
-- cannot write an email whose normalized key disagrees with it.

CREATE TABLE IF NOT EXISTS restricted.candidate_contact (
    candidate_id    uuid    PRIMARY KEY REFERENCES core.candidates (candidate_id),
    load_id         uuid    NOT NULL REFERENCES raw.load_manifest (load_id),
    email           text    NOT NULL,
    email_norm      text    NOT NULL GENERATED ALWAYS AS (ref.email_norm(email)) STORED,
    phone           text    NULL,
    phone_region    char(2) NULL,       -- country_hint at submission time
    phone_e164      text    NOT NULL DEFAULT '',   -- set by trigger; '' when unparseable
    CHECK (email_norm <> ''),
    CHECK (phone_e164 = '' OR phone_e164 ~ '^\+[0-9]{8,15}$')
);

-- ref.phone_e164 reads ref.phone_regions, so it is STABLE, not IMMUTABLE,
-- and cannot back a generated column. A trigger does the same job.
CREATE OR REPLACE FUNCTION restricted.set_phone_e164()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.phone_e164 := ref.phone_e164(NEW.phone, NEW.phone_region);
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS candidate_contact_phone_e164 ON restricted.candidate_contact;
CREATE TRIGGER candidate_contact_phone_e164
    BEFORE INSERT OR UPDATE OF phone, phone_region ON restricted.candidate_contact
    FOR EACH ROW EXECUTE FUNCTION restricted.set_phone_e164();

-- Within one load, a normalized email identifies one candidate. Across
-- loads the same person may legitimately appear again (a superseding load
-- re-creates everything), so uniqueness is per load.
CREATE UNIQUE INDEX IF NOT EXISTS candidate_contact_email_per_load
    ON restricted.candidate_contact (load_id, email_norm);

-- Same for phones, ignoring the ones we could not normalize.
CREATE UNIQUE INDEX IF NOT EXISTS candidate_contact_phone_per_load
    ON restricted.candidate_contact (load_id, phone_e164) WHERE phone_e164 <> '';
