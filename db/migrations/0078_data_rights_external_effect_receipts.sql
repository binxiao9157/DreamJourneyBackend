-- migration:data_rights_external_effect_receipts
--
-- P0-S3 records only hash-only observations linking one data-rights request to
-- an external cleanup domain. It is deliberately append-only and does not
-- store Provider IDs, object keys, media URLs or credential material.

CREATE TABLE rights_external_effect_receipts (
    id TEXT PRIMARY KEY CHECK (id ~ '^dre_[0-9a-f]{40}$'),
    request_id TEXT NOT NULL REFERENCES rights_requests(id) ON DELETE RESTRICT,
    owner_subject_hash TEXT NOT NULL CHECK (owner_subject_hash ~ '^[0-9a-f]{64}$'),
    domain TEXT NOT NULL CHECK (domain IN (
        'objectStorage',
        'providerVoice',
        'providerDigitalHuman',
        'notificationDelivery',
        'backupRetention'
    )),
    effect_identity_hash TEXT NOT NULL CHECK (effect_identity_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'accepted', 'completed', 'failed', 'unknown', 'unsupported'
    )),
    provider_receipt_present BOOLEAN NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'),
    observation_hash TEXT NOT NULL UNIQUE CHECK (observation_hash ~ '^[0-9a-f]{64}$'),
    observed_at TIMESTAMPTZ NOT NULL,
    evidence_hash TEXT CHECK (evidence_hash IS NULL OR evidence_hash ~ '^[0-9a-f]{64}$'),
    retention_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (retention_until IS NULL OR retention_until >= observed_at)
);

CREATE INDEX idx_rights_external_effect_receipts_request_domain
    ON rights_external_effect_receipts(request_id, domain, observed_at DESC);

CREATE OR REPLACE FUNCTION validate_rights_external_effect_receipt_owner()
RETURNS trigger AS $$
DECLARE
    request_subject_hash TEXT;
BEGIN
    SELECT request.subject_id
    INTO request_subject_hash
    FROM rights_requests AS request
    WHERE request.id = NEW.request_id
    FOR SHARE;

    IF NOT FOUND OR request_subject_hash IS DISTINCT FROM NEW.owner_subject_hash THEN
        RAISE EXCEPTION 'external effect receipt owner does not match rights request';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER rights_external_effect_receipts_validate_owner
BEFORE INSERT ON rights_external_effect_receipts
FOR EACH ROW EXECUTE FUNCTION validate_rights_external_effect_receipt_owner();

CREATE OR REPLACE FUNCTION reject_rights_external_effect_receipt_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'rights_external_effect_receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER rights_external_effect_receipts_no_update
BEFORE UPDATE OR DELETE ON rights_external_effect_receipts
FOR EACH ROW EXECUTE FUNCTION reject_rights_external_effect_receipt_mutation();
