-- migration:realtime_voice_session_tickets
--
-- Short-lived, user/session-bound admission leases for the backend realtime
-- voice WebSocket proxy. Only a SHA-256 digest of the bearer ticket is stored.
-- Audio, transcripts and provider credentials never enter this table.

CREATE TABLE realtime_voice_session_tickets (
    id TEXT PRIMARY KEY CHECK (id ~ '^rvs_[a-f0-9]{32}$'),
    ticket_hash TEXT NOT NULL UNIQUE CHECK (ticket_hash ~ '^[a-f0-9]{64}$'),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    auth_session_id TEXT NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN (
        'issued', 'active', 'released', 'expired', 'revoked'
    )),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    contract_version INTEGER NOT NULL DEFAULT 1 CHECK (contract_version = 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (expires_at > created_at),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX realtime_voice_ticket_user_status_idx
    ON realtime_voice_session_tickets(user_id, status, expires_at DESC);

CREATE INDEX realtime_voice_ticket_auth_session_idx
    ON realtime_voice_session_tickets(auth_session_id, status, expires_at DESC);

CREATE INDEX realtime_voice_ticket_expiry_idx
    ON realtime_voice_session_tickets(status, expires_at ASC)
    WHERE status IN ('issued', 'active');

REVOKE ALL ON TABLE realtime_voice_session_tickets FROM PUBLIC;
