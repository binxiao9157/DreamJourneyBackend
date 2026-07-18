-- migration:owner_truth_memory_version_provenance
--
-- A MemoryRecord keeps its initial admission metadata for legacy compatibility,
-- but every immutable MemoryVersion must carry its own source and receipt
-- lineage. This makes a later correction version cite the correction source
-- rather than silently inheriting the first version's source.

ALTER TABLE owner_truth.memory_versions
    ADD COLUMN IF NOT EXISTS source_id UUID,
    ADD COLUMN IF NOT EXISTS source_version BIGINT,
    ADD COLUMN IF NOT EXISTS decision_receipt_id UUID,
    ADD COLUMN IF NOT EXISTS supersedes_version_id UUID;

ALTER TABLE owner_truth.memory_versions
    ADD CONSTRAINT owner_truth_memory_versions_source_pair
        CHECK (
            (source_id IS NULL AND source_version IS NULL)
            OR (source_id IS NOT NULL AND source_version IS NOT NULL AND source_version >= 1)
        ),
    ADD CONSTRAINT owner_truth_memory_versions_supersedes_not_self
        CHECK (supersedes_version_id IS NULL OR supersedes_version_id <> id),
    ADD CONSTRAINT owner_truth_memory_versions_source_fk
        FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT owner_truth_memory_versions_decision_receipt_fk
        FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT owner_truth_memory_versions_supersedes_fk
        FOREIGN KEY (vault_id, supersedes_version_id)
        REFERENCES owner_truth.memory_versions(vault_id, id)
        ON DELETE RESTRICT;

-- Existing shadow rows predate version-level provenance. Backfill only from
-- the immutable first-admission binding; newer writes provide these values
-- explicitly through the application contract below.
UPDATE owner_truth.memory_versions AS version
SET source_id = memory.source_id,
    source_version = memory.source_version,
    decision_receipt_id = memory.decision_receipt_id
