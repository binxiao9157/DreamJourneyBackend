-- migration:owner_truth_family_contribution_grants
--
-- Add a default-off, Owner-controlled static family contribution lane.  This
-- is not Memorial authority: it cannot grant Vault reads, Candidate decisions,
-- publication, Voice, or Digital Human control.

CREATE TABLE owner_truth.family_contribution_grants (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    contributor_subject_id TEXT NOT NULL CHECK (BTRIM(contributor_subject_id) <> ''),
    relationship_id TEXT NOT NULL,
    relationship_epoch BIGINT NOT NULL CHECK (relationship_epoch >= 1),
    scope TEXT NOT NULL CHECK (scope = 'submitTextSource'),
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    create_command_id_hash TEXT NOT NULL CHECK (create_command_id_hash ~ '^[a-f0-9]{64}$'),
    create_payload_hash TEXT NOT NULL CHECK (create_payload_hash ~ '^[a-f0-9]{64}$'),
    revoke_command_id_hash TEXT CHECK (
        revoke_command_id_hash IS NULL OR revoke_command_id_hash ~ '^[a-f0-9]{64}$'
    ),
    revoke_payload_hash TEXT CHECK (
        revoke_payload_hash IS NULL OR revoke_payload_hash ~ '^[a-f0-9]{64}$'
    ),
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, create_command_id_hash),
    CHECK (owner_subject_id <> contributor_subject_id),
    CHECK (
        (status = 'active'
            AND revoked_at IS NULL
            AND revocation_reason IS NULL
            AND revoke_command_id_hash IS NULL
            AND revoke_payload_hash IS NULL)
        OR (status = 'revoked'
            AND revoked_at IS NOT NULL
            AND NULLIF(BTRIM(revocation_reason), '') IS NOT NULL
            AND revoke_command_id_hash IS NOT NULL
            AND revoke_payload_hash IS NOT NULL)
    ),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (relationship_id)
        REFERENCES public.family_relationships(id)
        ON DELETE CASCADE
);

CREATE UNIQUE INDEX owner_truth_family_contribution_grants_active_scope
    ON owner_truth.family_contribution_grants(
        vault_id,
        relationship_id,
        contributor_subject_id
    )
    WHERE status = 'active';

CREATE INDEX owner_truth_family_contribution_grants_relationship
    ON owner_truth.family_contribution_grants(relationship_id, status, updated_at DESC);

CREATE OR REPLACE FUNCTION owner_truth.validate_family_contribution_grant()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_status TEXT;
    relationship_owner_subject_id TEXT;
    relationship_member_subject_id TEXT;
    relationship_status TEXT;
    current_relationship_epoch BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT owner_subject_id, status
        INTO vault_owner_subject_id, vault_status
        FROM owner_truth.vaults
        WHERE vault_id = NEW.vault_id;

        IF NOT FOUND
           OR vault_status IS DISTINCT FROM 'active'
           OR vault_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id THEN
            RAISE EXCEPTION 'family contribution vault authority is invalid';
        END IF;

        SELECT owner_subject_id, member_subject_id, status, relationship_epoch
        INTO relationship_owner_subject_id, relationship_member_subject_id,
            relationship_status, current_relationship_epoch
        FROM public.family_relationships
        WHERE id = NEW.relationship_id;

        IF NOT FOUND
           OR relationship_status IS DISTINCT FROM 'accepted'
           OR relationship_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
           OR relationship_member_subject_id IS DISTINCT FROM NEW.contributor_subject_id
           OR current_relationship_epoch IS DISTINCT FROM NEW.relationship_epoch THEN
            RAISE EXCEPTION 'family contribution relationship authority is invalid';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.vault_id IS DISTINCT FROM NEW.vault_id
       OR OLD.owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR OLD.contributor_subject_id IS DISTINCT FROM NEW.contributor_subject_id
       OR OLD.relationship_id IS DISTINCT FROM NEW.relationship_id
       OR OLD.relationship_epoch IS DISTINCT FROM NEW.relationship_epoch
       OR OLD.scope IS DISTINCT FROM NEW.scope
       OR OLD.create_command_id_hash IS DISTINCT FROM NEW.create_command_id_hash
       OR OLD.create_payload_hash IS DISTINCT FROM NEW.create_payload_hash THEN
        RAISE EXCEPTION 'family contribution grant identity is immutable';
    END IF;
    IF OLD.status = 'revoked'
       OR NEW.status IS DISTINCT FROM 'revoked'
       OR NEW.row_version IS DISTINCT FROM OLD.row_version + 1 THEN
        RAISE EXCEPTION 'family contribution grant may only be revoked once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_family_contribution_grants_validate
BEFORE INSERT OR UPDATE ON owner_truth.family_contribution_grants
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_family_contribution_grant();
