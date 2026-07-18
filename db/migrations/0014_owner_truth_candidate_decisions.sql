-- migration:owner_truth_candidate_decisions
--
-- Add the Owner-only Candidate decision lane. Candidate payload remains the
-- immutable processor proposal. A corrected value is stored separately from
-- the DecisionReceipt so the receipt can retain only hashes and decision
-- basis while WI-S1-01-05 later creates the authoritative MemoryVersion.

ALTER TABLE owner_truth.decision_receipts
    ADD COLUMN IF NOT EXISTS command_id_hash TEXT,
    ADD COLUMN IF NOT EXISTS payload_hash TEXT,
    ADD COLUMN IF NOT EXISTS expected_candidate_version BIGINT,
    ADD COLUMN IF NOT EXISTS candidate_before_hash TEXT,
    ADD COLUMN IF NOT EXISTS candidate_after_hash TEXT,
    ADD COLUMN IF NOT EXISTS decision_basis JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE owner_truth.decision_receipts
    ADD CONSTRAINT owner_truth_decision_receipts_command_id_hash_not_blank
        CHECK (command_id_hash IS NULL OR BTRIM(command_id_hash) <> ''),
    ADD CONSTRAINT owner_truth_decision_receipts_payload_hash_not_blank
        CHECK (payload_hash IS NULL OR BTRIM(payload_hash) <> ''),
    ADD CONSTRAINT owner_truth_decision_receipts_expected_candidate_version_valid
        CHECK (expected_candidate_version IS NULL OR expected_candidate_version >= 1),
    ADD CONSTRAINT owner_truth_decision_receipts_candidate_before_hash_not_blank
        CHECK (candidate_before_hash IS NULL OR BTRIM(candidate_before_hash) <> ''),
    ADD CONSTRAINT owner_truth_decision_receipts_candidate_after_hash_not_blank
        CHECK (candidate_after_hash IS NULL OR BTRIM(candidate_after_hash) <> ''),
    ADD CONSTRAINT owner_truth_decision_receipts_basis_is_object
        CHECK (jsonb_typeof(decision_basis) = 'object'),
    ADD CONSTRAINT owner_truth_decision_receipts_v2_metadata_complete
        CHECK (
            (
                command_id_hash IS NULL
                AND payload_hash IS NULL
                AND expected_candidate_version IS NULL
                AND candidate_before_hash IS NULL
                AND candidate_after_hash IS NULL
                AND decision_basis = '{}'::JSONB
            )
            OR (
                command_id_hash IS NOT NULL
                AND payload_hash IS NOT NULL
                AND expected_candidate_version IS NOT NULL
                AND candidate_before_hash IS NOT NULL
                AND candidate_after_hash IS NOT NULL
                AND decision_basis ? 'schemaVersion'
                AND decision_basis ? 'reasonCode'
                AND decision_basis ? 'sourceRefs'
            )
        );

CREATE UNIQUE INDEX owner_truth_decision_receipts_vault_command_id_hash_unique
    ON owner_truth.decision_receipts(vault_id, command_id_hash)
    WHERE command_id_hash IS NOT NULL;

CREATE TABLE owner_truth.candidate_decision_values (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    candidate_id UUID NOT NULL,
    decision_receipt_id UUID NOT NULL,
    content_schema_version TEXT NOT NULL CHECK (BTRIM(content_schema_version) <> ''),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    content JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, candidate_id),
    UNIQUE (vault_id, decision_receipt_id),
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_candidate_decision_value()
RETURNS TRIGGER AS $$
DECLARE
    receipt_candidate_id UUID;
    receipt_decision TEXT;
    receipt_after_hash TEXT;
BEGIN
    SELECT candidate_id, decision, candidate_after_hash
    INTO receipt_candidate_id, receipt_decision, receipt_after_hash
    FROM owner_truth.decision_receipts
    WHERE vault_id = NEW.vault_id AND id = NEW.decision_receipt_id;

    IF receipt_candidate_id IS DISTINCT FROM NEW.candidate_id
        OR receipt_decision IS DISTINCT FROM 'corrected'
        OR receipt_after_hash IS DISTINCT FROM NEW.content_hash THEN
        RAISE EXCEPTION 'owner truth corrected decision value must match its receipt';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_candidate_decision_values_validate_receipt
BEFORE INSERT ON owner_truth.candidate_decision_values
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_candidate_decision_value();

CREATE OR REPLACE FUNCTION owner_truth.candidate_decision_values_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth candidate decision values are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_candidate_decision_values_no_update
BEFORE UPDATE ON owner_truth.candidate_decision_values
FOR EACH ROW EXECUTE FUNCTION owner_truth.candidate_decision_values_append_only();

CREATE TRIGGER owner_truth_candidate_decision_values_no_delete
BEFORE DELETE ON owner_truth.candidate_decision_values
FOR EACH ROW EXECUTE FUNCTION owner_truth.candidate_decision_values_append_only();

CREATE OR REPLACE FUNCTION owner_truth.assert_corrected_decision_value_present()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.decision = 'corrected' AND NOT EXISTS (
        SELECT 1
        FROM owner_truth.candidate_decision_values
        WHERE vault_id = NEW.vault_id
          AND decision_receipt_id = NEW.id
          AND candidate_id = NEW.candidate_id
    ) THEN
        RAISE EXCEPTION 'owner truth corrected decision requires one corrected value';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER owner_truth_corrected_decision_requires_value
AFTER INSERT ON owner_truth.decision_receipts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.assert_corrected_decision_value_present();

CREATE INDEX owner_truth_memory_candidates_owner_pending_created
    ON owner_truth.memory_candidates(vault_id, owner_subject_id, created_at DESC)
    WHERE decision_status = 'pending';
