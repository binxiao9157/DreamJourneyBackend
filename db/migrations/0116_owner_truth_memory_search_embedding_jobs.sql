-- migration:owner_truth_memory_search_embedding_jobs
--
-- Durable derived-vector jobs for current, private SearchDocuments.  Formal
-- MemoryVersion writes never depend on this table: the worker may retry or be
-- disabled without changing confirmed facts.  Every completion is bound to a
-- current search-document hash and checkpoint by the existing embedding
-- trigger plus the worker's compare-and-write query.

CREATE TABLE owner_truth.search_document_embedding_jobs (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    memory_version_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    source_projection_checkpoint TEXT NOT NULL CHECK (BTRIM(source_projection_checkpoint) <> ''),
    embedding_model_id TEXT NOT NULL CHECK (BTRIM(embedding_model_id) <> ''),
    embedding_model_version TEXT NOT NULL CHECK (BTRIM(embedding_model_version) <> ''),
    embedding_dimensions SMALLINT NOT NULL CHECK (embedding_dimensions BETWEEN 1 AND 4096),
    state TEXT NOT NULL CHECK (state IN ('queued', 'leased', 'retry', 'ready', 'failed', 'stale')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        vault_id, authority_epoch, memory_version_id,
        embedding_model_id, embedding_model_version
    ),
    FOREIGN KEY (vault_id, authority_epoch, memory_version_id)
        REFERENCES owner_truth.search_documents(vault_id, authority_epoch, memory_version_id)
        ON DELETE CASCADE,
    CHECK (
        (state = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR
        (state <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)
    )
);

CREATE INDEX owner_truth_search_document_embedding_jobs_claim
    ON owner_truth.search_document_embedding_jobs (
        embedding_model_id, embedding_model_version, embedding_dimensions,
        state, available_at, updated_at, memory_version_id
    );

CREATE OR REPLACE FUNCTION owner_truth.validate_search_document_embedding_job()
RETURNS TRIGGER AS $$
DECLARE
    checkpoint_hash TEXT;
    checkpoint_state TEXT;
    checkpoint_owner TEXT;
    document_hash TEXT;
BEGIN
    SELECT source_projection_checkpoint, state, owner_subject_id
      INTO checkpoint_hash, checkpoint_state, checkpoint_owner
      FROM owner_truth.search_document_checkpoints
     WHERE vault_id = NEW.vault_id
       AND authority_epoch = NEW.authority_epoch;

    SELECT content_hash
      INTO document_hash
      FROM owner_truth.search_documents
     WHERE vault_id = NEW.vault_id
       AND authority_epoch = NEW.authority_epoch
       AND memory_version_id = NEW.memory_version_id;

    IF NOT FOUND
       OR checkpoint_state IS DISTINCT FROM 'ready'
       OR checkpoint_hash IS DISTINCT FROM NEW.source_projection_checkpoint
       OR checkpoint_owner IS DISTINCT FROM NEW.owner_subject_id
       OR document_hash IS DISTINCT FROM NEW.content_hash
    THEN
        RAISE EXCEPTION 'owner truth search embedding job is stale or cross-scope';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_search_document_embedding_jobs_validate_source
BEFORE INSERT OR UPDATE OF content_hash, source_projection_checkpoint, owner_subject_id
ON owner_truth.search_document_embedding_jobs
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_search_document_embedding_job();
