-- migration:owner_truth_interview_candidate_proposal_authorization_evidence
--
-- Keep the formal ownerTruthCandidateReview capture on the immutable
-- Candidate-proposal admission ledger only. It is deliberately not copied to
-- Source metadata or the default-off extraction effect, because neither is a
-- durable authorization root for a future public/shareable representation.
-- Empty evidence remains the explicit legacy QA-only row shape.

ALTER TABLE owner_truth.interview_review_batch_candidate_admissions
    ADD COLUMN authorization_evidence JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE owner_truth.interview_review_batch_candidate_admissions
    ADD CONSTRAINT owner_truth_interview_candidate_proposal_authorization_evidence_is_object
        CHECK (jsonb_typeof(authorization_evidence) = 'object');

ALTER TABLE owner_truth.interview_review_batch_candidate_admissions
    ADD CONSTRAINT owner_truth_interview_candidate_proposal_formal_feature
        CHECK (
            authorization_evidence = '{}'::JSONB
            OR COALESCE(
                authorization_evidence->>'feature' = 'ownerTruthCandidateReview',
                FALSE
            )
        ) NOT VALID;

ALTER TABLE owner_truth.interview_review_batch_candidate_admissions
    VALIDATE CONSTRAINT owner_truth_interview_candidate_proposal_formal_feature;

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_candidate_proposal_authorization_evidence()
RETURNS TRIGGER AS $$
BEGIN
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
        RAISE EXCEPTION 'interview candidate proposal authorization evidence is malformed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_interview_candidate_proposal_auth_evidence_validate
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_admissions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_interview_candidate_proposal_authorization_evidence();
