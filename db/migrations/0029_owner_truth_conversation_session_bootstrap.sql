-- migration:owner_truth_conversation_session_bootstrap
--
-- M0-A private conversation bootstrap. Conversations, interview-session
-- pacing state, and raw message records stay in the Owner Truth private lane.
-- This additive migration intentionally does not create Sources, Candidates,
-- DecisionReceipts, MemoryVersions, public routes, or Provider effects.

CREATE TABLE owner_truth.conversation_threads (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'paused', 'ended')),
    entry_mode TEXT NOT NULL
        CHECK (entry_mode IN ('naturalInput', 'recommendation', 'resume')),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.interview_sessions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    current_thread_id UUID NOT NULL,
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'paused', 'ended')),
    boundary TEXT NOT NULL DEFAULT 'open'
        CHECK (boundary IN ('open', 'skipOnce', 'cooldown', 'doNotAsk')),
    turn_count INTEGER NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, current_thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT
);

-- The product has one natural input session at a time per Owner Vault. Paused
-- sessions can remain as private history, but cannot compete as "current".
CREATE UNIQUE INDEX owner_truth_interview_sessions_one_active_per_vault
    ON owner_truth.interview_sessions(vault_id)
    WHERE state = 'active';

CREATE TABLE owner_truth.conversation_messages (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    thread_id UUID NOT NULL,
    session_id UUID NOT NULL,
    sequence_number BIGINT NOT NULL CHECK (sequence_number >= 1),
    author TEXT NOT NULL CHECK (author IN ('owner', 'assistant', 'system')),
    kind TEXT NOT NULL CHECK (kind IN ('narrative', 'question', 'summary')),
    content_schema_version TEXT NOT NULL CHECK (BTRIM(content_schema_version) <> ''),
    content_hash TEXT NOT NULL CHECK (BTRIM(content_hash) <> ''),
    content_payload JSONB NOT NULL CHECK (jsonb_typeof(content_payload) = 'object'),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, thread_id, sequence_number),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, session_id)
        REFERENCES owner_truth.interview_sessions(vault_id, id)
        ON DELETE RESTRICT
);

CREATE TABLE owner_truth.conversation_command_receipts (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    command_id_hash TEXT NOT NULL CHECK (BTRIM(command_id_hash) <> ''),
    payload_hash TEXT NOT NULL CHECK (BTRIM(payload_hash) <> ''),
    command_type TEXT NOT NULL
        CHECK (command_type IN ('startInterviewSession', 'appendInterviewMessage', 'setInterviewBoundary')),
    target_thread_id UUID NOT NULL,
    target_session_id UUID NOT NULL,
    result_message_id UUID,
    expected_thread_version BIGINT
        CHECK (expected_thread_version IS NULL OR expected_thread_version >= 0),
    expected_session_version BIGINT
        CHECK (expected_session_version IS NULL OR expected_session_version >= 1),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    CONSTRAINT owner_truth_conversation_command_receipts_vault_command_unique
        UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, target_thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, target_session_id)
        REFERENCES owner_truth.interview_sessions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, result_message_id)
        REFERENCES owner_truth.conversation_messages(vault_id, id)
        ON DELETE RESTRICT,
    CHECK (
        (command_type = 'startInterviewSession'
            AND result_message_id IS NULL
            AND expected_thread_version = 0
            AND expected_session_version IS NULL)
        OR
        (command_type = 'appendInterviewMessage'
            AND result_message_id IS NOT NULL
            AND expected_thread_version IS NOT NULL
            AND expected_session_version IS NOT NULL)
        OR
        (command_type = 'setInterviewBoundary'
            AND result_message_id IS NULL
            AND expected_thread_version IS NULL
            AND expected_session_version IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION owner_truth.conversation_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth conversation records are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_conversation_threads_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.conversation_threads
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_interview_sessions_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.interview_sessions
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_conversation_messages_bind_vault_authority
BEFORE INSERT ON owner_truth.conversation_messages
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_conversation_messages_no_update
BEFORE UPDATE ON owner_truth.conversation_messages
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_conversation_messages_no_delete
BEFORE DELETE ON owner_truth.conversation_messages
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_conversation_command_receipts_no_update
BEFORE UPDATE ON owner_truth.conversation_command_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_conversation_command_receipts_no_delete
BEFORE DELETE ON owner_truth.conversation_command_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE INDEX owner_truth_conversation_messages_thread_sequence
    ON owner_truth.conversation_messages(vault_id, thread_id, sequence_number);

CREATE INDEX owner_truth_conversation_command_receipts_vault_created
    ON owner_truth.conversation_command_receipts(vault_id, created_at DESC);