FROM owner_truth.memories AS memory
WHERE memory.vault_id = version.vault_id
  AND memory.id = version.memory_id
  AND version.version_number = 1
  AND (
      version.source_id IS NULL
      OR version.source_version IS NULL
      OR version.decision_receipt_id IS NULL
  );

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_version_provenance()
RETURNS TRIGGER AS $$
DECLARE
    memory_owner_subject_id TEXT;
    memory_authority_epoch BIGINT;
    memory_status TEXT;
    source_owner_subject_id TEXT;
    source_state TEXT;
    source_version_value BIGINT;
    source_authority_epoch BIGINT;
    receipt_decision TEXT;
    receipt_authority_epoch BIGINT;
    candidate_owner_subject_id TEXT;
    candidate_source_id UUID;
    candidate_decision_status TEXT;
    candidate_authority_epoch BIGINT;
    predecessor_memory_id UUID;
    predecessor_version_number BIGINT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO memory_owner_subject_id, memory_authority_epoch, memory_status
    FROM owner_truth.memories
    WHERE vault_id = NEW.vault_id AND id = NEW.memory_id;

    IF NOT FOUND OR memory_status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'owner truth MemoryVersion must belong to an active MemoryRecord';
    END IF;

    IF NEW.source_id IS NOT NULL THEN
        SELECT owner_subject_id, state, source_version, authority_epoch
        INTO source_owner_subject_id, source_state, source_version_value, source_authority_epoch
        FROM owner_truth.sources
        WHERE vault_id = NEW.vault_id AND id = NEW.source_id;

        IF NOT FOUND
            OR source_owner_subject_id IS DISTINCT FROM memory_owner_subject_id
            OR source_state IS DISTINCT FROM 'active'
            OR source_version_value IS DISTINCT FROM NEW.source_version
            OR source_authority_epoch IS DISTINCT FROM memory_authority_epoch
        THEN
            RAISE EXCEPTION 'owner truth MemoryVersion source provenance is not active for this Owner Vault';
        END IF;
    END IF;

    IF NEW.decision_receipt_id IS NOT NULL THEN
        SELECT receipt.decision, receipt.authority_epoch,
            candidate.owner_subject_id, candidate.source_id, candidate.decision_status,
            candidate.authority_epoch
        INTO receipt_decision, receipt_authority_epoch,
            candidate_owner_subject_id, candidate_source_id, candidate_decision_status,
            candidate_authority_epoch
        FROM owner_truth.decision_receipts AS receipt
        JOIN owner_truth.memory_candidates AS candidate
          ON candidate.vault_id = receipt.vault_id
         AND candidate.id = receipt.candidate_id
        WHERE receipt.vault_id = NEW.vault_id AND receipt.id = NEW.decision_receipt_id;

        IF NOT FOUND
            OR receipt_decision NOT IN ('accepted', 'corrected')
            OR candidate_decision_status IS DISTINCT FROM receipt_decision
            OR candidate_owner_subject_id IS DISTINCT FROM memory_owner_subject_id
            OR candidate_authority_epoch IS DISTINCT FROM memory_authority_epoch
            OR receipt_authority_epoch IS DISTINCT FROM memory_authority_epoch
            OR candidate_source_id IS DISTINCT FROM NEW.source_id
        THEN
            RAISE EXCEPTION 'owner truth MemoryVersion DecisionReceipt does not match its source provenance';
        END IF;
    END IF;

    IF NEW.version_number = 1 AND NEW.supersedes_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'owner truth first MemoryVersion cannot supersede another version';
    END IF;
    IF NEW.version_number > 1 AND NEW.supersedes_version_id IS NULL THEN
        RAISE EXCEPTION 'owner truth replacement MemoryVersion must name its superseded version';
    END IF;
    IF NEW.supersedes_version_id IS NOT NULL THEN
        SELECT memory_id, version_number
        INTO predecessor_memory_id, predecessor_version_number
        FROM owner_truth.memory_versions
        WHERE vault_id = NEW.vault_id AND id = NEW.supersedes_version_id;

        IF NOT FOUND
            OR predecessor_memory_id IS DISTINCT FROM NEW.memory_id
            OR predecessor_version_number >= NEW.version_number
        THEN
            RAISE EXCEPTION 'owner truth MemoryVersion supersession chain is invalid';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_versions_validate_provenance
BEFORE INSERT ON owner_truth.memory_versions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_version_provenance();

CREATE OR REPLACE FUNCTION owner_truth.memory_version_provenance_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.vault_id IS DISTINCT FROM OLD.vault_id
        OR NEW.memory_id IS DISTINCT FROM OLD.memory_id
        OR NEW.version_number IS DISTINCT FROM OLD.version_number
        OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
        OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
        OR NEW.payload IS DISTINCT FROM OLD.payload
        OR NEW.source_id IS DISTINCT FROM OLD.source_id
        OR NEW.source_version IS DISTINCT FROM OLD.source_version
        OR NEW.decision_receipt_id IS DISTINCT FROM OLD.decision_receipt_id
        OR NEW.supersedes_version_id IS DISTINCT FROM OLD.supersedes_version_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
    THEN
        RAISE EXCEPTION 'owner truth MemoryVersion provenance is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_versions_provenance_immutable
BEFORE UPDATE ON owner_truth.memory_versions
FOR EACH ROW EXECUTE FUNCTION owner_truth.memory_version_provenance_immutable();

