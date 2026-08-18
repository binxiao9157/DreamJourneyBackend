-- migration:password_authentication_v2
-- Additive password login throttling and one-time OTP action grants. Phone
-- targets and action tokens remain keyed hashes; raw values are never stored.

ALTER TABLE auth_challenges
    DROP CONSTRAINT IF EXISTS auth_challenges_purpose_check;

ALTER TABLE auth_challenges
    ADD CONSTRAINT auth_challenges_purpose_check
    CHECK (
        purpose IN (
            'login',
            'register',
            'restore',
            'invitation',
            'passwordreset',
            'sensitiveoperation'
        )
    );

CREATE TABLE password_login_states (
    target_hash_key_version TEXT NOT NULL REFERENCES identity_hash_key_versions(version),
    target_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    last_failed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (target_hash_key_version, target_hash),
    CHECK (target_hash ~ '^[0-9a-f]{64}$'),
    CHECK (failed_attempts >= 0)
);

CREATE INDEX idx_password_login_states_locked_until
    ON password_login_states(locked_until)
    WHERE locked_until IS NOT NULL;

CREATE TABLE password_action_grants (
    id TEXT PRIMARY KEY,
    subject_id TEXT REFERENCES subjects(id),
    purpose TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    proof_receipt_id TEXT REFERENCES identity_proofs(id),
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    contract_version INTEGER NOT NULL DEFAULT 1,
    CHECK (purpose IN ('passwordReset', 'sensitiveOperation')),
    CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('active', 'consumed', 'expired', 'revoked')),
    CHECK (contract_version = 1)
);

CREATE INDEX idx_password_action_grants_subject_status_expiry
    ON password_action_grants(subject_id, status, expires_at ASC);
