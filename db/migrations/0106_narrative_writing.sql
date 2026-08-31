-- migration:narrative_writing
--
-- Versioned private writing artifacts derived only from current Owner Truth
-- MemoryVersions. The schema is intentionally limited to the writing domain.

CREATE SCHEMA IF NOT EXISTS narrative;

CREATE TABLE narrative.book_projects (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    subject_persona_id TEXT NOT NULL CHECK (BTRIM(subject_persona_id) <> ''),
    project_type TEXT NOT NULL CHECK (project_type IN ('selfAutobiography', 'taStory')),
    narrator_type TEXT NOT NULL CHECK (narrator_type IN (
        'selfFirstPerson', 'thirdPersonBiography', 'controllerWitness'
    )),
    title TEXT NOT NULL CHECK (BTRIM(title) <> ''),
    state TEXT NOT NULL CHECK (state IN (
        'notStarted', 'checkingReadiness', 'needsMoreMemory',
        'readyForConfirmation', 'generatingAuditions', 'auditionsReady',
        'generatingGoldenSample', 'goldenSampleReview', 'toneConfirmed',
        'outlineReview', 'writing', 'updateAvailable', 'paused', 'disputed',
        'suspended', 'archived', 'deleted'
    )),
    privacy_state TEXT NOT NULL DEFAULT 'private' CHECK (privacy_state = 'private'),
    writing_context JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(writing_context) = 'object'),
    paused_from_state TEXT CHECK (paused_from_state IN (
        'notStarted', 'checkingReadiness', 'needsMoreMemory', 'readyForConfirmation',
        'generatingAuditions', 'auditionsReady', 'generatingGoldenSample',
        'goldenSampleReview', 'toneConfirmed', 'outlineReview', 'writing',
        'updateAvailable', 'disputed', 'suspended'
    )),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    optimistic_version BIGINT NOT NULL DEFAULT 0 CHECK (optimistic_version >= 0),
    current_memory_snapshot_id UUID,
    current_golden_sample_id UUID,
    current_constitution_id UUID,
    current_outline_id UUID,
    ignored_memory_fingerprint TEXT CHECK (
        ignored_memory_fingerprint IS NULL OR ignored_memory_fingerprint ~ '^[a-f0-9]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id) REFERENCES owner_truth.vaults(vault_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX narrative_one_active_project
    ON narrative.book_projects(vault_id, subject_persona_id, project_type)
    WHERE state NOT IN ('archived', 'deleted');

CREATE TABLE narrative.memory_snapshots (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    memory_version_refs JSONB NOT NULL CHECK (jsonb_typeof(memory_version_refs) = 'array'),
    writing_context JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(writing_context) = 'object'),
    source_fingerprint TEXT NOT NULL CHECK (source_fingerprint ~ '^[a-f0-9]{64}$'),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[a-f0-9]{64}$'),
    created_by TEXT NOT NULL CHECK (BTRIM(created_by) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (project_id, snapshot_hash),
    FOREIGN KEY (vault_id, project_id)
        REFERENCES narrative.book_projects(vault_id, id) ON DELETE RESTRICT
);

CREATE TABLE narrative.artifact_versions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN (
        'writingAudition', 'goldenSample', 'narrativeStyleProfile',
        'writingConstitution', 'outline', 'chapter'
    )),
    artifact_key TEXT NOT NULL CHECK (BTRIM(artifact_key) <> ''),
    version_number BIGINT NOT NULL CHECK (version_number >= 1),
    parent_version_id UUID,
    supersedes_artifact_version_id UUID,
    memory_snapshot_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'draft', 'readyForReview', 'confirmed', 'final', 'stale', 'superseded'
    )),
    content_text TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(payload) = 'object'),
    schema_version TEXT NOT NULL CHECK (BTRIM(schema_version) <> ''),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    model_id TEXT,
    prompt_version TEXT,
    pipeline_version TEXT,
    constitution_version_id UUID,
    origin TEXT NOT NULL CHECK (origin IN ('generated', 'userEdited')),
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (project_id, artifact_type, artifact_key, version_number),
    FOREIGN KEY (vault_id, project_id)
        REFERENCES narrative.book_projects(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_snapshot_id)
        REFERENCES narrative.memory_snapshots(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_version_id) REFERENCES narrative.artifact_versions(id) ON DELETE RESTRICT,
    FOREIGN KEY (supersedes_artifact_version_id)
        REFERENCES narrative.artifact_versions(id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX narrative_one_current_artifact
    ON narrative.artifact_versions(project_id, artifact_type, artifact_key)
    WHERE is_current;

CREATE TABLE narrative.artifact_memory_refs (
    artifact_version_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    memory_id UUID NOT NULL,
    memory_version_id UUID NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
    anchor_type TEXT NOT NULL CHECK (anchor_type IN ('paragraph', 'claim', 'outlineNode')),
    anchor_id TEXT NOT NULL CHECK (BTRIM(anchor_id) <> ''),
    claim_hash TEXT NOT NULL CHECK (claim_hash ~ '^[a-f0-9]{64}$'),
    PRIMARY KEY (artifact_version_id, anchor_type, anchor_id, memory_version_id),
    FOREIGN KEY (vault_id, artifact_version_id)
        REFERENCES narrative.artifact_versions(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_id)
        REFERENCES owner_truth.memories(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (memory_version_id)
        REFERENCES owner_truth.memory_versions(id) ON DELETE RESTRICT
);

CREATE TABLE narrative.project_decisions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    command_id UUID NOT NULL,
    expected_project_version BIGINT NOT NULL CHECK (expected_project_version >= 0),
    decision_type TEXT NOT NULL CHECK (BTRIM(decision_type) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    target_artifact_version_id UUID,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(payload) = 'object'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, command_id),
    FOREIGN KEY (vault_id, project_id)
        REFERENCES narrative.book_projects(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (target_artifact_version_id)
        REFERENCES narrative.artifact_versions(id) ON DELETE RESTRICT
);

CREATE TABLE narrative.generation_jobs (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    vault_id TEXT NOT NULL,
    job_type TEXT NOT NULL CHECK (job_type IN (
        'auditions', 'goldenSample', 'outline', 'chapter', 'reviseArtifact'
    )),
    state TEXT NOT NULL CHECK (state IN (
        'queued', 'snapshotting', 'retrieving', 'planning', 'drafting',
        'validatingFacts', 'editingStyle', 'finalValidation', 'readyForReview',
        'needsEcho', 'failed', 'cancelled', 'superseded'
    )),
    memory_snapshot_id UUID NOT NULL,
    input_artifact_ids JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(input_artifact_ids) = 'array'),
    input_payload JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (jsonb_typeof(input_payload) = 'object'),
    command_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (BTRIM(idempotency_key) <> ''),
    expected_project_version BIGINT NOT NULL CHECK (expected_project_version >= 0),
    progress_stage TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    error_code TEXT,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    model_id TEXT,
    prompt_version TEXT,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (vault_id, id),
    UNIQUE (project_id, command_id, job_type),
    UNIQUE (vault_id, idempotency_key),
    FOREIGN KEY (vault_id, project_id)
        REFERENCES narrative.book_projects(vault_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, memory_snapshot_id)
        REFERENCES narrative.memory_snapshots(vault_id, id) ON DELETE RESTRICT
);

CREATE INDEX narrative_jobs_claim
    ON narrative.generation_jobs(state, created_at)
    WHERE state IN ('queued', 'failed');

CREATE TABLE narrative.generation_outbox (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    job_id UUID NOT NULL,
    event_key TEXT NOT NULL CHECK (BTRIM(event_key) <> ''),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'delivered')),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    UNIQUE (vault_id, job_id),
    UNIQUE (vault_id, event_key),
    FOREIGN KEY (vault_id, job_id)
        REFERENCES narrative.generation_jobs(vault_id, id) ON DELETE RESTRICT
);

