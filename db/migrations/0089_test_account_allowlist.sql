-- migration:test_account_allowlist
--
-- Machine-managed synthetic login targets for controlled QA. Raw phone
-- targets and plaintext verification codes are never persisted.

CREATE TABLE test_account_allowlist (
    id TEXT PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),
    identity_type TEXT NOT NULL CHECK (identity_type = 'phone'),
    target_hash_key_version TEXT NOT NULL CHECK (BTRIM(target_hash_key_version) <> ''),
    target_hash TEXT NOT NULL CHECK (target_hash ~ '^[a-f0-9]{64}$'),
    target_hint TEXT NOT NULL CHECK (BTRIM(target_hint) <> ''),
    code_hash_key_version TEXT NOT NULL CHECK (BTRIM(code_hash_key_version) <> ''),
    code_hash TEXT NOT NULL CHECK (code_hash ~ '^[a-f0-9]{64}$'),
    code_version INTEGER NOT NULL DEFAULT 1 CHECK (code_version >= 1),
    label TEXT NOT NULL CHECK (CHAR_LENGTH(BTRIM(label)) BETWEEN 1 AND 80),
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    subject_id TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_by_hash TEXT NOT NULL CHECK (created_by_hash ~ '^[a-f0-9]{64}$'),
    use_count BIGINT NOT NULL DEFAULT 0 CHECK (use_count >= 0),
    last_used_at TIMESTAMPTZ,
    contract_version INTEGER NOT NULL DEFAULT 1 CHECK (contract_version = 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE RESTRICT,
    UNIQUE (identity_type, target_hash_key_version, target_hash)
);

CREATE INDEX test_account_allowlist_active_target
    ON test_account_allowlist (
        identity_type,
        target_hash_key_version,
        target_hash,
        expires_at
    )
    WHERE status = 'active';

CREATE INDEX test_account_allowlist_subject
    ON test_account_allowlist(subject_id)
    WHERE subject_id IS NOT NULL;

REVOKE ALL ON TABLE test_account_allowlist FROM PUBLIC;
