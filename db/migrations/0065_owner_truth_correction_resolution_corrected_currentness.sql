-- migration:owner_truth_correction_resolution_corrected_currentness
--
-- Migration 0058 correctly tightened Source liveness and authority checks, but
-- treated the predecessor MemoryVersion as current for every terminal decision.
-- A corrected decision deliberately activates its successor before inserting
-- the immutable resolution receipt, so the predecessor must already be
-- historical at this trigger boundary. A rejected decision must leave that
-- predecessor current. Keep the applied 0058 checksum immutable and replace
-- only the trigger function with decision-aware currentness validation.
--
-- No Source, Candidate, DecisionReceipt, MemoryVersion, or correction row is
-- rewritten by this compatibility fix. The feature remains default-off.

CREATE OR REPLACE FUNCTION owner_truth.validate_correction_resolution()
RETURNS TRIGGER AS $$
DECLARE
    request_owner_subject_id TEXT;
    request_candidate_id UUID;
    request_memory_id UUID;
    request_expected_version_id UUID;
    request_correction_source_id UUID;
    request_status TEXT;
    candidate_owner_subject_id TEXT;
    candidate_source_id UUID;
    candidate_decision_status TEXT;
    candidate_authority_epoch BIGINT;
    receipt_candidate_id UUID;
    receipt_decision TEXT;
    receipt_authority_epoch BIGINT;
    predecessor_memory_id UUID;
    predecessor_version_number BIGINT;
    predecessor_is_current BOOLEAN;
    predecessor_memory_owner_subject_id TEXT;
    predecessor_memory_status TEXT;
    predecessor_memory_authority_epoch BIGINT;
    predecessor_source_id UUID;
    predecessor_source_version BIGINT;
    predecessor_source_owner_subject_id TEXT;
    predecessor_source_state TEXT;
    predecessor_source_authority_epoch BIGINT;
    predecessor_source_row_version BIGINT;
    correction_source_owner_subject_id TEXT;
    correction_source_state TEXT;
    correction_source_authority_epoch BIGINT;
    replacement_memory_id UUID;
    replacement_version_number BIGINT;
    replacement_is_current BOOLEAN;
    replacement_source_id UUID;
    replacement_receipt_id UUID;
    replacement_supersedes_id UUID;
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    SELECT owner_subject_id, candidate_id, memory_id, expected_memory_version_id,
        correction_source_id, status
    INTO request_owner_subject_id, request_candidate_id, request_memory_id,
        request_expected_version_id, request_correction_source_id, request_status
    FROM owner_truth.correction_requests
    WHERE vault_id = NEW.vault_id AND id = NEW.correction_request_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing request';
    END IF;

    SELECT owner_subject_id, source_id, decision_status, authority_epoch
    INTO candidate_owner_subject_id, candidate_source_id, candidate_decision_status,
        candidate_authority_epoch
    FROM owner_truth.memory_candidates
    WHERE vault_id = NEW.vault_id AND id = NEW.candidate_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing Candidate';
    END IF;

    SELECT candidate_id, decision, authority_epoch
    INTO receipt_candidate_id, receipt_decision, receipt_authority_epoch
    FROM owner_truth.decision_receipts
    WHERE vault_id = NEW.vault_id AND id = NEW.decision_receipt_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing DecisionReceipt';
    END IF;

    SELECT memory.id, version.version_number, version.is_current,
        memory.owner_subject_id, memory.status, memory.authority_epoch,
        version.source_id, version.source_version,
        source.owner_subject_id, source.state, source.authority_epoch, source.source_version
    INTO predecessor_memory_id, predecessor_version_number, predecessor_is_current,
        predecessor_memory_owner_subject_id, predecessor_memory_status,
        predecessor_memory_authority_epoch, predecessor_source_id,
        predecessor_source_version, predecessor_source_owner_subject_id,
        predecessor_source_state, predecessor_source_authority_epoch,
        predecessor_source_row_version
    FROM owner_truth.memory_versions AS version
    JOIN owner_truth.memories AS memory
      ON memory.vault_id = version.vault_id AND memory.id = version.memory_id
    JOIN owner_truth.sources AS source
      ON source.vault_id = version.vault_id AND source.id = version.source_id
    WHERE version.vault_id = NEW.vault_id AND version.id = NEW.expected_memory_version_id
    FOR SHARE OF version, memory, source;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing predecessor version';
    END IF;

    SELECT owner_subject_id, state, authority_epoch
    INTO correction_source_owner_subject_id, correction_source_state,
        correction_source_authority_epoch
    FROM owner_truth.sources
    WHERE vault_id = NEW.vault_id AND id = request_correction_source_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing correction Source';
    END IF;

    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing Vault';
    END IF;

    IF request_status IS DISTINCT FROM 'pending'
        OR NEW.candidate_id IS DISTINCT FROM request_candidate_id
        OR NEW.expected_memory_version_id IS DISTINCT FROM request_expected_version_id
        OR receipt_candidate_id IS DISTINCT FROM request_candidate_id
        OR candidate_owner_subject_id IS DISTINCT FROM request_owner_subject_id
        OR candidate_source_id IS DISTINCT FROM request_correction_source_id
        OR candidate_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR receipt_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR request_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR vault_status IS DISTINCT FROM 'active'
        OR predecessor_memory_id IS DISTINCT FROM request_memory_id
        OR predecessor_memory_owner_subject_id IS DISTINCT FROM request_owner_subject_id
        OR predecessor_memory_status IS DISTINCT FROM 'active'
        OR predecessor_memory_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR predecessor_source_owner_subject_id IS DISTINCT FROM request_owner_subject_id
        OR predecessor_source_state IS DISTINCT FROM 'active'
        OR predecessor_source_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR predecessor_source_row_version IS DISTINCT FROM predecessor_source_version
        OR correction_source_owner_subject_id IS DISTINCT FROM request_owner_subject_id
        OR correction_source_state IS DISTINCT FROM 'active'
        OR correction_source_authority_epoch IS DISTINCT FROM vault_authority_epoch
    THEN
        RAISE EXCEPTION 'owner truth correction resolution does not match a current active Source chain';
    END IF;

    IF NEW.decision IS DISTINCT FROM receipt_decision
        OR NEW.decision IS DISTINCT FROM candidate_decision_status
    THEN
        RAISE EXCEPTION 'owner truth correction resolution decision does not match Candidate receipt';
    END IF;

    IF NEW.decision = 'rejected' THEN
        IF predecessor_is_current IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'owner truth rejected correction resolution requires a current predecessor';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.decision IS DISTINCT FROM 'corrected' THEN
        RAISE EXCEPTION 'owner truth correction resolution decision is unsupported';
    END IF;

    SELECT memory_id, version_number, is_current, source_id, decision_receipt_id,
        supersedes_version_id
    INTO replacement_memory_id, replacement_version_number,
        replacement_is_current, replacement_source_id, replacement_receipt_id,
        replacement_supersedes_id
    FROM owner_truth.memory_versions
    WHERE vault_id = NEW.vault_id AND id = NEW.replacement_memory_version_id;
    IF NOT FOUND
        OR predecessor_is_current IS DISTINCT FROM FALSE
        OR replacement_memory_id IS DISTINCT FROM request_memory_id
        OR replacement_version_number IS DISTINCT FROM predecessor_version_number + 1
        OR replacement_is_current IS NOT TRUE
        OR replacement_source_id IS DISTINCT FROM request_correction_source_id
        OR replacement_receipt_id IS DISTINCT FROM NEW.decision_receipt_id
        OR replacement_supersedes_id IS DISTINCT FROM NEW.expected_memory_version_id
        OR NOT EXISTS (
            SELECT 1
            FROM owner_truth.candidate_decision_values AS value
            WHERE value.vault_id = NEW.vault_id
              AND value.candidate_id = NEW.candidate_id
              AND value.decision_receipt_id = NEW.decision_receipt_id
        )
    THEN
        RAISE EXCEPTION 'owner truth correction resolution successor lineage is invalid';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
