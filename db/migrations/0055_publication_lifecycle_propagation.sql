-- Add future publication lifecycle and propagation receipt metadata. This
-- migration is additive and default-off: it creates no writer, session revoke,
-- public query, index/cache mutation, provider call, or external cleanup.

ALTER TABLE publication.publications
    ADD COLUMN IF NOT EXISTS last_transition_sequence BIGINT NOT NULL DEFAULT 0
        CHECK (last_transition_sequence >= 0),
    ADD COLUMN IF NOT EXISTS last_transition_hash TEXT
        CHECK (last_transition_hash ~ '^[a-f0-9]{64}$'),
    ADD COLUMN IF NOT EXISTS access_deny_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lifecycle_state_reason TEXT
        CHECK (lifecycle_state_reason IN (
            'ownerAction', 'memoryCorrection', 'memoryDeleted', 'consentRevoked',
            'thirdPartyObjection', 'rightsRequest'
        ));

CREATE TABLE publication.lifecycle_transition_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    command_hash TEXT NOT NULL CHECK (command_hash ~ '^[a-f0-9]{64}$'),
    action TEXT NOT NULL CHECK (action IN ('update', 'suspend', 'withdraw')),
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN (
        'ownerAction', 'memoryCorrection', 'memoryDeleted', 'consentRevoked',
        'thirdPartyObjection', 'rightsRequest'
    )),
    transition_sequence BIGINT NOT NULL CHECK (transition_sequence >= 1),
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    propagation_plan_hash TEXT NOT NULL CHECK (propagation_plan_hash ~ '^[a-f0-9]{64}$'),
    outcome TEXT NOT NULL CHECK (outcome IN ('blocked', 'denied', 'accepted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (publication_id, transition_sequence),
    UNIQUE (publication_id, command_hash),
    FOREIGN KEY (publication_id, vault_id)
        REFERENCES publication.publications(id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE publication.propagation_cleanup_candidates (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    lifecycle_receipt_id UUID NOT NULL,
    layer TEXT NOT NULL CHECK (layer IN (
        'publicGateway', 'shareGrant', 'visitorSession', 'publicIndex', 'cache',
        'externalIndex', 'objectStore', 'cdn'
    )),
    candidate_hash TEXT NOT NULL CHECK (candidate_hash ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('denyRequired', 'receiptRequired', 'unknown')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (lifecycle_receipt_id, layer),
    FOREIGN KEY (lifecycle_receipt_id)
        REFERENCES publication.lifecycle_transition_receipts(id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

REVOKE ALL ON TABLE publication.lifecycle_transition_receipts FROM PUBLIC;
REVOKE ALL ON TABLE publication.propagation_cleanup_candidates FROM PUBLIC;

CREATE INDEX publication_lifecycle_transition_receipts_lookup
    ON publication.lifecycle_transition_receipts(publication_id, transition_sequence DESC);
CREATE INDEX publication_propagation_cleanup_candidates_lookup
    ON publication.propagation_cleanup_candidates(publication_id, layer, created_at DESC);
