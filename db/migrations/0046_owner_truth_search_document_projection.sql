-- migration:owner_truth_search_document_projection
--
-- Add a default-off, rebuildable private SearchDocument index.  It is derived
-- only from the current Owner Truth MemoryVersion projection and never becomes
-- a Source/Candidate/MemoryVersion writer, KBLite authority, or public search
-- surface.  A stale source checkpoint causes application reads to fail closed.

CREATE TABLE owner_truth.search_document_checkpoints (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    state TEXT NOT NULL CHECK (state IN ('ready', 'rebuilding')),
    source_projection_checkpoint TEXT NOT NULL CHECK (BTRIM(source_projection_checkpoint) <> ''),
    document_count BIGINT NOT NULL DEFAULT 0 CHECK (document_count >= 0),
    document_hash TEXT NOT NULL CHECK (document_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-search-document-projection-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.search_documents (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    memory_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    memory_kind TEXT NOT NULL
        CHECK (memory_kind IN ('experience', 'knowledge', 'emotion')),
    perspective_type TEXT NOT NULL
        CHECK (perspective_type IN ('firstPerson', 'reported', 'inferred')),
    sensitivity TEXT NOT NULL
        CHECK (sensitivity IN ('standard', 'sensitive', 'restricted')),
    -- Private derived material. It is never present in a public or QA result.
    search_text TEXT NOT NULL CHECK (char_length(search_text) <= 16384),
    structured_terms JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(structured_terms) = 'array'),
    text_was_truncated BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch, memory_id),
    UNIQUE (vault_id, authority_epoch, memory_version_id),
    FOREIGN KEY (vault_id, authority_epoch)
        REFERENCES owner_truth.search_document_checkpoints(vault_id, authority_epoch)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_search_document_checkpoint()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
    projection_owner_subject_id TEXT;
    projection_source TEXT;
    projection_state TEXT;
    projection_hash TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT owner_subject_id, projection_source, state, projection_hash
    INTO projection_owner_subject_id, projection_source, projection_state, projection_hash
    FROM owner_truth.memory_projection_checkpoints
    WHERE vault_id = NEW.vault_id
      AND authority_epoch = NEW.authority_epoch;

    IF NOT FOUND
       OR vault_status IS DISTINCT FROM 'active'
       OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
       OR projection_owner_subject_id IS DISTINCT FROM vault_owner_subject_id
       OR projection_source IS DISTINCT FROM 'v4'
       OR projection_state IS DISTINCT FROM 'ready'
       OR NEW.source_projection_checkpoint IS DISTINCT FROM projection_hash
    THEN
        RAISE EXCEPTION 'owner truth search document checkpoint is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_search_document_checkpoints_validate_source
BEFORE INSERT OR UPDATE OF
    owner_subject_id,
    authority_epoch,
    source_projection_checkpoint,
    schema_version
ON owner_truth.search_document_checkpoints
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_search_document_checkpoint();

CREATE OR REPLACE FUNCTION owner_truth.validate_search_document()
RETURNS TRIGGER AS $$
DECLARE
    checkpoint_owner_subject_id TEXT;
    checkpoint_state TEXT;
    checkpoint_source_projection_checkpoint TEXT;
    projection_hash TEXT;
    projection_state TEXT;
    projection_source TEXT;
    entry_memory_version_id UUID;
    entry_content_hash TEXT;
    entry_memory_kind TEXT;
    entry_perspective_type TEXT;
    entry_sensitivity TEXT;
BEGIN
    SELECT
        checkpoint.owner_subject_id,
        checkpoint.state,
        checkpoint.source_projection_checkpoint,
        projection.projection_hash,
        projection.state,
        projection.projection_source
    INTO
        checkpoint_owner_subject_id,
        checkpoint_state,
        checkpoint_source_projection_checkpoint,
        projection_hash,
        projection_state,
        projection_source
    FROM owner_truth.search_document_checkpoints AS checkpoint
    JOIN owner_truth.memory_projection_checkpoints AS projection
      ON projection.vault_id = checkpoint.vault_id
     AND projection.authority_epoch = checkpoint.authority_epoch
    WHERE checkpoint.vault_id = NEW.vault_id
      AND checkpoint.authority_epoch = NEW.authority_epoch;

    SELECT
        memory_version_id,
        content_hash,
        memory_kind,
        perspective_type,
        sensitivity
    INTO
        entry_memory_version_id,
        entry_content_hash,
        entry_memory_kind,
        entry_perspective_type,
        entry_sensitivity
    FROM owner_truth.memory_projection_entries
    WHERE vault_id = NEW.vault_id
      AND authority_epoch = NEW.authority_epoch
      AND memory_id = NEW.memory_id;

    IF NOT FOUND
       OR checkpoint_owner_subject_id IS NULL
       OR checkpoint_state NOT IN ('ready', 'rebuilding')
       OR projection_source IS DISTINCT FROM 'v4'
       OR projection_state IS DISTINCT FROM 'ready'
       OR checkpoint_source_projection_checkpoint IS DISTINCT FROM projection_hash
       OR NEW.memory_version_id IS DISTINCT FROM entry_memory_version_id
       OR NEW.content_hash IS DISTINCT FROM entry_content_hash
       OR NEW.memory_kind IS DISTINCT FROM entry_memory_kind
       OR NEW.perspective_type IS DISTINCT FROM entry_perspective_type
       OR NEW.sensitivity IS DISTINCT FROM entry_sensitivity
       OR EXISTS (
           SELECT 1
           FROM jsonb_array_elements(NEW.structured_terms) AS term(value)
           WHERE jsonb_typeof(term.value) <> 'string'
       )
    THEN
        RAISE EXCEPTION 'owner truth search document is not derived from a current projection entry';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_search_documents_validate_projection_entry
BEFORE INSERT OR UPDATE ON owner_truth.search_documents
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_search_document();

CREATE INDEX owner_truth_search_document_checkpoints_ready
    ON owner_truth.search_document_checkpoints(vault_id, authority_epoch, updated_at DESC)
    WHERE state = 'ready';

CREATE INDEX owner_truth_search_documents_scope
    ON owner_truth.search_documents(vault_id, authority_epoch, memory_version_id);
