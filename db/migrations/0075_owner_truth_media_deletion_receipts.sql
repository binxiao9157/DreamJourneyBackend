-- migration:owner_truth_media_deletion_receipts
--
-- P0-S1 adds a revocation-first lifecycle for private media SourceObjects.
-- These columns are deliberately metadata-only: the physical object-store
-- deletion is a later worker concern, and a tombstone never means bytes are
-- already gone.

ALTER TABLE owner_truth.media_source_objects
    ADD COLUMN access_state TEXT NOT NULL DEFAULT 'available'
        CHECK (access_state IN ('available', 'accessRevoked')),
    ADD COLUMN deletion_status TEXT NOT NULL DEFAULT 'notRequested'
        CHECK (deletion_status IN ('notRequested', 'pending', 'partial', 'unsupported', 'completed')),
    ADD COLUMN deletion_generation BIGINT NOT NULL DEFAULT 0
        CHECK (deletion_generation >= 0),
    ADD COLUMN deletion_retryable BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN deletion_failure_code TEXT,
    ADD COLUMN deletion_requested_at TIMESTAMPTZ,
    ADD COLUMN deletion_updated_at TIMESTAMPTZ;

CREATE TABLE owner_truth.media_source_object_deletion_commands (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    source_object_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    deletion_generation BIGINT NOT NULL CHECK (deletion_generation >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, source_object_id, command_id_hash),
    FOREIGN KEY (vault_id, source_object_id)
        REFERENCES owner_truth.media_source_objects(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE INDEX owner_truth_media_deletion_commands_owner_idx
    ON owner_truth.media_source_object_deletion_commands(vault_id, owner_subject_id, created_at DESC);
CREATE INDEX owner_truth_media_source_objects_deletion_idx
    ON owner_truth.media_source_objects(access_state, deletion_status, deletion_updated_at);