-- Projection, Citation and correction validation must derive a source from the
-- cited MemoryVersion, not from the MemoryRecord's first admission.
CREATE OR REPLACE FUNCTION owner_truth.validate_memory_projection_entry()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    memory_owner_subject_id TEXT;
    memory_kind_value TEXT;
    memory_perspective_type TEXT;
    memory_epistemic_status TEXT;
    memory_sensitivity TEXT;
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    source_owner_subject_id TEXT;
    source_state TEXT;
    source_version_value BIGINT;
    source_authority_epoch BIGINT;
    version_source_id UUID;
    version_source_version BIGINT;
    version_number_value BIGINT;
    version_is_current BOOLEAN;
    version_schema_version TEXT;
    version_content_hash TEXT;
    version_payload JSONB;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT
        memory.owner_subject_id,
        memory.memory_kind,
        memory.perspective_type,
        memory.epistemic_status,
        memory.sensitivity,
        memory.status,
        memory.authority_epoch,
        source.owner_subject_id,
        source.state,
        source.source_version,
        source.authority_epoch,
        version.source_id,
        version.source_version,
        version.version_number,
        version.is_current,
        version.schema_version,
        version.content_hash,
        version.payload
    INTO
        memory_owner_subject_id,
        memory_kind_value,
        memory_perspective_type,
        memory_epistemic_status,
        memory_sensitivity,
        memory_status,
        memory_authority_epoch,
        source_owner_subject_id,
        source_state,
        source_version_value,
        source_authority_epoch,
        version_source_id,
        version_source_version,
        version_number_value,
        version_is_current,
        version_schema_version,
        version_content_hash,
        version_payload
    FROM owner_truth.memories AS memory
    JOIN owner_truth.memory_versions AS version
      ON version.vault_id = memory.vault_id
     AND version.memory_id = memory.id
    JOIN owner_truth.sources AS source
      ON source.vault_id = version.vault_id
     AND source.id = version.source_id
    WHERE memory.vault_id = NEW.vault_id
      AND memory.id = NEW.memory_id
      AND version.id = NEW.memory_version_id;

    IF NOT FOUND
        OR vault_status IS DISTINCT FROM 'active'
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR memory_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR version_source_id IS DISTINCT FROM NEW.source_id
        OR version_source_version IS DISTINCT FROM NEW.source_version
        OR source_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR source_state IS DISTINCT FROM 'active'
        OR source_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR source_version_value IS DISTINCT FROM NEW.source_version
        OR NEW.memory_kind IS DISTINCT FROM memory_kind_value
        OR NEW.perspective_type IS DISTINCT FROM memory_perspective_type
        OR NEW.epistemic_status IS DISTINCT FROM memory_epistemic_status
        OR NEW.sensitivity IS DISTINCT FROM memory_sensitivity
        OR NEW.version_number IS DISTINCT FROM version_number_value
        OR version_is_current IS DISTINCT FROM TRUE
        OR NEW.content_schema_version IS DISTINCT FROM version_schema_version
        OR NEW.content_schema_version IS DISTINCT FROM (version_payload ->> 'contentSchemaVersion')
        OR NEW.content_hash IS DISTINCT FROM version_content_hash
        OR NOT (NEW.payload ? 'content')
        OR NOT (NEW.payload ? 'evidenceRefs')
        OR (NEW.payload - ARRAY['content', 'evidenceRefs']) <> '{}'::JSONB
        OR NEW.payload -> 'content' IS DISTINCT FROM version_payload -> 'content'
        OR NEW.payload -> 'evidenceRefs' IS DISTINCT FROM version_payload -> 'evidenceRefs'
    THEN
        RAISE EXCEPTION 'owner truth projection entry is not a current authorized MemoryVersion';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION owner_truth.validate_answer_citation()
RETURNS TRIGGER AS $$
DECLARE
    answer_vault_id TEXT;
    answer_owner_subject_id TEXT;
    answer_authority_epoch BIGINT;
    memory_vault_id TEXT;
    memory_owner_subject_id TEXT;
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    version_vault_id TEXT;
    version_memory_id UUID;
    version_number_value BIGINT;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
    version_source_id UUID;
    version_source_version BIGINT;
    source_vault_id TEXT;
    source_owner_subject_id TEXT;
    source_version_value BIGINT;
    source_state TEXT;
    source_authority_epoch BIGINT;
