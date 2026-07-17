CREATE TABLE token_families (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    current_session_version INTEGER NOT NULL CHECK (current_session_version >= 1),
    contract_version INTEGER NOT NULL DEFAULT 1 CHECK (contract_version = 1),
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_token_families_user_status
    ON token_families(user_id, status, updated_at DESC);

ALTER TABLE auth_sessions
    ADD COLUMN family_id TEXT,
    ADD COLUMN parent_session_id TEXT,
    ADD COLUMN successor_session_id TEXT,
    ADD COLUMN session_version INTEGER,
    ADD COLUMN rotated_at TIMESTAMPTZ,
    ADD COLUMN reuse_detected_at TIMESTAMPTZ,
    ADD COLUMN revoke_reason TEXT;

ALTER TABLE auth_sessions
    ADD CONSTRAINT auth_sessions_family_fk
        FOREIGN KEY (family_id) REFERENCES token_families(id)
        DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT auth_sessions_parent_fk
        FOREIGN KEY (parent_session_id) REFERENCES auth_sessions(id)
        DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT auth_sessions_successor_fk
        FOREIGN KEY (successor_session_id) REFERENCES auth_sessions(id)
        DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE auth_sessions
    ADD CONSTRAINT auth_sessions_family_version_check
    CHECK (
        (family_id IS NULL AND session_version IS NULL)
        OR (
            family_id IS NOT NULL
            AND session_version IS NOT NULL
            AND session_version >= 1
        )
    );

CREATE UNIQUE INDEX idx_auth_sessions_family_version
    ON auth_sessions(family_id, session_version)
    WHERE family_id IS NOT NULL;

CREATE INDEX idx_auth_sessions_family_status
    ON auth_sessions(family_id, status, session_version DESC)
    WHERE family_id IS NOT NULL;

CREATE UNIQUE INDEX idx_auth_sessions_one_active_family
    ON auth_sessions(family_id)
    WHERE family_id IS NOT NULL AND status = 'active';

UPDATE auth_sessions
SET payload = payload || '{"legacyFamily": true}'::jsonb
WHERE family_id IS NULL;

CREATE TABLE session_events (
    id TEXT PRIMARY KEY,
    family_id TEXT REFERENCES token_families(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES auth_sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    session_version INTEGER NOT NULL CHECK (session_version >= 0),
    contract_version INTEGER NOT NULL DEFAULT 1 CHECK (contract_version = 1),
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_session_events_family_time
    ON session_events(family_id, occurred_at, id);

CREATE INDEX idx_session_events_user_time
    ON session_events(user_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION reject_session_event_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'session_events are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER session_events_no_update
BEFORE UPDATE ON session_events
FOR EACH ROW EXECUTE FUNCTION reject_session_event_update();
