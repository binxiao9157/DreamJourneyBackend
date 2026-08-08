-- migration:data_export_jobs
--
-- Owner-scoped transient export jobs. Raw request keys, credentials, object
-- locators and provider responses are never stored. The JSON payload is the
-- same bounded, redacted data copy produced by the existing export authority.

CREATE TABLE data_export_jobs (
    id TEXT PRIMARY KEY CHECK (id ~ '^dej_[a-f0-9]{32}$'),
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_key_hash TEXT NOT NULL CHECK (request_key_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'ready', 'partial', 'failed', 'expired'
    )),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    artifact_hash TEXT CHECK (artifact_hash ~ '^[a-f0-9]{64}$'),
    artifact_payload JSONB,
    manifest_payload JSONB,
    failure_code TEXT CHECK (
        failure_code IS NULL OR failure_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
    ),
    contract_version INTEGER NOT NULL DEFAULT 1 CHECK (contract_version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    ready_at TIMESTAMPTZ,
    UNIQUE (owner_user_id, request_key_hash),
    CHECK (expires_at > created_at),
    CHECK (jsonb_typeof(artifact_payload) = 'object' OR artifact_payload IS NULL),
    CHECK (jsonb_typeof(manifest_payload) = 'object' OR manifest_payload IS NULL),
    CHECK (
        status NOT IN ('ready', 'partial') OR (
            artifact_hash IS NOT NULL
            AND artifact_payload IS NOT NULL
            AND manifest_payload IS NOT NULL
            AND ready_at IS NOT NULL
            AND failure_code IS NULL
        )
    ),
    CHECK (status <> 'failed' OR failure_code IS NOT NULL),
    CHECK (status <> 'expired' OR artifact_payload IS NULL)
);

CREATE INDEX data_export_jobs_owner_created_idx
    ON data_export_jobs(owner_user_id, created_at DESC);
CREATE INDEX data_export_jobs_status_expiry_idx
    ON data_export_jobs(status, expires_at ASC);

REVOKE ALL ON TABLE data_export_jobs FROM PUBLIC;
