-- migration:owner_truth_family_contribution_authenticated_owner
--
-- Widen the formal family-contribution admission contract from the original
-- closed-pilot cohort to authenticated Owners. Existing QA and closed-pilot
-- grants remain valid; no grant, Source, Candidate or Memory value is changed.

ALTER TABLE owner_truth.family_contribution_grants
    DROP CONSTRAINT IF EXISTS family_contribution_grants_admission_mode_check;

ALTER TABLE owner_truth.family_contribution_grants
    ADD CONSTRAINT family_contribution_grants_admission_mode_check
    CHECK (admission_mode IN ('qa', 'closedPilot', 'authenticatedOwner'))
    NOT VALID;

ALTER TABLE owner_truth.family_contribution_grants
    VALIDATE CONSTRAINT family_contribution_grants_admission_mode_check;

ALTER TABLE owner_truth.family_contribution_grants
    DROP CONSTRAINT IF EXISTS owner_truth_family_contribution_grants_admission_evidence_check;

ALTER TABLE owner_truth.family_contribution_grants
    ADD CONSTRAINT owner_truth_family_contribution_grants_admission_evidence_check
    CHECK (
        (admission_mode = 'qa' AND authorization_evidence = '{}'::jsonb)
        OR (
            admission_mode IN ('closedPilot', 'authenticatedOwner')
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
    ) NOT VALID;

ALTER TABLE owner_truth.family_contribution_grants
    VALIDATE CONSTRAINT owner_truth_family_contribution_grants_admission_evidence_check;
