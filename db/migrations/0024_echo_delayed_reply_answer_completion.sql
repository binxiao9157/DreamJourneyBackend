-- migration:echo_delayed_reply_answer_completion
--
-- Default-off V4 persistence for the delayed Echo reply completion path.  The
-- existing echo_delayed_replies table remains the scheduling aggregate.  This
-- table stores the private, immutable Answer/Message body separately so an
-- Inbox projection and the generic async-effect receipt can remain value-free.

CREATE TABLE echo_delayed_reply_answers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    delayed_reply_id TEXT NOT NULL CHECK (BTRIM(delayed_reply_id) <> ''),
    conversation_id TEXT NOT NULL CHECK (BTRIM(conversation_id) <> ''),
    request_id TEXT NOT NULL CHECK (BTRIM(request_id) <> ''),
    reply_generation BIGINT NOT NULL CHECK (reply_generation >= 1),
    context_hash TEXT NOT NULL CHECK (context_hash ~ '^[a-f0-9]{64}$'),
    context_version TEXT NOT NULL CHECK (BTRIM(context_version) <> ''),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    answer_hash TEXT NOT NULL CHECK (answer_hash ~ '^[a-f0-9]{64}$'),
    citation_receipt_hash TEXT NOT NULL CHECK (citation_receipt_hash ~ '^[a-f0-9]{64}$'),
    provider_result_hash TEXT NOT NULL CHECK (provider_result_hash ~ '^[a-f0-9]{64}$'),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, delayed_reply_id),
    UNIQUE (vault_id, conversation_id, request_id, context_hash)
);

CREATE INDEX idx_echo_delayed_reply_answers_owner_completed
    ON echo_delayed_reply_answers(owner_subject_id, completed_at DESC);

CREATE INDEX idx_echo_delayed_reply_answers_user_completed
    ON echo_delayed_reply_answers(user_id, completed_at DESC);
