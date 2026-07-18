-- migration:owner_truth_memory_activation
--
-- Bind the first authoritative MemoryRecord/MemoryVersion to the immutable
-- Owner DecisionReceipt that admitted it.  Legacy Owner Truth memories remain
-- valid with a NULL receipt link; new receipt-derived activation is additive
-- and default-off at the route/release-policy layer.

ALTER TABLE owner_truth.memories
    ADD COLUMN IF NOT EXISTS decision_receipt_id UUID;

ALTER TABLE owner_truth.memories
    ADD CONSTRAINT owner_truth_memories_decision_receipt_fk
        FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT;

CREATE UNIQUE INDEX owner_truth_memories_one_per_decision_receipt
    ON owner_truth.memories(vault_id, decision_receipt_id)
    WHERE decision_receipt_id IS NOT NULL;

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_decision_receipt()
RETURNS TRIGGER AS $$
DECLARE
    receipt_candidate_id UUID;
    receipt_decision TEXT;
    receipt_policy_version TEXT;
    receipt_authority_epoch BIGINT;
    receipt_after_hash TEXT;
    candidate_owner_subject_id TEXT;
    candidate_source_id UUID;
    candidate_memory_kind TEXT;
    candidate_perspective_type TEXT;
    candidate_epistemic_status TEXT;
    candidate_sensitivity TEXT;
    candidate_policy_version TEXT;
    candidate_authority_epoch BIGINT;
    source_owner_subject_id TEXT;
    source_state TEXT;
    source_version BIGINT;
BEGIN
    IF NEW.decision_receipt_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT
        receipt.candidate_id,
        receipt.decision,
        receipt.policy_version,
        receipt.authority_epoch,
        receipt.candidate_after_hash,
        candidate.owner_subject_id,
        candidate.source_id,
        candidate.candidate_kind,
        candidate.perspective_type,
        candidate.epistemic_status,
        candidate.sensitivity,
        candidate.policy_version,
        candidate.authority_epoch,
        source.owner_subject_id,
        source.state,
        source.source_version
    INTO
        receipt_candidate_id,
        receipt_decision,
        receipt_policy_version,
        receipt_authority_epoch,
        receipt_after_hash,
        candidate_owner_subject_id,
        candidate_source_id,
        candidate_memory_kind,
        candidate_perspective_type,
        candidate_epistemic_status,
        candidate_sensitivity,
        candidate_policy_version,
        candidate_authority_epoch,
        source_owner_subject_id,
        source_state,
        source_version
    FROM owner_truth.decision_receipts AS receipt
    JOIN owner_truth.memory_candidates AS candidate
      ON candidate.vault_id = receipt.vault_id
     AND candidate.id = receipt.candidate_id
    JOIN owner_truth.sources AS source
      ON source.vault_id = candidate.vault_id
     AND source.id = candidate.source_id
    WHERE receipt.vault_id = NEW.vault_id
      AND receipt.id = NEW.decision_receipt_id;

    IF NOT FOUND
        OR receipt_decision NOT IN ('accepted', 'corrected')
        OR NEW.owner_subject_id IS DISTINCT FROM candidate_owner_subject_id
        OR NEW.source_id IS DISTINCT FROM candidate_source_id
        OR NEW.source_version IS DISTINCT FROM source_version
        OR NEW.memory_kind IS DISTINCT FROM candidate_memory_kind
        OR NEW.perspective_type IS DISTINCT FROM candidate_perspective_type
        OR NEW.epistemic_status IS DISTINCT FROM candidate_epistemic_status
        OR NEW.sensitivity IS DISTINCT FROM candidate_sensitivity
        OR NEW.policy_version IS DISTINCT FROM candidate_policy_version
        OR NEW.policy_version IS DISTINCT FROM receipt_policy_version
        OR NEW.authority_epoch IS DISTINCT FROM candidate_authority_epoch
        OR NEW.authority_epoch IS DISTINCT FROM receipt_authority_epoch
        OR NEW.content_hash IS DISTINCT FROM receipt_after_hash
        OR NEW.status IS DISTINCT FROM 'active'
        OR source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR source_state IS DISTINCT FROM 'active'
    THEN
        RAISE EXCEPTION 'owner truth memory must match an active accepted/corrected DecisionReceipt';
    END IF;

    IF receipt_decision = 'corrected' AND NOT EXISTS (
        SELECT 1
        FROM owner_truth.candidate_decision_values AS decision_value
        WHERE decision_value.vault_id = NEW.vault_id
          AND decision_value.decision_receipt_id = NEW.decision_receipt_id
          AND decision_value.candidate_id = receipt_candidate_id
          AND decision_value.content_hash = NEW.content_hash
    ) THEN
        RAISE EXCEPTION 'owner truth corrected memory must use its immutable corrected value';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memories_validate_decision_receipt
BEFORE INSERT OR UPDATE OF decision_receipt_id, owner_subject_id, source_id,
    source_version, memory_kind, perspective_type, epistemic_status,
    sensitivity, status, policy_version, content_hash, authority_epoch
ON owner_truth.memories
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_decision_receipt();

CREATE OR REPLACE FUNCTION owner_truth.memory_decision_receipt_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.decision_receipt_id IS DISTINCT FROM OLD.decision_receipt_id THEN
        RAISE EXCEPTION 'owner truth memory DecisionReceipt binding is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memories_decision_receipt_immutable
BEFORE UPDATE OF decision_receipt_id ON owner_truth.memories
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_decision_receipt_immutable();
