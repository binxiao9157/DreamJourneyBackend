-- migration:legacy_identity_inbox_bridge
--
-- V4's legacy bridge is additive and default-off. It does not backfill a row,
-- claim a legacy account, issue a session, authorize a grant, or expose an
-- inbox. A later controlled migration must create claim_pending rows and only
-- promote them after an independently verified identity proof.

CREATE TABLE legacy_identity_aliases (
    legacy_account_user_id TEXT PRIMARY KEY
        REFERENCES users(id) ON DELETE RESTRICT,
    legacy_alias_hash TEXT NOT NULL UNIQUE
        CHECK (legacy_alias_hash ~ '^[0-9a-f]{64}$'),
    subject_id TEXT NOT NULL UNIQUE
        REFERENCES subjects(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL UNIQUE
        REFERENCES owner_truth.vaults(vault_id) ON DELETE RESTRICT,
    claim_state TEXT NOT NULL
        CHECK (claim_state IN ('claim_pending', 'verified', 'quarantined', 'retired')),
    identity_proof_id TEXT UNIQUE
        REFERENCES identity_proofs(id) ON DELETE RESTRICT,
    reason_code TEXT NOT NULL
        CHECK (reason_code ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$'),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (claim_state = 'verified' AND identity_proof_id IS NOT NULL AND claimed_at IS NOT NULL)
        OR (claim_state <> 'verified' AND identity_proof_id IS NULL AND claimed_at IS NULL)
    )
);

CREATE INDEX idx_legacy_identity_aliases_claim_state
    ON legacy_identity_aliases(claim_state, updated_at DESC);

CREATE OR REPLACE FUNCTION validate_legacy_identity_alias()
RETURNS trigger AS $$
DECLARE
    subject_status TEXT;
    vault_owner_subject_id TEXT;
    vault_status TEXT;
    proof_subject_id TEXT;
BEGIN
    SELECT status INTO subject_status
    FROM subjects
    WHERE id = NEW.subject_id;

    SELECT owner_subject_id, status
    INTO vault_owner_subject_id, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND OR vault_owner_subject_id IS DISTINCT FROM NEW.subject_id THEN
        RAISE EXCEPTION 'legacy identity alias vault owner must match subject';
    END IF;

    IF NEW.claim_state = 'verified' THEN
        SELECT subject_id INTO proof_subject_id
        FROM identity_proofs
        WHERE id = NEW.identity_proof_id;

        IF NOT FOUND
           OR proof_subject_id IS DISTINCT FROM NEW.subject_id
           OR subject_status IS DISTINCT FROM 'active'
           OR vault_status IS DISTINCT FROM 'active' THEN
            RAISE EXCEPTION 'verified legacy identity alias requires active subject, vault and matching proof';
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.legacy_account_user_id IS DISTINCT FROM OLD.legacy_account_user_id
           OR NEW.legacy_alias_hash IS DISTINCT FROM OLD.legacy_alias_hash
           OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
           OR NEW.vault_id IS DISTINCT FROM OLD.vault_id THEN
            RAISE EXCEPTION 'legacy identity alias coordinates are immutable';
        END IF;
        IF NEW.row_version IS DISTINCT FROM OLD.row_version + 1 THEN
            RAISE EXCEPTION 'legacy identity alias row version must advance by one';
        END IF;
        IF (OLD.claim_state = 'claim_pending' AND NEW.claim_state NOT IN ('claim_pending', 'verified', 'quarantined', 'retired'))
           OR (OLD.claim_state = 'verified' AND NEW.claim_state NOT IN ('verified', 'quarantined', 'retired'))
           OR (OLD.claim_state = 'quarantined' AND NEW.claim_state NOT IN ('quarantined', 'retired'))
           OR (OLD.claim_state = 'retired' AND NEW.claim_state <> 'retired') THEN
            RAISE EXCEPTION 'legacy identity alias state transition is invalid';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER legacy_identity_aliases_validate_insert
BEFORE INSERT ON legacy_identity_aliases
FOR EACH ROW EXECUTE FUNCTION validate_legacy_identity_alias();

CREATE TRIGGER legacy_identity_aliases_validate_update
BEFORE UPDATE ON legacy_identity_aliases
FOR EACH ROW EXECUTE FUNCTION validate_legacy_identity_alias();
