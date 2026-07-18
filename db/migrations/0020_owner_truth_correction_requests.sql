-- migration:owner_truth_correction_requests
--
-- Add the default-off, QA-only first half of the Answer correction flow.
-- A request binds an immutable Answer/Citation to a private correction Source
-- and a pending Candidate. It cannot replace a MemoryVersion by itself.

CREATE TABLE owner_truth.correction_requests (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[a-f0-9]{64}$'),
    command_payload_hash TEXT NOT NULL CHECK (command_payload_hash ~ '^[a-f0-9]{64}$'),
    answer_id UUID NOT NULL,
    citation_id UUID NOT NULL,
    memory_id UUID NOT NULL,
    expected_memory_version_id UUID NOT NULL,
    correction_source_id UUID NOT NULL,
    correction_text_hash TEXT NOT NULL CHECK (correction_text_hash ~ '^[a-f0-9]{64}$'),
    correction_text_length INTEGER NOT NULL CHECK (correction_text_length > 0),
    reason_code_hash TEXT NOT NULL CHECK (reason_code_hash ~ '^[a-f0-9]{64}$'),
    candidate_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'rejected', 'invalidated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    UNIQUE (vault_id, candidate_id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
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
    FOREIGN KEY (vault_id, expected_memory_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, correction_source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_correction_request()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    answer_owner_subject_id TEXT;
    answer_authority_epoch BIGINT;
    citation_answer_id UUID;
    citation_memory_id UUID;
    citation_memory_version_id UUID;
    citation_memory_version BIGINT;
    citation_source_id UUID;
    citation_source_version BIGINT;
    citation_content_hash TEXT;
    memory_owner_subject_id TEXT;
    memory_source_id UUID;
    memory_source_version BIGINT;
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    version_memory_id UUID;
    version_number_value BIGINT;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
    source_owner_subject_id TEXT;
    source_version_value BIGINT;
    source_state TEXT;
    source_authority_epoch BIGINT;
    correction_source_owner_subject_id TEXT;
    correction_source_state TEXT;
    correction_source_authority_epoch BIGINT;
    candidate_owner_subject_id TEXT;
    candidate_source_id UUID;
    candidate_decision_status TEXT;
    candidate_authority_epoch BIGINT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT owner_subject_id, authority_epoch
    INTO answer_owner_subject_id, answer_authority_epoch
    FROM owner_truth.answers
    WHERE vault_id = NEW.vault_id AND id = NEW.answer_id;

    SELECT answer_id, memory_id, memory_version_id, memory_version,
        source_id, source_version, content_hash
    INTO citation_answer_id, citation_memory_id, citation_memory_version_id,
        citation_memory_version, citation_source_id, citation_source_version,
        citation_content_hash
    FROM owner_truth.answer_citations
    WHERE vault_id = NEW.vault_id AND id = NEW.citation_id;

    SELECT owner_subject_id, source_id, source_version, status, authority_epoch
    INTO memory_owner_subject_id, memory_source_id, memory_source_version,
        memory_status, memory_authority_epoch
    FROM owner_truth.memories
    WHERE vault_id = NEW.vault_id AND id = NEW.memory_id;

    SELECT memory_id, version_number, is_current, content_hash
    INTO version_memory_id, version_number_value, version_is_current, version_content_hash
    FROM owner_truth.memory_versions
    WHERE vault_id = NEW.vault_id AND id = NEW.expected_memory_version_id;

    SELECT owner_subject_id, source_version, state, authority_epoch
    INTO source_owner_subject_id, source_version_value, source_state, source_authority_epoch
    FROM owner_truth.sources
    WHERE vault_id = NEW.vault_id AND id = memory_source_id;

    SELECT owner_subject_id, state, authority_epoch
    INTO correction_source_owner_subject_id, correction_source_state,
        correction_source_authority_epoch
    FROM owner_truth.sources
    WHERE vault_id = NEW.vault_id AND id = NEW.correction_source_id;

    SELECT owner_subject_id, source_id, decision_status, authority_epoch
    INTO candidate_owner_subject_id, candidate_source_id, candidate_decision_status,
        candidate_authority_epoch
    FROM owner_truth.memory_candidates
    WHERE vault_id = NEW.vault_id AND id = NEW.candidate_id;

    IF NOT FOUND
        OR vault_status IS DISTINCT FROM 'active'
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR answer_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR answer_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR citation_answer_id IS DISTINCT FROM NEW.answer_id
        OR citation_memory_id IS DISTINCT FROM NEW.memory_id
        OR citation_memory_version_id IS DISTINCT FROM NEW.expected_memory_version_id
        OR memory_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR version_memory_id IS DISTINCT FROM NEW.memory_id
        OR version_number_value IS DISTINCT FROM citation_memory_version
        OR version_is_current IS DISTINCT FROM TRUE
        OR version_content_hash IS DISTINCT FROM citation_content_hash
        OR memory_source_id IS DISTINCT FROM citation_source_id
        OR memory_source_version IS DISTINCT FROM citation_source_version
        OR source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR source_state IS DISTINCT FROM 'active'
        OR source_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR source_version_value IS DISTINCT FROM memory_source_version
        OR correction_source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR correction_source_state IS DISTINCT FROM 'active'
        OR correction_source_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR candidate_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR candidate_source_id IS DISTINCT FROM NEW.correction_source_id
        OR candidate_decision_status IS DISTINCT FROM 'pending'
        OR candidate_authority_epoch IS DISTINCT FROM vault_authority_epoch
    THEN
        RAISE EXCEPTION 'owner truth correction request is not bound to an active cited MemoryVersion';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_correction_requests_validate_target
BEFORE INSERT ON owner_truth.correction_requests
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_correction_request();

CREATE OR REPLACE FUNCTION owner_truth.correction_request_immutable_fields()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.vault_id IS DISTINCT FROM OLD.vault_id
        OR NEW.owner_subject_id IS DISTINCT FROM OLD.owner_subject_id
        OR NEW.command_id_hash IS DISTINCT FROM OLD.command_id_hash
        OR NEW.command_payload_hash IS DISTINCT FROM OLD.command_payload_hash
        OR NEW.answer_id IS DISTINCT FROM OLD.answer_id
        OR NEW.citation_id IS DISTINCT FROM OLD.citation_id
        OR NEW.memory_id IS DISTINCT FROM OLD.memory_id
        OR NEW.expected_memory_version_id IS DISTINCT FROM OLD.expected_memory_version_id
        OR NEW.correction_source_id IS DISTINCT FROM OLD.correction_source_id
        OR NEW.correction_text_hash IS DISTINCT FROM OLD.correction_text_hash
        OR NEW.correction_text_length IS DISTINCT FROM OLD.correction_text_length
        OR NEW.reason_code_hash IS DISTINCT FROM OLD.reason_code_hash
        OR NEW.candidate_id IS DISTINCT FROM OLD.candidate_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'owner truth correction request evidence is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_correction_requests_immutable_fields
BEFORE UPDATE ON owner_truth.correction_requests
FOR EACH ROW EXECUTE FUNCTION owner_truth.correction_request_immutable_fields();

CREATE OR REPLACE FUNCTION owner_truth.correction_request_no_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth correction requests are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_correction_requests_no_delete
BEFORE DELETE ON owner_truth.correction_requests
FOR EACH ROW EXECUTE FUNCTION owner_truth.correction_request_no_delete();

CREATE INDEX owner_truth_correction_requests_pending
    ON owner_truth.correction_requests(vault_id, owner_subject_id, created_at ASC)
    WHERE status = 'pending';

CREATE INDEX owner_truth_correction_requests_memory_version
    ON owner_truth.correction_requests(vault_id, memory_id, expected_memory_version_id, created_at DESC);
