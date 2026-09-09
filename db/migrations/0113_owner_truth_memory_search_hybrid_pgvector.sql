-- migration:owner_truth_memory_search_hybrid_pgvector
--
-- Same-PostgreSQL optional semantic index for current, private Owner Truth
-- SearchDocuments.  This migration intentionally fails when pgvector cannot
-- be installed: treating a missing extension as a usable semantic index would
-- silently degrade a production promise.  Runtime remains opt-in because an
-- embedding provider and private-data egress approval are separate decisions.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE owner_truth.search_document_embeddings (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    memory_version_id UUID NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    source_projection_checkpoint TEXT NOT NULL CHECK (BTRIM(source_projection_checkpoint) <> ''),
    embedding_model_id TEXT NOT NULL CHECK (BTRIM(embedding_model_id) <> ''),
    embedding_model_version TEXT NOT NULL CHECK (BTRIM(embedding_model_version) <> ''),
    embedding_dimensions SMALLINT NOT NULL CHECK (embedding_dimensions BETWEEN 1 AND 4096),
    embedding vector NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        vault_id, authority_epoch, memory_version_id,
        embedding_model_id, embedding_model_version
    ),
    FOREIGN KEY (vault_id, authority_epoch, memory_version_id)
        REFERENCES owner_truth.search_documents(vault_id, authority_epoch, memory_version_id)
        ON DELETE CASCADE,
    CHECK (vector_dims(embedding) = embedding_dimensions)
);

CREATE INDEX owner_truth_search_document_embeddings_scope_model
    ON owner_truth.search_document_embeddings (
        vault_id, authority_epoch, embedding_model_id, embedding_model_version,
        source_projection_checkpoint, memory_version_id
    );

-- An HNSW index is deliberately scoped to the locked 1024-dimensional BGE-M3
-- evaluation contract.  A different model/dimension requires its own explicit
-- migration and benchmark rather than silently sharing an incompatible index.
CREATE INDEX owner_truth_search_document_embeddings_bge_m3_hnsw
    ON owner_truth.search_document_embeddings
    USING hnsw ((embedding::vector(1024)) vector_cosine_ops)
    WHERE embedding_model_id = 'bge-m3'
      AND embedding_model_version = 'v1'
      AND embedding_dimensions = 1024;

CREATE OR REPLACE FUNCTION owner_truth.validate_search_document_embedding()
RETURNS TRIGGER AS $$
DECLARE
    checkpoint_hash TEXT;
    checkpoint_state TEXT;
    document_hash TEXT;
BEGIN
    SELECT source_projection_checkpoint, state
      INTO checkpoint_hash, checkpoint_state
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
       OR document_hash IS DISTINCT FROM NEW.content_hash
    THEN
        RAISE EXCEPTION 'owner truth search embedding is stale or cross-scope';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_search_document_embeddings_validate_source
BEFORE INSERT OR UPDATE ON owner_truth.search_document_embeddings
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_search_document_embedding();
