-- migration:owner_truth_person_memory_projection_versions
--
-- Preserve each changed, rebuildable person-memory projection as a versioned
-- snapshot. Source, Candidate and MemoryVersion remain the only authority;
-- cognitive, relationship and biography payloads can always be regenerated.

CREATE TABLE owner_truth.person_memory_projection_versions (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    projection_hash TEXT NOT NULL CHECK (projection_hash ~ '^[a-f0-9]{64}$'),
    source_hash TEXT NOT NULL CHECK (source_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL CHECK (BTRIM(schema_version) <> ''),
    model_version TEXT NOT NULL CHECK (model_version ~ '^[a-f0-9]{64}$'),
    memory_count BIGINT NOT NULL CHECK (memory_count >= 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch, projection_hash),
    FOREIGN KEY (vault_id, authority_epoch)
        REFERENCES owner_truth.memory_projection_checkpoints(vault_id, authority_epoch)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX owner_truth_person_memory_projection_one_current
    ON owner_truth.person_memory_projection_versions(vault_id, authority_epoch)
    WHERE is_current;

CREATE INDEX owner_truth_person_memory_projection_history
    ON owner_truth.person_memory_projection_versions(
        vault_id,
        authority_epoch,
        created_at DESC
    );

CREATE OR REPLACE FUNCTION owner_truth.validate_person_memory_projection_version()
RETURNS TRIGGER AS $$
DECLARE
    checkpoint_state TEXT;
    checkpoint_source_hash TEXT;
    checkpoint_projection_hash TEXT;
BEGIN
    SELECT state, source_hash, projection_hash
    INTO checkpoint_state, checkpoint_source_hash, checkpoint_projection_hash
    FROM owner_truth.memory_projection_checkpoints
    WHERE vault_id = NEW.vault_id
      AND authority_epoch = NEW.authority_epoch;

    IF NOT FOUND
        OR checkpoint_state IS DISTINCT FROM 'ready'
        OR NEW.source_hash IS DISTINCT FROM checkpoint_source_hash
        OR NEW.projection_hash IS DISTINCT FROM checkpoint_projection_hash
        OR NEW.payload ->> 'modelVersion' IS DISTINCT FROM NEW.model_version
        OR COALESCE((NEW.payload ->> 'memoryCount')::BIGINT, -1) IS DISTINCT FROM NEW.memory_count
    THEN
        RAISE EXCEPTION 'person memory projection does not match the current checkpoint';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_person_memory_projection_versions_validate
BEFORE INSERT OR UPDATE OF projection_hash, source_hash, schema_version,
    model_version, memory_count, payload
ON owner_truth.person_memory_projection_versions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_person_memory_projection_version();

REVOKE ALL ON TABLE owner_truth.person_memory_projection_versions FROM PUBLIC;