BEGIN
    SELECT answer.vault_id, answer.owner_subject_id, answer.authority_epoch
    INTO answer_vault_id, answer_owner_subject_id, answer_authority_epoch
    FROM owner_truth.answers AS answer
    WHERE answer.vault_id = NEW.vault_id AND answer.id = NEW.answer_id;

    SELECT memory.vault_id, memory.owner_subject_id, memory.status, memory.authority_epoch
    INTO memory_vault_id, memory_owner_subject_id, memory_status, memory_authority_epoch
    FROM owner_truth.memories AS memory
    WHERE memory.vault_id = NEW.vault_id AND memory.id = NEW.memory_id;

    SELECT memory_version.vault_id, memory_version.memory_id,
        memory_version.version_number, memory_version.is_current,
        memory_version.content_hash, memory_version.source_id,
        memory_version.source_version
    INTO version_vault_id, version_memory_id, version_number_value,
        version_is_current, version_content_hash, version_source_id,
        version_source_version
    FROM owner_truth.memory_versions AS memory_version
    WHERE memory_version.vault_id = NEW.vault_id
      AND memory_version.id = NEW.memory_version_id;

    SELECT source.vault_id, source.owner_subject_id, source.source_version,
        source.state, source.authority_epoch
    INTO source_vault_id, source_owner_subject_id, source_version_value,
        source_state, source_authority_epoch
    FROM owner_truth.sources AS source
    WHERE source.vault_id = NEW.vault_id AND source.id = version_source_id;

    IF NOT FOUND
        OR answer_vault_id IS DISTINCT FROM NEW.vault_id
        OR memory_vault_id IS DISTINCT FROM NEW.vault_id
        OR version_vault_id IS DISTINCT FROM NEW.vault_id
        OR source_vault_id IS DISTINCT FROM NEW.vault_id
        OR memory_owner_subject_id IS DISTINCT FROM answer_owner_subject_id
        OR source_owner_subject_id IS DISTINCT FROM answer_owner_subject_id
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM answer_authority_epoch
        OR version_source_id IS DISTINCT FROM NEW.source_id
        OR version_source_version IS DISTINCT FROM NEW.source_version
        OR source_state IS DISTINCT FROM 'active'
        OR source_authority_epoch IS DISTINCT FROM answer_authority_epoch
        OR source_version_value IS DISTINCT FROM NEW.source_version
        OR version_memory_id IS DISTINCT FROM NEW.memory_id
        OR version_number_value IS DISTINCT FROM NEW.memory_version
        OR version_is_current IS DISTINCT FROM TRUE
        OR version_content_hash IS DISTINCT FROM NEW.content_hash
    THEN
        RAISE EXCEPTION 'owner truth Answer citation is not a current authorized MemoryVersion';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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
    memory_status TEXT;
    memory_authority_epoch BIGINT;
    version_memory_id UUID;
    version_number_value BIGINT;
    version_is_current BOOLEAN;
    version_content_hash TEXT;
    version_source_id UUID;
    version_source_version BIGINT;
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

    SELECT owner_subject_id, status, authority_epoch
    INTO memory_owner_subject_id, memory_status, memory_authority_epoch
    FROM owner_truth.memories
    WHERE vault_id = NEW.vault_id AND id = NEW.memory_id;

    SELECT memory_id, version_number, is_current, content_hash,
        source_id, source_version
    INTO version_memory_id, version_number_value, version_is_current,
        version_content_hash, version_source_id, version_source_version
    FROM owner_truth.memory_versions
    WHERE vault_id = NEW.vault_id AND id = NEW.expected_memory_version_id;

    SELECT owner_subject_id, source_version, state, authority_epoch
    INTO source_owner_subject_id, source_version_value, source_state, source_authority_epoch
    FROM owner_truth.sources
    WHERE vault_id = NEW.vault_id AND id = version_source_id;

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
        OR version_source_id IS DISTINCT FROM citation_source_id
        OR version_source_version IS DISTINCT FROM citation_source_version
        OR source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR source_state IS DISTINCT FROM 'active'
        OR source_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR source_version_value IS DISTINCT FROM version_source_version
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
