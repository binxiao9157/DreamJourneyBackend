-- migration:owner_truth_echo_text_context
--
-- Text Echo reference resolution has a short-lived, private server-side
-- context.  It is not an Owner Truth Source/Candidate/MemoryVersion and is
-- never consumed by extraction or review.  Expiry and cascade removal keep it
-- from becoming another authority or a durable shadow memory store.

CREATE TABLE IF NOT EXISTS owner_truth.echo_conversation_contexts (
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    product_session_id TEXT NOT NULL
        CHECK (BTRIM(product_session_id) <> '' AND LENGTH(product_session_id) <= 128),
    turns JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(turns) = 'array'),
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, owner_subject_id, product_session_id),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS owner_truth_echo_conversation_context_expiry
    ON owner_truth.echo_conversation_contexts(expires_at);

CREATE INDEX IF NOT EXISTS owner_truth_echo_conversation_context_owner
    ON owner_truth.echo_conversation_contexts(vault_id, owner_subject_id, updated_at DESC);
