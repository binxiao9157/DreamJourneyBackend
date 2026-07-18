-- migration:owner_truth_memory_projection_trigger_fix
--
-- 0016 is already deployed as an additive shadow projection migration.  Keep
-- its ledger checksum intact and replace only the projection-entry trigger
-- function so the SELECT/INTO mapping preserves MemoryVersion schema_version.

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_projection_entry()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    memory_owner_subject_id TEXT;
    memory_source_id UUID;
    memory_source_version BIGINT;
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
        memory.source_id,
        memory.source_version,
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
        version.version_number,
        version.is_current,
        version.schema_version,
        version.content_hash,
        version.payload
    INTO
        memory_owner_subject_id,
        memory_source_id,
        memory_source_version,
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
      ON source.vault_id = memory.vault_id
     AND source.id = memory.source_id
    WHERE memory.vault_id = NEW.vault_id
      AND memory.id = NEW.memory_id
      AND version.id = NEW.memory_version_id;

    IF NOT FOUND
        OR vault_status IS DISTINCT FROM 'active'
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR memory_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR memory_status IS DISTINCT FROM 'active'
        OR memory_authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR memory_source_id IS DISTINCT FROM NEW.source_id
        OR memory_source_version IS DISTINCT FROM NEW.source_version
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
