-- Add hash-only candidates for a future Publication canary, incident and exit
-- decision. This migration is additive and default-off: it does not enroll an
-- adult cohort, expose a public route, open a Visitor session, issue a grant,
-- perform an incident operation, remove data or contact an external service.

CREATE TABLE publication.canary_decision_candidates (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    build_hash TEXT NOT NULL CHECK (build_hash ~ '^[a-f0-9]{64}$'),
    schema_hash TEXT NOT NULL CHECK (schema_hash ~ '^[a-f0-9]{64}$'),
    evidence_set_hash TEXT NOT NULL CHECK (evidence_set_hash ~ '^[a-f0-9]{64}$'),
    stage TEXT NOT NULL CHECK (stage IN ('synthetic', 'internal', 'adultCohort')),
    decision TEXT NOT NULL CHECK (decision IN ('noGo', 'pause')),
    candidate_hash TEXT NOT NULL CHECK (candidate_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'blocked' CHECK (state IN ('shadow', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (publication_version_id, policy_hash, build_hash, candidate_hash),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE publication.incident_exit_candidates (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    canary_candidate_id UUID NOT NULL,
    exit_kind TEXT NOT NULL CHECK (exit_kind IN (
        'withdrawal', 'rights', 'incident', 'regulatory'
    )),
    evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'blocked' CHECK (state IN ('shadow', 'blocked', 'pause')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (canary_candidate_id, exit_kind),
    FOREIGN KEY (canary_candidate_id)
        REFERENCES publication.canary_decision_candidates(id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

REVOKE ALL ON TABLE publication.canary_decision_candidates FROM PUBLIC;
REVOKE ALL ON TABLE publication.incident_exit_candidates FROM PUBLIC;

CREATE INDEX publication_canary_decision_candidates_lookup
    ON publication.canary_decision_candidates(publication_id, stage, created_at DESC);
CREATE INDEX publication_incident_exit_candidates_lookup
    ON publication.incident_exit_candidates(publication_id, exit_kind, created_at DESC);
