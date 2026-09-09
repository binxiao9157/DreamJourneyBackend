-- migration:owner_truth_projection_revision_fence
--
-- Bind each persisted projection to the authoritative formal-memory revision.
-- Formal-fact mutations invalidate derived read models in the same database
-- transaction, so normal reads can reuse persisted semantic consolidation
-- instead of rebuilding the complete person model for every query.

ALTER TABLE owner_truth.memory_projection_checkpoints
    ADD COLUMN IF NOT EXISTS memory_revision BIGINT NOT NULL DEFAULT 0
        CHECK (memory_revision >= 0);

INSERT INTO owner_truth.memory_revisions (vault_id, revision)
SELECT vault_id, 0
FROM owner_truth.vaults
ON CONFLICT (vault_id) DO NOTHING;

CREATE OR REPLACE FUNCTION owner_truth.invalidate_memory_derivatives()
RETURNS TRIGGER AS $$
DECLARE
    v_vault_id TEXT;
    v_previous_vault_id TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_vault_id := OLD.vault_id;
    ELSE
        v_vault_id := NEW.vault_id;
    END IF;

    UPDATE owner_truth.memory_projection_checkpoints
       SET state = 'rebuilding', updated_at = NOW()
     WHERE vault_id = v_vault_id
       AND state = 'ready';
    UPDATE owner_truth.search_document_checkpoints
       SET state = 'rebuilding', updated_at = NOW()
     WHERE vault_id = v_vault_id
       AND state = 'ready';

    IF TG_OP = 'UPDATE' AND OLD.vault_id IS DISTINCT FROM NEW.vault_id THEN
        v_previous_vault_id := OLD.vault_id;
        UPDATE owner_truth.memory_projection_checkpoints
           SET state = 'rebuilding', updated_at = NOW()
         WHERE vault_id = v_previous_vault_id
           AND state = 'ready';
        UPDATE owner_truth.search_document_checkpoints
           SET state = 'rebuilding', updated_at = NOW()
         WHERE vault_id = v_previous_vault_id
           AND state = 'ready';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_revisions_invalidate_derivatives
AFTER INSERT OR UPDATE OF revision ON owner_truth.memory_revisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.invalidate_memory_derivatives();

CREATE TRIGGER owner_truth_sources_invalidate_derivatives
AFTER INSERT OR DELETE OR UPDATE OF
    owner_subject_id, state, source_version, content_hash, authority_epoch
ON owner_truth.sources
FOR EACH ROW EXECUTE FUNCTION owner_truth.invalidate_memory_derivatives();

CREATE TRIGGER owner_truth_memories_invalidate_derivatives
AFTER INSERT OR DELETE OR UPDATE OF
    owner_subject_id, source_id, source_version, memory_kind, perspective_type,
    epistemic_status, sensitivity, status, content_hash, authority_epoch
ON owner_truth.memories
FOR EACH ROW EXECUTE FUNCTION owner_truth.invalidate_memory_derivatives();

CREATE TRIGGER owner_truth_memory_versions_invalidate_derivatives
AFTER INSERT OR UPDATE OR DELETE ON owner_truth.memory_versions
FOR EACH ROW EXECUTE FUNCTION owner_truth.invalidate_memory_derivatives();

CREATE TRIGGER owner_truth_projection_rights_invalidate_derivatives
AFTER INSERT ON owner_truth.projection_rights_events
FOR EACH ROW EXECUTE FUNCTION owner_truth.invalidate_memory_derivatives();

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_projection_checkpoint()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    current_rights_revision BIGINT;
    current_rights_state TEXT;
    current_rights_event_hash TEXT;
    current_memory_revision BIGINT;
    vault_found BOOLEAN;
    rights_event_found BOOLEAN;
BEGIN
    IF NEW.projection_source <> 'v4' THEN
        RETURN NEW;
    END IF;
    SELECT owner_subject_id, authority_epoch, status
      INTO vault_owner_subject_id, vault_authority_epoch, vault_status
      FROM owner_truth.vaults
     WHERE vault_id = NEW.vault_id;
    vault_found := FOUND;

    SELECT revision, rights_state, event_hash
      INTO current_rights_revision, current_rights_state, current_rights_event_hash
      FROM owner_truth.projection_rights_events
     WHERE vault_id = NEW.vault_id
       AND authority_epoch = NEW.authority_epoch
     ORDER BY revision DESC
     LIMIT 1;
    rights_event_found := FOUND;

    IF NOT rights_event_found THEN
        current_rights_revision := 0;
        current_rights_state := 'active';
        current_rights_event_hash := 'none';
    END IF;

    SELECT revision
      INTO current_memory_revision
      FROM owner_truth.memory_revisions
     WHERE vault_id = NEW.vault_id;
    current_memory_revision := COALESCE(current_memory_revision, 0);

    IF NOT vault_found
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR vault_status IS DISTINCT FROM 'active'
        OR (
            NEW.state = 'ready'
            AND (
                current_rights_state IS DISTINCT FROM 'active'
                OR NEW.rights_revision IS DISTINCT FROM current_rights_revision
                OR NEW.rights_event_hash IS DISTINCT FROM current_rights_event_hash
                OR NEW.memory_revision IS DISTINCT FROM current_memory_revision
            )
        )
    THEN
        RAISE EXCEPTION 'owner truth projection checkpoint authority fence is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS owner_truth_memory_projection_checkpoints_validate_vault
    ON owner_truth.memory_projection_checkpoints;
CREATE TRIGGER owner_truth_memory_projection_checkpoints_validate_vault
BEFORE INSERT OR UPDATE OF owner_subject_id, authority_epoch, projection_source,
    rights_revision, rights_event_hash, memory_revision, state
ON owner_truth.memory_projection_checkpoints
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_projection_checkpoint();

-- Existing projections were generated without the explicit revision column.
-- Fail closed and let the normal projection workers rebuild them under 0119.
UPDATE owner_truth.memory_projection_checkpoints AS checkpoint
   SET memory_revision = revision.revision,
       state = 'rebuilding',
       updated_at = NOW()
  FROM owner_truth.memory_revisions AS revision
 WHERE checkpoint.vault_id = revision.vault_id;

UPDATE owner_truth.search_document_checkpoints
   SET state = 'rebuilding', updated_at = NOW()
 WHERE state = 'ready';
