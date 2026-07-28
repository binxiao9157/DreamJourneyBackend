-- Add future publication aggregate-metric and release-guard metadata. This
-- migration is additive and default-off: it creates no Owner/Visitor route,
-- public query, publication writer, grant/session issuer, metrics reader or
-- release UI. Every table remains private to the publication schema.

CREATE TABLE publication.aggregate_metric_snapshots (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN (
        'draft', 'published', 'suspended', 'withdrawn'
    )),
    grant_count BIGINT NOT NULL CHECK (grant_count >= 0),
    session_count BIGINT NOT NULL CHECK (session_count >= 0),
    feedback_count BIGINT NOT NULL CHECK (feedback_count >= 0),
    report_count BIGINT NOT NULL CHECK (report_count >= 0),
    receipt_count BIGINT NOT NULL CHECK (receipt_count >= 0),
    minimum_sample_size INTEGER NOT NULL CHECK (minimum_sample_size >= 1),
    privacy_threshold_met BOOLEAN NOT NULL DEFAULT FALSE,
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'blocked' CHECK (state IN ('shadow', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (publication_version_id, policy_hash, snapshot_hash),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE publication.release_guard_candidates (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    audience TEXT NOT NULL CHECK (audience IN ('owner', 'visitor')),
    candidate_hash TEXT NOT NULL CHECK (candidate_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'blocked' CHECK (state IN ('shadow', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (publication_version_id, audience, policy_hash, candidate_hash),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

REVOKE ALL ON TABLE publication.aggregate_metric_snapshots FROM PUBLIC;
REVOKE ALL ON TABLE publication.release_guard_candidates FROM PUBLIC;

CREATE INDEX publication_aggregate_metric_snapshots_lookup
    ON publication.aggregate_metric_snapshots(publication_id, created_at DESC);
CREATE INDEX publication_release_guard_candidates_lookup
    ON publication.release_guard_candidates(publication_id, audience, created_at DESC);
