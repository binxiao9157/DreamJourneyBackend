-- migration:access_first_suspend
--
-- Account deletion is access-first: an accepted request suspends the account,
-- revokes active credentials, and writes one durable, idempotent revocation
-- event for downstream capability brokers. The account payload continues to
-- carry mutable lifecycle fields for backward compatibility.

CREATE TABLE rights_access_revocation_outbox (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES rights_requests(id),
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type = 'RightsAccessRevoked'),
    auth_epoch INTEGER NOT NULL CHECK (auth_epoch >= 0),
    provider_capability_state TEXT NOT NULL CHECK (
        provider_capability_state IN ('revoked')
    ),
    status TEXT NOT NULL CHECK (status IN ('pending', 'dispatched', 'failed')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    dispatched_at TIMESTAMPTZ,
    UNIQUE (request_id, event_type)
);

CREATE INDEX idx_rights_access_revocation_outbox_pending
    ON rights_access_revocation_outbox(status, created_at ASC);