CREATE INDEX narrative_generation_outbox_pending
    ON narrative.generation_outbox(available_at, created_at)
    WHERE state = 'pending';

CREATE TABLE narrative.generation_dead_letters (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    job_id UUID NOT NULL,
    error_code TEXT NOT NULL CHECK (BTRIM(error_code) <> ''),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, job_id),
    FOREIGN KEY (vault_id, job_id)
        REFERENCES narrative.generation_jobs(vault_id, id) ON DELETE RESTRICT
);

ALTER TABLE narrative.book_projects
    ADD CONSTRAINT narrative_project_current_snapshot_fk
    FOREIGN KEY (current_memory_snapshot_id)
    REFERENCES narrative.memory_snapshots(id) ON DELETE RESTRICT;
ALTER TABLE narrative.book_projects
    ADD CONSTRAINT narrative_project_current_golden_sample_fk
    FOREIGN KEY (current_golden_sample_id)
    REFERENCES narrative.artifact_versions(id) ON DELETE RESTRICT;
ALTER TABLE narrative.book_projects
    ADD CONSTRAINT narrative_project_current_constitution_fk
    FOREIGN KEY (current_constitution_id)
    REFERENCES narrative.artifact_versions(id) ON DELETE RESTRICT;
ALTER TABLE narrative.book_projects
    ADD CONSTRAINT narrative_project_current_outline_fk
    FOREIGN KEY (current_outline_id)
    REFERENCES narrative.artifact_versions(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION narrative.reject_immutable_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'narrative immutable rows cannot be updated or deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER narrative_memory_snapshots_immutable
BEFORE UPDATE OR DELETE ON narrative.memory_snapshots
FOR EACH ROW EXECUTE FUNCTION narrative.reject_immutable_update();

CREATE OR REPLACE FUNCTION narrative.protect_artifact_body()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.content_text IS DISTINCT FROM OLD.content_text
        OR NEW.payload IS DISTINCT FROM OLD.payload
        OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
        OR NEW.memory_snapshot_id IS DISTINCT FROM OLD.memory_snapshot_id
        OR NEW.artifact_type IS DISTINCT FROM OLD.artifact_type
        OR NEW.artifact_key IS DISTINCT FROM OLD.artifact_key
    THEN
        RAISE EXCEPTION 'narrative artifact body is append-only';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER narrative_artifact_body_append_only
BEFORE UPDATE ON narrative.artifact_versions
FOR EACH ROW EXECUTE FUNCTION narrative.protect_artifact_body();

REVOKE ALL ON SCHEMA narrative FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA narrative FROM PUBLIC;
