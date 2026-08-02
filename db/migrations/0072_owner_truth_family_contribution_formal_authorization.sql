-- migration:owner_truth_family_contribution_formal_authorization
--
-- Split the pre-existing QA-only family contribution fixtures from the
-- closed-pilot product lane. A relationship alone is not authorization: a
-- formal grant stores only a value-minimized server release-policy capture.

ALTER TABLE owner_truth.family_contribution_grants
    ADD COLUMN IF NOT EXISTS admission_mode TEXT NOT NULL DEFAULT 'qa'
        CHECK (admission_mode IN ('qa', 'closedPilot')),
    ADD COLUMN IF NOT EXISTS authorization_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(authorization_evidence) = 'object');

ALTER TABLE owner_truth.family_contribution_grants
    ADD CONSTRAINT owner_truth_family_contribution_grants_admission_evidence_check
    CHECK (
        (admission_mode = 'qa' AND authorization_evidence = '{}'::jsonb)
        OR (
            admission_mode = 'closedPilot'
            AND authorization_evidence ->> 'schemaVersion'
                = 'owner-truth-command-authorization-capture-v1'
            AND authorization_evidence ->> 'feature'
                = 'ownerTruthFamilyContribution'
            AND authorization_evidence ? 'policyVersion'
            AND authorization_evidence ? 'policyRevision'
            AND authorization_evidence ? 'emergencyRevision'
            AND authorization_evidence ? 'accountGenerationHash'
            AND authorization_evidence ? 'decisionIdHash'
            AND authorization_evidence ? 'audience'
            AND authorization_evidence ? 'cohort'
            AND authorization_evidence ? 'clientBuild'
            AND authorization_evidence ? 'expiresAt'
        )
    );

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
       OR OLD.create_payload_hash IS DISTINCT FROM NEW.create_payload_hash
       OR OLD.admission_mode IS DISTINCT FROM NEW.admission_mode
       OR OLD.authorization_evidence IS DISTINCT FROM NEW.authorization_evidence THEN
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
