-- migration:publication_share_grant_session_metadata
--
-- Add private, hash-only authorization metadata for future ShareGrant and
-- adult Visitor sessions. No raw bearer credential, public route, gateway,
-- session writer, query adapter, or Family-derived authorization is introduced.

ALTER TABLE publication.share_grants
    ADD COLUMN IF NOT EXISTS issuance_command_hash TEXT
        CHECK (issuance_command_hash ~ '^[a-f0-9]{64}$'),
    ADD COLUMN IF NOT EXISTS revocation_command_hash TEXT
        CHECK (revocation_command_hash ~ '^[a-f0-9]{64}$'),
    ADD COLUMN IF NOT EXISTS grant_policy_hash TEXT
        CHECK (grant_policy_hash ~ '^[a-f0-9]{64}$');

ALTER TABLE publication.visitor_sessions
    ADD COLUMN IF NOT EXISTS adult_verification_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (adult_verification_state IN ('verified', 'unknown', 'minor', 'failed')),
    ADD COLUMN IF NOT EXISTS relationship_origin TEXT NOT NULL DEFAULT 'direct'
        CHECK (relationship_origin IN ('direct', 'familyDerived')),
    ADD COLUMN IF NOT EXISTS emergency_contact_ref_hash TEXT
        CHECK (emergency_contact_ref_hash ~ '^[a-f0-9]{64}$'),
    ADD COLUMN IF NOT EXISTS expected_grant_use_count INTEGER
        CHECK (expected_grant_use_count >= 0),
    ADD COLUMN IF NOT EXISTS session_policy_hash TEXT
        CHECK (session_policy_hash ~ '^[a-f0-9]{64}$');

CREATE TABLE publication.share_grant_authorization_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    publication_id UUID NOT NULL,
    publication_version_id UUID NOT NULL,
    share_grant_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('issue', 'revoke', 'access')),
    command_hash TEXT NOT NULL CHECK (command_hash ~ '^[a-f0-9]{64}$'),
    actor_subject_hash TEXT NOT NULL CHECK (actor_subject_hash ~ '^[a-f0-9]{64}$'),
    visitor_subject_hash TEXT CHECK (visitor_subject_hash ~ '^[a-f0-9]{64}$'),
    outcome TEXT NOT NULL CHECK (outcome IN ('blocked', 'denied', 'accepted')),
    policy_hash TEXT NOT NULL CHECK (policy_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (share_grant_id, command_hash),
    FOREIGN KEY (share_grant_id, publication_id, vault_id)
        REFERENCES publication.share_grants(id, publication_id, vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (publication_version_id, publication_id, vault_id)
        REFERENCES publication.publication_versions(id, publication_id, vault_id)
        ON DELETE RESTRICT
);

REVOKE ALL ON TABLE publication.share_grant_authorization_receipts FROM PUBLIC;

CREATE INDEX publication_share_grant_authorization_receipts_lookup
    ON publication.share_grant_authorization_receipts(share_grant_id, created_at DESC);
