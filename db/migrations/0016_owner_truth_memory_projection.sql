-- migration:owner_truth_memory_projection
--
-- Add a default-off, rebuildable compatibility projection.  The projection is
-- deliberately isolated from legacy kb_snapshots: it may cache confirmed
-- MemoryVersion content, but it never becomes an Owner Truth writer.

CREATE TABLE owner_truth.memory_projection_checkpoints (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    projection_source TEXT NOT NULL
        CHECK (projection_source IN ('v4', 'legacy_compat')),
    state TEXT NOT NULL DEFAULT 'rebuilding'
        CHECK (state IN ('ready', 'rebuilding')),
    entry_count BIGINT NOT NULL DEFAULT 0 CHECK (entry_count >= 0),
    source_hash TEXT NOT NULL CHECK (BTRIM(source_hash) <> ''),
    projection_hash TEXT NOT NULL CHECK (BTRIM(projection_hash) <> ''),
    schema_version TEXT NOT NULL CHECK (BTRIM(schema_version) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.memory_projection_entries (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    memory_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    version_number BIGINT NOT NULL CHECK (version_number >= 1),
    source_id UUID NOT NULL,
    source_version BIGINT NOT NULL CHECK (source_version >= 1),
    memory_kind TEXT NOT NULL
        CHECK (memory_kind IN ('experience', 'knowledge', 'emotion')),
    perspective_type TEXT NOT NULL
        CHECK (perspective_type IN ('firstPerson', 'reported', 'inferred')),
    epistemic_status TEXT NOT NULL
        CHECK (epistemic_status IN ('observed', 'recalled', 'reported', 'inferred', 'uncertain')),
    sensitivity TEXT NOT NULL
        CHECK (sensitivity IN ('standard', 'sensitive', 'restricted')),
    visibility TEXT NOT NULL CHECK (visibility = 'owner'),
    content_schema_version TEXT NOT NULL CHECK (BTRIM(content_schema_version) <> ''),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch, memory_id),
    UNIQUE (vault_id, authority_epoch, memory_version_id),
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_memory_projection_checkpoint()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    IF NEW.projection_source <> 'v4' THEN
        RETURN NEW;
    END IF;
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND
        OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
        OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
        OR vault_status IS DISTINCT FROM 'active'
    THEN
        RAISE EXCEPTION 'owner truth projection checkpoint authority is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_memory_projection_checkpoints_validate_vault
BEFORE INSERT OR UPDATE OF owner_subject_id, authority_epoch, projection_source
ON owner_truth.memory_projection_checkpoints
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_projection_checkpoint();

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

CREATE TRIGGER owner_truth_memory_projection_entries_validate_memory_version
BEFORE INSERT OR UPDATE ON owner_truth.memory_projection_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_memory_projection_entry();

CREATE INDEX owner_truth_memory_projection_checkpoints_ready
    ON owner_truth.memory_projection_checkpoints(vault_id, authority_epoch, updated_at DESC)
    WHERE state = 'ready';
CREATE INDEX owner_truth_memory_projection_entries_source
    ON owner_truth.memory_projection_entries(vault_id, authority_epoch, source_id);
