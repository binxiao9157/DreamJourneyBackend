-- migration:owner_truth_correction_resolver
--
-- Complete the QA-only correction lane without changing public Echo reads.
-- A correction resolution must create a successor of the same MemoryRecord,
-- retain the cited Answer/Citation as immutable evidence, and emit an
-- append-only outdated event.  No resolver row may exist without the matching
-- Owner DecisionReceipt and version lineage.

CREATE TABLE owner_truth.correction_resolutions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    correction_request_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    decision_receipt_id UUID NOT NULL,
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    expected_memory_version_id UUID NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('corrected', 'rejected')),
    replacement_memory_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, correction_request_id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, decision_receipt_id),
    FOREIGN KEY (vault_id, correction_request_id)
        REFERENCES owner_truth.correction_requests(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, expected_memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, replacement_memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (decision = 'corrected' AND replacement_memory_version_id IS NOT NULL)
        OR (decision = 'rejected' AND replacement_memory_version_id IS NULL)
    )
);

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

    SELECT memory_id, version_number, is_current
    INTO predecessor_memory_id, predecessor_version_number, predecessor_is_current
    FROM owner_truth.memory_versions
    WHERE vault_id = NEW.vault_id AND id = NEW.expected_memory_version_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth correction resolution references a missing predecessor version';
    END IF;

    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;
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
    THEN
        RAISE EXCEPTION 'owner truth correction resolution does not match pending Owner authority';
    END IF;

    IF NEW.decision IS DISTINCT FROM receipt_decision
        OR NEW.decision IS DISTINCT FROM candidate_decision_status
    THEN
        RAISE EXCEPTION 'owner truth correction resolution decision does not match Candidate receipt';
    END IF;

    IF NEW.decision = 'rejected' THEN
        RETURN NEW;
    END IF;

    SELECT memory_id, version_number, is_current, source_id, decision_receipt_id,
        supersedes_version_id
    INTO replacement_memory_id, replacement_version_number,
        replacement_is_current, replacement_source_id, replacement_receipt_id,
        replacement_supersedes_id
    FROM owner_truth.memory_versions
    WHERE vault_id = NEW.vault_id AND id = NEW.replacement_memory_version_id;
    IF NOT FOUND
        OR predecessor_is_current IS NOT FALSE
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

CREATE TRIGGER owner_truth_correction_resolutions_validate
BEFORE INSERT ON owner_truth.correction_resolutions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_correction_resolution();

CREATE OR REPLACE FUNCTION owner_truth.correction_resolution_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth correction resolutions are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_correction_resolutions_no_update
BEFORE UPDATE ON owner_truth.correction_resolutions
FOR EACH ROW EXECUTE FUNCTION owner_truth.correction_resolution_append_only();

CREATE TRIGGER owner_truth_correction_resolutions_no_delete
BEFORE DELETE ON owner_truth.correction_resolutions
FOR EACH ROW EXECUTE FUNCTION owner_truth.correction_resolution_append_only();

CREATE TABLE owner_truth.answer_outdated_events (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    correction_resolution_id UUID NOT NULL,
    answer_id UUID NOT NULL,
    citation_id UUID NOT NULL,
    memory_id UUID NOT NULL,
    superseded_memory_version_id UUID NOT NULL,
    replacement_memory_version_id UUID NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, correction_resolution_id),
    FOREIGN KEY (vault_id, correction_resolution_id)
        REFERENCES owner_truth.correction_resolutions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, answer_id)
        REFERENCES owner_truth.answers(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, citation_id)
        REFERENCES owner_truth.answer_citations(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, superseded_memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, replacement_memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_answer_outdated_event()
RETURNS TRIGGER AS $$
DECLARE
    resolution_request_id UUID;
    resolution_decision TEXT;
    resolution_expected_version_id UUID;
    resolution_replacement_version_id UUID;
    request_answer_id UUID;
    request_citation_id UUID;
    request_memory_id UUID;
    request_expected_version_id UUID;
    vault_authority_epoch BIGINT;
BEGIN
    SELECT correction_request_id, decision, expected_memory_version_id,
        replacement_memory_version_id
    INTO resolution_request_id, resolution_decision, resolution_expected_version_id,
        resolution_replacement_version_id
    FROM owner_truth.correction_resolutions
    WHERE vault_id = NEW.vault_id AND id = NEW.correction_resolution_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth Answer outdated event references a missing correction resolution';
    END IF;

    SELECT answer_id, citation_id, memory_id, expected_memory_version_id
    INTO request_answer_id, request_citation_id, request_memory_id,
        request_expected_version_id
    FROM owner_truth.correction_requests
    WHERE vault_id = NEW.vault_id AND id = resolution_request_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'owner truth Answer outdated event references a missing correction request';
    END IF;

    SELECT authority_epoch
    INTO vault_authority_epoch
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;
    IF NOT FOUND
        OR resolution_decision IS DISTINCT FROM 'corrected'
        OR NEW.answer_id IS DISTINCT FROM request_answer_id
        OR NEW.citation_id IS DISTINCT FROM request_citation_id
        OR NEW.memory_id IS DISTINCT FROM request_memory_id
        OR NEW.superseded_memory_version_id IS DISTINCT FROM resolution_expected_version_id
        OR NEW.superseded_memory_version_id IS DISTINCT FROM request_expected_version_id
        OR NEW.replacement_memory_version_id IS DISTINCT FROM resolution_replacement_version_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
    THEN
        RAISE EXCEPTION 'owner truth Answer outdated event does not match its correction resolution';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answer_outdated_events_validate
BEFORE INSERT ON owner_truth.answer_outdated_events
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_answer_outdated_event();

CREATE OR REPLACE FUNCTION owner_truth.answer_outdated_event_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth Answer outdated events are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_answer_outdated_events_no_update
BEFORE UPDATE ON owner_truth.answer_outdated_events
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_outdated_event_append_only();

CREATE TRIGGER owner_truth_answer_outdated_events_no_delete
BEFORE DELETE ON owner_truth.answer_outdated_events
FOR EACH ROW EXECUTE FUNCTION owner_truth.answer_outdated_event_append_only();

CREATE OR REPLACE FUNCTION owner_truth.validate_correction_request_status_transition()
RETURNS TRIGGER AS $$
DECLARE
    resolution_decision TEXT;
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;
    IF OLD.status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'owner truth correction request terminal status is immutable';
    END IF;
    IF NEW.status = 'invalidated' THEN
        -- Source-removal invalidation remains a future explicit worker path.
        RETURN NEW;
    END IF;
    SELECT decision
    INTO resolution_decision
    FROM owner_truth.correction_resolutions
    WHERE vault_id = NEW.vault_id AND correction_request_id = NEW.id;
    IF NOT FOUND
        OR (NEW.status = 'accepted' AND resolution_decision IS DISTINCT FROM 'corrected')
        OR (NEW.status = 'rejected' AND resolution_decision IS DISTINCT FROM 'rejected')
        OR NEW.status NOT IN ('accepted', 'rejected')
    THEN
        RAISE EXCEPTION 'owner truth correction request terminal status requires a matching resolution';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_correction_requests_status_transition
BEFORE UPDATE OF status ON owner_truth.correction_requests
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_correction_request_status_transition();

CREATE INDEX owner_truth_correction_resolutions_request_created
    ON owner_truth.correction_resolutions(vault_id, correction_request_id, created_at DESC);

CREATE INDEX owner_truth_answer_outdated_events_answer_created
    ON owner_truth.answer_outdated_events(vault_id, answer_id, created_at DESC);
