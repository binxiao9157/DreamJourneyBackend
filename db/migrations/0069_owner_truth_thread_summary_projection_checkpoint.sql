-- migration:owner_truth_thread_summary_projection_checkpoint
--
-- Add a default-off, rebuildable Phase 4A Thread summary checkpoint. It stores
-- only current Thread/session handles and confirmed-MemoryVersion anchors. It
-- never stores transcript text, Owner narrative, model labels, semantic topic
-- names, Source content, Candidate payloads, or provider output.

CREATE TABLE owner_truth.thread_summary_projection_checkpoints (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    state TEXT NOT NULL CHECK (state IN ('ready', 'rebuilding')),
    source_dimension_checkpoint TEXT NOT NULL
        CHECK (source_dimension_checkpoint ~ '^[a-f0-9]{64}$'),
    input_digest TEXT NOT NULL CHECK (input_digest ~ '^[a-f0-9]{64}$'),
    projection_hash TEXT NOT NULL CHECK (projection_hash ~ '^[a-f0-9]{64}$'),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    thread_count BIGINT NOT NULL DEFAULT 0 CHECK (thread_count >= 0),
    association_count BIGINT NOT NULL DEFAULT 0 CHECK (association_count >= 0),
    filtered_stale_cue_count BIGINT NOT NULL DEFAULT 0
        CHECK (filtered_stale_cue_count >= 0),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-thread-summary-checkpoint-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.thread_summary_projection_threads (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    thread_id UUID NOT NULL,
    session_id UUID NOT NULL,
    thread_state TEXT NOT NULL CHECK (BTRIM(thread_state) <> ''),
    session_state TEXT NOT NULL CHECK (BTRIM(session_state) <> ''),
    session_boundary TEXT NOT NULL CHECK (BTRIM(session_boundary) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, authority_epoch, thread_id),
    FOREIGN KEY (vault_id, authority_epoch)
        REFERENCES owner_truth.thread_summary_projection_checkpoints(vault_id, authority_epoch)
        ON DELETE CASCADE
);

CREATE TABLE owner_truth.thread_summary_projection_anchors (
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    thread_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    target_dimension TEXT NOT NULL CHECK (target_dimension IN (
        'lifeStage',
        'importantPeople',
        'keyDecisions',
        'professionalExperience',
        'values',
        'aspirationsAndBoundaries'
    )),
    missing_facet TEXT NOT NULL CHECK (BTRIM(missing_facet) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        vault_id,
        authority_epoch,
        thread_id,
        memory_version_id,
        target_dimension,
        missing_facet
    ),
    FOREIGN KEY (vault_id, authority_epoch, thread_id)
        REFERENCES owner_truth.thread_summary_projection_threads(
            vault_id,
            authority_epoch,
            thread_id
        )
        ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION owner_truth.validate_thread_summary_projection_checkpoint()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND
       OR vault_status IS DISTINCT FROM 'active'
       OR NEW.owner_subject_id IS DISTINCT FROM vault_owner_subject_id
       OR NEW.authority_epoch IS DISTINCT FROM vault_authority_epoch
    THEN
        RAISE EXCEPTION 'owner truth thread summary projection checkpoint is stale';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_thread_summary_projection_checkpoints_validate_vault
BEFORE INSERT OR UPDATE OF owner_subject_id, authority_epoch, state,
    source_dimension_checkpoint, input_digest, projection_hash, policy_version
ON owner_truth.thread_summary_projection_checkpoints
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_thread_summary_projection_checkpoint();

CREATE INDEX owner_truth_thread_summary_projection_checkpoints_ready
    ON owner_truth.thread_summary_projection_checkpoints(
        vault_id,
        authority_epoch,
        updated_at DESC
    )
    WHERE state = 'ready';

CREATE INDEX owner_truth_thread_summary_projection_anchors_memory
    ON owner_truth.thread_summary_projection_anchors(
        vault_id,
        authority_epoch,
        memory_version_id
    );
