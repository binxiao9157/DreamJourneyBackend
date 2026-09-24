-- migration:owner_truth_live_long_memory_pipeline
--
-- Private, resumable coordination for long Live transcript organization. These
-- tables never expose or activate formal memories; final Candidate publication
-- remains inside the existing Owner Truth extraction transaction.

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_runs (
    id UUID PRIMARY KEY,
    owner_subject_id TEXT NOT NULL,
    vault_id TEXT NOT NULL REFERENCES owner_truth.vaults(vault_id) ON DELETE CASCADE,
    product_session_id TEXT NOT NULL,
    capture_generation INTEGER NOT NULL CHECK (capture_generation > 0),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    pipeline_version TEXT NOT NULL,
    budget_policy_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('collecting','organizing','readyToPublish','published','failed')),
    source_id UUID,
    source_version INTEGER CHECK (source_version IS NULL OR source_version >= 0),
    source_content_hash CHAR(64),
    final_watermark BIGINT CHECK (final_watermark IS NULL OR final_watermark >= 0),
    planned_unit_count INTEGER NOT NULL DEFAULT 0 CHECK (planned_unit_count >= 0),
    provider_request_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_request_count >= 0),
    reserved_input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (reserved_input_tokens >= 0),
    reserved_output_tokens BIGINT NOT NULL DEFAULT 0 CHECK (reserved_output_tokens >= 0),
    recovery_request_count INTEGER NOT NULL DEFAULT 0 CHECK (recovery_request_count >= 0),
    manifest_hash CHAR(64),
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_subject_id, vault_id, product_session_id, capture_generation, pipeline_version),
    CHECK (
        (source_id IS NULL AND source_version IS NULL AND source_content_hash IS NULL AND final_watermark IS NULL)
        OR
        (source_id IS NOT NULL AND source_version IS NOT NULL AND source_content_hash IS NOT NULL AND final_watermark IS NOT NULL)
    )
);

ALTER TABLE owner_truth.live_memory_runs
    ADD COLUMN IF NOT EXISTS failure_code TEXT;

CREATE INDEX IF NOT EXISTS live_memory_runs_owner_state_idx
    ON owner_truth.live_memory_runs (owner_subject_id, vault_id, state, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS live_memory_runs_source_idx
    ON owner_truth.live_memory_runs (vault_id, source_id)
    WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_work_units (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES owner_truth.live_memory_runs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    kind TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    input_hash CHAR(64) NOT NULL,
    budget_lineage_key UUID NOT NULL,
    ownership JSONB NOT NULL,
    context JSONB NOT NULL,
    parent_unit_id UUID REFERENCES owner_truth.live_memory_work_units(id),
    state TEXT NOT NULL CHECK (state IN ('planned','running','completed','failed')),
    output_hash CHAR(64),
    output_coverage JSONB,
    failure_code TEXT,
    extra_request_count INTEGER NOT NULL DEFAULT 0 CHECK (extra_request_count >= 0),
    lease_owner TEXT,
    lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, kind, ordinal, input_hash)
);

ALTER TABLE owner_truth.live_memory_work_units
    ADD COLUMN IF NOT EXISTS failure_code TEXT;

CREATE INDEX IF NOT EXISTS live_memory_work_units_run_state_idx
    ON owner_truth.live_memory_work_units (run_id, state, ordinal);

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_segments (
    message_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES owner_truth.live_memory_runs(id) ON DELETE CASCADE,
    sequence_number BIGINT NOT NULL CHECK (sequence_number > 0),
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    text_hash CHAR(64) NOT NULL,
    unit_id UUID REFERENCES owner_truth.live_memory_work_units(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, sequence_number)
);

CREATE INDEX IF NOT EXISTS live_memory_segments_unassigned_idx
    ON owner_truth.live_memory_segments (run_id, sequence_number)
    WHERE unit_id IS NULL;

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_provider_attempts (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES owner_truth.live_memory_runs(id) ON DELETE CASCADE,
    unit_id UUID NOT NULL REFERENCES owner_truth.live_memory_work_units(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    request_hash CHAR(64) NOT NULL,
    exposure_state TEXT NOT NULL CHECK (exposure_state IN ('reserved','notSent','sent','outcomeUnknown','responseAccepted','rejected')),
    reserved_input_tokens BIGINT NOT NULL CHECK (reserved_input_tokens >= 0),
    reserved_output_tokens BIGINT NOT NULL CHECK (reserved_output_tokens >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    recovery BOOLEAN NOT NULL DEFAULT FALSE,
    model TEXT,
    finish_reason TEXT,
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_hash CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (run_id, ordinal)
);

CREATE INDEX IF NOT EXISTS live_memory_provider_attempts_unit_idx
    ON owner_truth.live_memory_provider_attempts (unit_id, ordinal);

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_atoms (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES owner_truth.live_memory_runs(id) ON DELETE CASCADE,
    unit_id UUID NOT NULL REFERENCES owner_truth.live_memory_work_units(id) ON DELETE CASCADE,
    memory JSONB NOT NULL,
    source_turn_indices JSONB NOT NULL,
    evidence_hash CHAR(64) NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active','merged','superseded','retracted','unresolved')),
    replacement_atom_id UUID REFERENCES owner_truth.live_memory_atoms(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, evidence_hash)
);

CREATE INDEX IF NOT EXISTS live_memory_atoms_run_state_idx
    ON owner_truth.live_memory_atoms (run_id, state, id);

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_relations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES owner_truth.live_memory_runs(id) ON DELETE CASCADE,
    left_atom_id UUID NOT NULL REFERENCES owner_truth.live_memory_atoms(id) ON DELETE CASCADE,
    right_atom_id UUID NOT NULL REFERENCES owner_truth.live_memory_atoms(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('duplicate','supplement','correction','retraction','distinct','unresolved')),
    proof JSONB NOT NULL,
    proof_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, left_atom_id, right_atom_id)
);

CREATE TABLE IF NOT EXISTS owner_truth.live_memory_publication_manifests (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE REFERENCES owner_truth.live_memory_runs(id) ON DELETE CASCADE,
    source_id UUID NOT NULL,
    source_version INTEGER NOT NULL CHECK (source_version >= 0),
    source_content_hash CHAR(64) NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    items JSONB NOT NULL,
    coverage JSONB NOT NULL,
    manifest_hash CHAR(64) NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('readyToPublish','published')),
    extraction_id UUID,
    candidate_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);
