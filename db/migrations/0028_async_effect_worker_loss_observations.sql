-- migration:async_effect_worker_loss_observations
--
-- Append-only, value-free evidence that a worker lease expired. This is an
-- observation ledger only: it has no foreign key to a job, contains no owner
-- or resource identifier, and cannot reclaim, retry, or execute work.

CREATE TABLE async_effects.worker_loss_observations (
    observation_id TEXT PRIMARY KEY
        CHECK (observation_id ~ '^aew-[0-9a-f]{32}$'),
    observation_state TEXT NOT NULL
        CHECK (observation_state IN ('observed', 'clear', 'skipped', 'unknown')),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    observed_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > observed_at),
    runtime_enabled BOOLEAN NOT NULL,
    worker_enabled BOOLEAN NOT NULL,
    expired_lease_count INTEGER NOT NULL CHECK (expired_lease_count >= 0),
    expired_job_type_counts JSONB NOT NULL
        CHECK (jsonb_typeof(expired_job_type_counts) = 'object'),
    oldest_expired_lease_age_seconds INTEGER
        CHECK (oldest_expired_lease_age_seconds IS NULL OR oldest_expired_lease_age_seconds >= 0),
    lease_owner_hash_count INTEGER NOT NULL
        CHECK (lease_owner_hash_count >= 0 AND lease_owner_hash_count <= expired_lease_count),
    observer_worker_id_hash TEXT NOT NULL
        CHECK (observer_worker_id_hash ~ '^[0-9a-f]{64}$'),
    artifact_hash TEXT NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (expired_lease_count = 0
            AND oldest_expired_lease_age_seconds IS NULL
            AND lease_owner_hash_count = 0)
        OR
        (expired_lease_count > 0
            AND oldest_expired_lease_age_seconds IS NOT NULL
            AND lease_owner_hash_count > 0)
    )
);

CREATE INDEX async_effects_worker_loss_observations_observed_idx
    ON async_effects.worker_loss_observations(observed_at DESC, observation_state);

CREATE TRIGGER async_effects_worker_loss_observations_no_update
BEFORE UPDATE ON async_effects.worker_loss_observations
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();

CREATE TRIGGER async_effects_worker_loss_observations_no_delete
BEFORE DELETE ON async_effects.worker_loss_observations
FOR EACH ROW EXECUTE FUNCTION async_effects.append_only_receipt();
