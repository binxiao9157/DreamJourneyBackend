-- migration:owner_truth_candidate_decision_authorization_evidence
--
-- Persist the minimum server-issued ownerTruthCandidateReview policy capture
-- on the immutable DecisionReceipt. Empty evidence explicitly marks legacy
-- QA-only receipts; raw bearer/session/decision values never enter this table.

ALTER TABLE owner_truth.decision_receipts
    ADD COLUMN IF NOT EXISTS authorization_evidence JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE owner_truth.decision_receipts
    ADD CONSTRAINT owner_truth_decision_receipts_authorization_evidence_is_object
        CHECK (jsonb_typeof(authorization_evidence) = 'object');

ALTER TABLE owner_truth.decision_receipts
    ADD CONSTRAINT owner_truth_decision_receipts_formal_candidate_review_feature
        CHECK (
            authorization_evidence = '{}'::JSONB
            OR COALESCE(
                authorization_evidence->>'feature' = 'ownerTruthCandidateReview',
                FALSE
            )
        ) NOT VALID;

ALTER TABLE owner_truth.decision_receipts
    VALIDATE CONSTRAINT owner_truth_decision_receipts_formal_candidate_review_feature;

CREATE OR REPLACE FUNCTION owner_truth.validate_decision_receipt_authorization_evidence()
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
        RAISE EXCEPTION 'decision receipt authorization evidence is malformed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_decision_receipts_auth_evidence_validate
BEFORE INSERT ON owner_truth.decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_decision_receipt_authorization_evidence();
