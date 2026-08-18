-- migration:owner_truth_v2_facets_search
--
-- Preserve the content schema beside each private SearchDocument and add a
-- GIN index for derived structured facet terms. Existing rows are V1 and are
-- not rewritten with synthetic facets.

ALTER TABLE owner_truth.search_documents
    ADD COLUMN IF NOT EXISTS content_schema_version TEXT NOT NULL
    DEFAULT 'owner-truth-v1'
    CHECK (BTRIM(content_schema_version) <> '');

CREATE INDEX IF NOT EXISTS owner_truth_search_documents_structured_terms_gin
    ON owner_truth.search_documents USING GIN (structured_terms);

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
    entry_content_schema_version TEXT;
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
        content_schema_version,
        memory_kind,
        perspective_type,
        sensitivity
    INTO
        entry_memory_version_id,
        entry_content_hash,
        entry_content_schema_version,
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
       OR NEW.content_schema_version IS DISTINCT FROM entry_content_schema_version
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
