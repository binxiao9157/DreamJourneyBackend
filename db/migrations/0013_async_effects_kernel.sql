-- migration:async_effects_kernel
--
-- V1 adds a durable coordination kernel only.  The tables contain opaque IDs,
-- hashes, and status evidence; user content and provider credentials remain in
-- their owning aggregate/provider boundary.  No worker or scheduler is enabled
-- by this migration.

CREATE SCHEMA IF NOT EXISTS async_effects;

CREATE TABLE async_effects.operations (
    operation_id UUID PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK (BTRIM(operation_type) <> ''),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN (
        'accepted', 'cancelRequested', 'cancelled', 'completed', 'failed', 'unknown', 'blocked'
    )),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    terminal_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, stable_key)
);

CREATE TABLE async_effects.outbox_events (
    event_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    event_type TEXT NOT NULL CHECK (BTRIM(event_type) <> ''),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'claimed', 'dispatched', 'cancelled', 'deadLettered')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (operation_id, event_type),
    UNIQUE (vault_id, stable_key, event_type)
);

CREATE TABLE async_effects.jobs (
    job_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    job_type TEXT NOT NULL CHECK (BTRIM(job_type) <> ''),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'leased', 'retryWait', 'succeeded', 'failed', 'unknown', 'cancelled', 'blocked'
    )),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    terminal_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (operation_id, job_type),
    UNIQUE (vault_id, stable_key, job_type)
);

CREATE TABLE async_effects.job_attempts (
    attempt_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES async_effects.jobs(job_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN (
        'started', 'succeeded', 'retryableFailed', 'terminalFailed', 'unknown', 'cancelled'
    )),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, attempt)
);

CREATE TABLE async_effects.consumer_inbox (
    inbox_id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES async_effects.outbox_events(event_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    consumer_name TEXT NOT NULL CHECK (BTRIM(consumer_name) <> ''),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('received', 'processing', 'completed', 'failed', 'unknown', 'skipped')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (consumer_name, event_id)
);

CREATE TABLE async_effects.business_receipts (
    receipt_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    receipt_type TEXT NOT NULL CHECK (BTRIM(receipt_type) <> ''),
    business_target_key TEXT NOT NULL CHECK (business_target_key ~ '^[0-9a-f]{64}$'),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('accepted', 'completed', 'skipped', 'blocked', 'failed', 'unknown')),
    outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'completed', 'skipped', 'blocked', 'failed', 'unknown')),
    reason_code TEXT,
    result_ref_hash TEXT CHECK (result_ref_hash IS NULL OR result_ref_hash ~ '^[0-9a-f]{64}$'),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (operation_id, receipt_type, business_target_key)
);

CREATE TABLE async_effects.provider_effects (
    effect_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    provider_name TEXT NOT NULL CHECK (BTRIM(provider_name) <> ''),
    provider_effect_key_hash TEXT NOT NULL CHECK (provider_effect_key_hash ~ '^[0-9a-f]{64}$'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'completed', 'failed', 'unknown', 'cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    accepted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_name, provider_effect_key_hash)
);

CREATE TABLE async_effects.provider_receipts (
    provider_receipt_id UUID PRIMARY KEY,
    effect_id UUID NOT NULL REFERENCES async_effects.provider_effects(effect_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    provider_name TEXT NOT NULL CHECK (BTRIM(provider_name) <> ''),
    provider_receipt_hash TEXT NOT NULL CHECK (provider_receipt_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('accepted', 'completed', 'failed', 'unknown')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_name, provider_receipt_hash)
);

CREATE TABLE async_effects.dead_letters (
    dead_letter_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES async_effects.jobs(job_id) ON DELETE RESTRICT,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    failure_hash TEXT NOT NULL CHECK (failure_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('open', 'reconciled', 'discarded')),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, attempt)
);

CREATE TABLE async_effects.scheduler_leases (
    lease_id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    resource_type TEXT NOT NULL CHECK (BTRIM(resource_type) <> ''),
    resource_id TEXT NOT NULL CHECK (BTRIM(resource_id) <> ''),
    resource_version BIGINT NOT NULL CHECK (resource_version >= 0),
    purpose TEXT NOT NULL CHECK (BTRIM(purpose) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    stable_key TEXT NOT NULL CHECK (stable_key ~ '^[0-9a-f]{64}$'),
    scheduler_key TEXT NOT NULL CHECK (BTRIM(scheduler_key) <> ''),
    state TEXT NOT NULL CHECK (state IN ('available', 'leased', 'expired', 'released')),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scheduler_key, operation_id)
);

CREATE INDEX async_effects_outbox_pending_idx
    ON async_effects.outbox_events(state, available_at, created_at);
CREATE INDEX async_effects_jobs_claim_idx
    ON async_effects.jobs(state, available_at, created_at);
CREATE INDEX async_effects_jobs_operation_idx
    ON async_effects.jobs(operation_id, created_at);
CREATE INDEX async_effects_inbox_event_idx
    ON async_effects.consumer_inbox(operation_id, event_id);
CREATE INDEX async_effects_provider_effect_operation_idx
    ON async_effects.provider_effects(operation_id, state);
CREATE INDEX async_effects_dead_letters_open_idx
    ON async_effects.dead_letters(state, created_at);

CREATE OR REPLACE FUNCTION async_effects.guard_terminal_state()
RETURNS TRIGGER AS $$
DECLARE
    terminal_states TEXT[] := string_to_array(TG_ARGV[0], ',');
BEGIN
    IF OLD.state = ANY(terminal_states) AND NEW.state IS DISTINCT FROM OLD.state THEN
        RAISE EXCEPTION 'async effect terminal state cannot be reverted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION async_effects.append_only_receipt()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'async effect receipt is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER async_effects_operations_terminal_guard
BEFORE UPDATE ON async_effects.operations
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('cancelled,completed,failed,unknown,blocked');
CREATE TRIGGER async_effects_outbox_terminal_guard
BEFORE UPDATE ON async_effects.outbox_events
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('dispatched,cancelled,deadLettered');
CREATE TRIGGER async_effects_jobs_terminal_guard
BEFORE UPDATE ON async_effects.jobs
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('succeeded,failed,unknown,cancelled,blocked');
CREATE TRIGGER async_effects_attempts_terminal_guard
BEFORE UPDATE ON async_effects.job_attempts
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('succeeded,retryableFailed,terminalFailed,unknown,cancelled');
CREATE TRIGGER async_effects_inbox_terminal_guard
BEFORE UPDATE ON async_effects.consumer_inbox
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('completed,failed,unknown,skipped');
CREATE TRIGGER async_effects_provider_effects_terminal_guard
BEFORE UPDATE ON async_effects.provider_effects
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('completed,failed,unknown,cancelled');
CREATE TRIGGER async_effects_dead_letters_terminal_guard
BEFORE UPDATE ON async_effects.dead_letters
FOR EACH ROW EXECUTE FUNCTION async_effects.guard_terminal_state('reconciled,discarded');

CREATE TRIGGER async_effects_business_receipts_no_update
BEFORE UPDATE ON async_effects.business_receipts
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
CREATE TRIGGER async_effects_business_receipts_no_delete
BEFORE DELETE ON async_effects.business_receipts
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
CREATE TRIGGER async_effects_provider_receipts_no_update
BEFORE UPDATE ON async_effects.provider_receipts
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
CREATE TRIGGER async_effects_provider_receipts_no_delete
BEFORE DELETE ON async_effects.provider_receipts
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
