-- migration:publication_share_grant_authority_epoch
--
-- Bind an issued ShareGrant to the same Owner authority epoch that confirmed
-- its independently stored public projection. Visitor admission rechecks this
-- binding under row locks before opening a session.

ALTER TABLE publication.share_grants
    ADD COLUMN IF NOT EXISTS authority_epoch BIGINT;

UPDATE publication.share_grants AS grant
SET authority_epoch = publication.authority_epoch
FROM publication.publications AS publication
WHERE publication.id = grant.publication_id
  AND publication.vault_id = grant.vault_id
  AND grant.authority_epoch IS NULL;

ALTER TABLE publication.share_grants
    ALTER COLUMN authority_epoch SET NOT NULL;

ALTER TABLE publication.share_grants
    ADD CONSTRAINT publication_share_grants_authority_epoch_nonnegative
        CHECK (authority_epoch >= 0);

CREATE OR REPLACE FUNCTION publication.validate_share_grant_authority_epoch()
RETURNS TRIGGER AS $$
DECLARE
    publication_authority_epoch BIGINT;
BEGIN
    SELECT authority_epoch
    INTO publication_authority_epoch
    FROM publication.publications
    WHERE id = NEW.publication_id
      AND vault_id = NEW.vault_id;

    IF NOT FOUND OR NEW.authority_epoch IS DISTINCT FROM publication_authority_epoch THEN
        RAISE EXCEPTION 'share grant authority epoch must match publication authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_share_grants_validate_authority_epoch
BEFORE INSERT OR UPDATE OF vault_id, publication_id, authority_epoch
ON publication.share_grants
FOR EACH ROW EXECUTE FUNCTION publication.validate_share_grant_authority_epoch();

REVOKE ALL ON FUNCTION publication.validate_share_grant_authority_epoch() FROM PUBLIC;
