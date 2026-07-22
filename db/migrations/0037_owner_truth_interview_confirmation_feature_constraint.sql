-- migration:owner_truth_interview_confirmation_feature_constraint
--
-- Formal interview Candidate confirmation can only carry the release-policy
-- capture for ownerTruthCandidateReview. Empty evidence remains the explicit
-- legacy QA-only row shape. This contract migration does not expose a route
-- or activate a MemoryVersion.

ALTER TABLE owner_truth.interview_review_batch_candidate_decisions
    ADD CONSTRAINT owner_truth_interview_batch_decision_formal_feature
        CHECK (
            authorization_evidence = '{}'::JSONB
            OR COALESCE(
                authorization_evidence->>'feature' = 'ownerTruthCandidateReview',
                FALSE
            )
        ) NOT VALID;

ALTER TABLE owner_truth.interview_review_batch_candidate_decisions
    VALIDATE CONSTRAINT owner_truth_interview_batch_decision_formal_feature;

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_batch_candidate_authorization_evidence()
RETURNS TRIGGER AS $$
BEGIN
    -- Legacy QA-only rows remain explicitly empty. Any populated capture is a
    -- formal release-policy authorization receipt for this confirmation route
    -- only and must be self-describing and value-minimized.
    IF NEW.authorization_evidence = '{}'::JSONB THEN
        RETURN NEW;
    END IF;

    IF jsonb_typeof(NEW.authorization_evidence) IS DISTINCT FROM 'object'
       OR NEW.authorization_evidence->>'schemaVersion'
            IS DISTINCT FROM 'owner-truth-command-authorization-capture-v1'
       OR NEW.authorization_evidence->>'feature'
            IS DISTINCT FROM 'ownerTruthCandidateReview'
       OR COALESCE(NEW.authorization_evidence->>'policyVersion', '') = ''
       OR jsonb_typeof(NEW.authorization_evidence->'policyRevision') IS DISTINCT FROM 'number'
       OR jsonb_typeof(NEW.authorization_evidence->'emergencyRevision') IS DISTINCT FROM 'number'
       OR COALESCE(NEW.authorization_evidence->>'accountGenerationHash', '')
            !~ '^[a-f0-9]{24,64}$'
       OR COALESCE(NEW.authorization_evidence->>'decisionIdHash', '')
            !~ '^[a-f0-9]{64}$'
       OR COALESCE(NEW.authorization_evidence->>'audience', '') = ''
       OR COALESCE(NEW.authorization_evidence->>'cohort', '') = ''
       OR jsonb_typeof(NEW.authorization_evidence->'clientBuild') IS DISTINCT FROM 'number'
       OR COALESCE(NEW.authorization_evidence->>'expiresAt', '') = ''
    THEN
        RAISE EXCEPTION 'interview batch decision authorization evidence is malformed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
