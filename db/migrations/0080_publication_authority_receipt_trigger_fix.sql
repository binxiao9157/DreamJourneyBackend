-- migration:publication_authority_receipt_trigger_fix
--
-- 0079 introduced the append-only publication authority receipt trigger. On
-- PostgreSQL, its local variable `pinned_memory_version_id` conflicted with
-- the identically named publication_versions column during confirmation. Keep
-- the applied 0079 checksum immutable and replace only the function body.
-- The publication lane remains internal QA-only and default-off.

CREATE OR REPLACE FUNCTION publication.validate_publication_authority_receipt()
RETURNS TRIGGER AS $$
DECLARE
    memory_owner_subject_id TEXT;
    memory_authority_epoch BIGINT;
    memory_state TEXT;
    memory_is_current BOOLEAN;
    source_owner_subject_id TEXT;
    source_authority_epoch BIGINT;
    source_state TEXT;
    receipt_decision TEXT;
    receipt_authority_epoch BIGINT;
    candidate_owner_subject_id TEXT;
    candidate_authority_epoch BIGINT;
    candidate_decision_status TEXT;
    publication_owner_subject_id TEXT;
    publication_authority_epoch BIGINT;
    draft_owner_subject_id TEXT;
    draft_authority_epoch BIGINT;
    stored_pinned_memory_version_id UUID;
BEGIN
    SELECT
        memory.owner_subject_id,
        memory.authority_epoch,
        memory.status,
        version.is_current,
        source.owner_subject_id,
        source.authority_epoch,
        source.state,
        decision_receipt.decision,
        decision_receipt.authority_epoch,
        candidate.owner_subject_id,
        candidate.authority_epoch,
        candidate.decision_status
    INTO
        memory_owner_subject_id,
        memory_authority_epoch,
        memory_state,
        memory_is_current,
        source_owner_subject_id,
        source_authority_epoch,
        source_state,
        receipt_decision,
        receipt_authority_epoch,
        candidate_owner_subject_id,
        candidate_authority_epoch,
        candidate_decision_status
    FROM owner_truth.memory_versions AS version
    JOIN owner_truth.memories AS memory
      ON memory.vault_id = version.vault_id
     AND memory.id = version.memory_id
    JOIN owner_truth.sources AS source
      ON source.vault_id = version.vault_id
     AND source.id = version.source_id
    JOIN owner_truth.decision_receipts AS decision_receipt
      ON decision_receipt.vault_id = version.vault_id
     AND decision_receipt.id = version.decision_receipt_id
    JOIN owner_truth.memory_candidates AS candidate
      ON candidate.vault_id = decision_receipt.vault_id
     AND candidate.id = decision_receipt.candidate_id
    WHERE version.vault_id = NEW.vault_id
      AND version.id = NEW.memory_version_id;

    IF NOT FOUND
        OR memory_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR memory_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR memory_state IS DISTINCT FROM 'active'
        OR memory_is_current IS DISTINCT FROM TRUE
        OR source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR source_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR source_state IS DISTINCT FROM 'active'
        OR receipt_decision NOT IN ('accepted', 'corrected')
        OR receipt_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR candidate_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR candidate_authority_epoch IS DISTINCT FROM NEW.authority_epoch
        OR candidate_decision_status IS DISTINCT FROM receipt_decision
    THEN
        RAISE EXCEPTION 'publication authority receipt must bind one current Owner-confirmed MemoryVersion';
    END IF;

    SELECT owner_subject_id, authority_epoch
    INTO publication_owner_subject_id, publication_authority_epoch
    FROM publication.publications
    WHERE id = NEW.publication_id
      AND vault_id = NEW.vault_id;

    IF NOT FOUND
        OR publication_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR publication_authority_epoch IS DISTINCT FROM NEW.authority_epoch
    THEN
        RAISE EXCEPTION 'publication authority receipt must match its publication owner and epoch';
    END IF;

    SELECT owner_subject_id, authority_epoch
    INTO draft_owner_subject_id, draft_authority_epoch
    FROM publication.publication_drafts
    WHERE id = NEW.draft_id
      AND vault_id = NEW.vault_id
      AND publication_id = NEW.publication_id;

    IF NOT FOUND
        OR draft_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
        OR draft_authority_epoch IS DISTINCT FROM NEW.authority_epoch
    THEN
        RAISE EXCEPTION 'publication authority receipt must match its draft owner and epoch';
    END IF;

    IF NEW.publication_version_id IS NOT NULL THEN
        SELECT version.pinned_memory_version_id
        INTO stored_pinned_memory_version_id
        FROM publication.publication_versions AS version
        WHERE version.id = NEW.publication_version_id
          AND version.publication_id = NEW.publication_id
          AND version.vault_id = NEW.vault_id;

        IF NOT FOUND
            OR stored_pinned_memory_version_id IS DISTINCT FROM NEW.memory_version_id
        THEN
            RAISE EXCEPTION 'publication authority receipt must bind the publication version authority anchor';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
