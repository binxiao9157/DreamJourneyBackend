-- migration:async_effect_dead_letter_replay_requests
--
-- Stores a single append-only, restore-fenced replay authorization record for
-- an existing open dead letter.  This is deliberately inert: no job is
-- re-enqueued, no worker is enabled, and no Provider is invoked.

CREATE TABLE async_effects.dead_letter_replay_requests (
    replay_id UUID PRIMARY KEY,
    dead_letter_id UUID NOT NULL REFERENCES async_effects.dead_letters(dead_letter_id) ON DELETE RESTRICT,
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
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    authorization_receipt_hash TEXT NOT NULL CHECK (authorization_receipt_hash ~ '^[0-9a-f]{64}$'),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    restore_id_hash TEXT NOT NULL CHECK (restore_id_hash ~ '^[0-9a-f]{64}$'),
    restore_checkpoint_hash TEXT NOT NULL CHECK (restore_checkpoint_hash ~ '^[0-9a-f]{64}$'),
    recovery_authorization_receipt_hash TEXT NOT NULL
        CHECK (recovery_authorization_receipt_hash ~ '^[0-9a-f]{64}$'),
    next_attempt INTEGER NOT NULL CHECK (next_attempt > 0),
    state TEXT NOT NULL CHECK (state = 'authorized'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dead_letter_id)
);

CREATE INDEX async_effects_dead_letter_replay_requests_job_idx
    ON async_effects.dead_letter_replay_requests(job_id, created_at);

CREATE TRIGGER async_effects_dead_letter_replay_requests_no_update
BEFORE UPDATE ON async_effects.dead_letter_replay_requests
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();

CREATE TRIGGER async_effects_dead_letter_replay_requests_no_delete
BEFORE DELETE ON async_effects.dead_letter_replay_requests
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
