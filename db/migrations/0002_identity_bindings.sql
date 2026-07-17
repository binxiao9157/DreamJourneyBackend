-- migration:identity_bindings
-- Additive strong-identity records. Targets and verification codes are never
-- stored directly; only keyed SHA-256 digests are persisted.

CREATE TABLE identity_hash_key_versions (
    version TEXT PRIMARY KEY,
    key_fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$'),
    CHECK (key_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('active', 'retired'))
);

CREATE TABLE subjects (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'suspended', 'retired'))
);

CREATE TABLE identity_bindings (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subjects(id),
    identity_type TEXT NOT NULL,
    target_hash_key_version TEXT NOT NULL REFERENCES identity_hash_key_versions(version),
    target_hash TEXT NOT NULL,
    provider_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, subject_id),
    UNIQUE (identity_type, target_hash_key_version, target_hash),
    CHECK (identity_type IN ('phone')),
    CHECK (target_hash ~ '^[0-9a-f]{64}$'),
    CHECK (status IN ('active', 'suspended', 'revoked'))
);

CREATE INDEX idx_identity_bindings_subject_status
    ON identity_bindings(subject_id, status, updated_at DESC);

CREATE TABLE auth_challenges (
    id TEXT PRIMARY KEY,
    identity_type TEXT NOT NULL,
    target_hash_key_version TEXT NOT NULL REFERENCES identity_hash_key_versions(version),
    target_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    provider_mode TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    internal_verification_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (identity_type IN ('phone')),
    CHECK (target_hash ~ '^[0-9a-f]{64}$'),
    CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    CHECK (purpose IN ('login', 'register', 'restore', 'invitation')),
    CHECK (status IN ('active', 'consumed', 'expired', 'locked')),
    CHECK (attempts >= 0 AND attempts <= max_attempts),
    CHECK (max_attempts > 0)
);

CREATE INDEX idx_auth_challenges_status_expiry
    ON auth_challenges(status, expires_at ASC);

CREATE INDEX idx_auth_challenges_target_purpose_created
    ON auth_challenges(
        identity_type,
        target_hash_key_version,
        target_hash,
        purpose,
        created_at DESC
    );

CREATE TABLE identity_proofs (
    id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL UNIQUE REFERENCES auth_challenges(id),
    binding_id TEXT NOT NULL,
    subject_id TEXT NOT NULL REFERENCES subjects(id),
    provider_mode TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    contract_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (contract_version = 1),
    FOREIGN KEY (binding_id, subject_id) REFERENCES identity_bindings(id, subject_id)
);

CREATE INDEX idx_identity_proofs_binding_verified
    ON identity_proofs(binding_id, verified_at DESC);

CREATE FUNCTION reject_identity_proof_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'identity proof receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER identity_proofs_no_update_or_delete
BEFORE UPDATE OR DELETE ON identity_proofs
FOR EACH ROW EXECUTE FUNCTION reject_identity_proof_update();
