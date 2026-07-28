-- Add future Visitor answer-safety metadata without admitting a Visitor query,
-- provider call, feedback writer, public route, raw message, or answer body.

ALTER TABLE publication.visitor_sessions
    ADD COLUMN IF NOT EXISTS continuous_use_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_interaction_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS requested_exit_channel TEXT
        CHECK (requested_exit_channel IN ('ui', 'voice', 'keyword')),
    ADD COLUMN IF NOT EXISTS safety_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (safety_state IN ('none', 'crisis', 'unknown'));

CREATE TABLE publication.visitor_answer_safety_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    visitor_session_id UUID NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    public_context_source TEXT NOT NULL
        CHECK (public_context_source IN ('publicationVersion', 'unknown')),
    public_context_hash TEXT NOT NULL CHECK (public_context_hash ~ '^[a-f0-9]{64}$'),
    public_citation_set_hash TEXT NOT NULL CHECK (public_citation_set_hash ~ '^[a-f0-9]{64}$'),
    interaction_kind TEXT NOT NULL CHECK (interaction_kind IN ('answer', 'report', 'exit')),
    prompt_risk_state TEXT NOT NULL
        CHECK (prompt_risk_state IN ('clear', 'promptInjection', 'privateExtraction', 'unknown')),
    risk_state TEXT NOT NULL CHECK (risk_state IN ('none', 'crisis', 'unknown')),
    rate_limit_state TEXT NOT NULL CHECK (rate_limit_state IN ('allowed', 'limitReached', 'unknown')),
    exit_channel TEXT NOT NULL CHECK (exit_channel IN ('none', 'ui', 'voice', 'keyword')),
    report_kind TEXT NOT NULL CHECK (report_kind IN ('none', 'safetyReport', 'accessIssue', 'contentConcern')),
    outcome TEXT NOT NULL CHECK (outcome IN ('blocked', 'denied', 'accepted')),
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (visitor_session_id, request_hash),
    FOREIGN KEY (visitor_session_id, publication_id, vault_id)
        REFERENCES publication.visitor_sessions(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

REVOKE ALL ON TABLE publication.visitor_answer_safety_receipts FROM PUBLIC;

CREATE INDEX publication_visitor_answer_safety_receipts_session_created
    ON publication.visitor_answer_safety_receipts(visitor_session_id, created_at DESC);
