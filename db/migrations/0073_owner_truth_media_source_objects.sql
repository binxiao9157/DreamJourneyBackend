-- migration:owner_truth_media_source_objects
--
-- Stage 2 adds the first non-shadow private media boundary.  It is additive:
-- media bytes stay in a private object store and these relations retain only
-- ownership, integrity, safety and processing metadata.  Existing Archive
-- items remain compatibility data and are never promoted by this migration.

CREATE TABLE owner_truth.media_source_objects (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    media_kind TEXT NOT NULL
        CHECK (media_kind IN ('image', 'audio', 'video', 'document')),
    state TEXT NOT NULL
        CHECK (state IN (
            'uploadPending', 'verified', 'quarantined', 'processing',
            'processed', 'failed', 'deleted'
        )),
    content_type TEXT NOT NULL CHECK (BTRIM(content_type) <> ''),
    magic_mime TEXT,
    file_name TEXT NOT NULL CHECK (BTRIM(file_name) <> ''),
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes > 0),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    storage_provider TEXT,
    storage_key TEXT,
    storage_version BIGINT NOT NULL DEFAULT 0 CHECK (storage_version >= 0),
    safety_status TEXT NOT NULL
        CHECK (safety_status IN ('pending', 'clean', 'blocked', 'unavailable')),
    safety_provider TEXT,
    safety_reason_code TEXT,
    processing_status TEXT NOT NULL
        CHECK (processing_status IN (
            'notQueued', 'queued', 'processing', 'succeeded', 'retryableFailed',
            'failed', 'notApplicable', 'blocked'
        )),
    processing_attempt INTEGER NOT NULL DEFAULT 0 CHECK (processing_attempt >= 0),
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    failure_code TEXT,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    origin_command_id_hash TEXT NOT NULL
        CHECK (origin_command_id_hash ~ '^[0-9a-f]{64}$'),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    uploaded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, origin_command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    CHECK (
        (state = 'uploadPending' AND storage_provider IS NULL AND storage_key IS NULL)
        OR state <> 'uploadPending'
    ),
    CHECK (
        state <> 'verified'
        OR (
            storage_provider IS NOT NULL
            AND storage_key IS NOT NULL
            AND storage_version >= 1
            AND safety_status = 'clean'
        )
    )
);

CREATE TABLE owner_truth.media_source_object_upload_intents (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    source_object_id UUID NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    command_id_hash TEXT NOT NULL CHECK (command_id_hash ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    upload_token_hash TEXT NOT NULL CHECK (upload_token_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'uploaded', 'rejected', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    uploaded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_object_id)
        REFERENCES owner_truth.media_source_objects(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (state = 'uploaded' AND uploaded_at IS NOT NULL)
        OR (state <> 'uploaded')
    )
);

CREATE INDEX owner_truth_media_source_objects_owner_state_idx
    ON owner_truth.media_source_objects(vault_id, owner_subject_id, state, updated_at DESC);
CREATE INDEX owner_truth_media_source_objects_processing_idx
    ON owner_truth.media_source_objects(processing_status, updated_at);
CREATE INDEX owner_truth_media_upload_intents_pending_idx
    ON owner_truth.media_source_object_upload_intents(vault_id, state, expires_at);
