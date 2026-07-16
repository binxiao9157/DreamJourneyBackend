-- migration:existing_schema_baseline
-- Fresh databases execute this file. Existing deployments must use explicit,
-- verified baseline adoption and do not replay this DDL.

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    nickname TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE kb_snapshots (
    user_id TEXT PRIMARY KEY,
    graph JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE kb_changes (
    user_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    operation_id TEXT NOT NULL,
    graph JSONB NOT NULL,
    mutation JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, revision),
    UNIQUE (user_id, operation_id)
);

CREATE INDEX idx_kb_changes_user_revision
    ON kb_changes(user_id, revision ASC);

CREATE TABLE kb_change_feed_state (
    user_id TEXT PRIMARY KEY,
    minimum_since_revision BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (minimum_since_revision >= 0)
);

CREATE TABLE kb_operation_receipts (
    user_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload_hash TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, operation_id)
);

CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_memories_user_created
    ON memories(user_id, created_at DESC);

CREATE TABLE archive_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_archive_items_user_created
    ON archive_items(user_id, created_at DESC);

CREATE TABLE mailbox_letters (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mailbox_letters_user_created
    ON mailbox_letters(user_id, created_at DESC);

CREATE TABLE echo_delayed_replies (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_echo_delayed_replies_user_created
    ON echo_delayed_replies(user_id, created_at DESC);

CREATE TABLE push_device_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_push_device_tokens_user_updated
    ON push_device_tokens(user_id, updated_at DESC);

CREATE TABLE voice_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_voice_profiles_user_updated
    ON voice_profiles(user_id, updated_at DESC);

CREATE TABLE voice_clone_slots (
    provider_speaker_id TEXT PRIMARY KEY,
    voice_profile_id TEXT UNIQUE,
    user_id TEXT,
    persona_scope TEXT,
    digital_human_id TEXT,
    status TEXT NOT NULL DEFAULT 'available',
    training_attempts INTEGER NOT NULL DEFAULT 0,
    configured BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_voice_clone_slots_user_updated
    ON voice_clone_slots(user_id, updated_at DESC);

CREATE TABLE digital_human_sessions (
    id TEXT PRIMARY KEY,
    resource_key TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    scene TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_digital_human_sessions_resource_status
    ON digital_human_sessions(resource_key, status, expires_at);

CREATE INDEX idx_digital_human_sessions_user_device
    ON digital_human_sessions(user_id, device_id, status);

CREATE TABLE auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    access_token_hash TEXT UNIQUE NOT NULL,
    refresh_token_hash TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    access_expires_at TIMESTAMPTZ NOT NULL,
    refresh_expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_auth_sessions_user_status
    ON auth_sessions(user_id, status, updated_at DESC);

CREATE TABLE evidence_events (
    event_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    retention_class TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (event_type IN ('operation', 'rights', 'incident', 'providerCost')),
    CHECK (schema_version = 1),
    CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    CHECK (octet_length(payload::text) <= 16384)
);

CREATE INDEX idx_evidence_events_operation_time
    ON evidence_events(operation_id, occurred_at DESC);

CREATE INDEX idx_evidence_events_type_time
    ON evidence_events(event_type, occurred_at DESC);

CREATE INDEX idx_evidence_events_operation_kind_time
    ON evidence_events((payload->>'operation'), occurred_at DESC)
    WHERE event_type = 'operation';

CREATE INDEX idx_evidence_events_retention
    ON evidence_events(expires_at ASC)
    WHERE legal_hold = FALSE AND expires_at IS NOT NULL;

CREATE FUNCTION reject_evidence_event_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'evidence_events are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER evidence_events_no_update
BEFORE UPDATE ON evidence_events
FOR EACH ROW EXECUTE FUNCTION reject_evidence_event_update();

CREATE TABLE profiles (
    user_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE password_credentials (
    user_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE family_members (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_family_members_user_created
    ON family_members(user_id, created_at ASC);

CREATE INDEX idx_family_members_invitation_code
    ON family_members ((payload->>'invitationCode'));

CREATE TABLE care_snapshots (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    viewer_family_member_id TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_care_snapshots_user_viewer_created
    ON care_snapshots(user_id, viewer_family_member_id, created_at DESC);
