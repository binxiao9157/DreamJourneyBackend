-- migration:owner_truth_biography_topic_jobs
-- Additive derived artifacts only. Existing durable formal revisions are the
-- recoverable discovery ledger. No trigger on fact writes, no historical sweep.
CREATE TABLE owner_truth.biography_topic_jobs (
    vault_id TEXT NOT NULL REFERENCES owner_truth.vaults(vault_id) ON DELETE RESTRICT,
    owner_subject_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    source_revision TEXT NOT NULL CHECK (source_revision ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','ready','fallback','obsolete')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 2),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
    result JSONB,
    request_metadata JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(request_metadata)='array'),
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    error_code TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, topic_id, source_revision)
);
CREATE INDEX biography_topic_jobs_pending ON owner_truth.biography_topic_jobs(vault_id,state,available_at);
REVOKE ALL ON owner_truth.biography_topic_jobs FROM PUBLIC;
