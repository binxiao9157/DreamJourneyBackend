-- migration:owner_truth_interview_confirmation_authority_receipts
--
-- Add value-minimized release-policy evidence and an immutable direct link
-- between an interview review root command and the DecisionReceipts it wrote.
-- This does not expose a route, activate a MemoryVersion, or store a raw
-- bearer token, session ID, or client decision ID.

ALTER TABLE owner_truth.interview_review_batch_candidate_decisions
    ADD COLUMN authorization_evidence JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE owner_truth.interview_review_batch_candidate_decisions
    ADD CONSTRAINT owner_truth_interview_batch_decision_authorization_evidence_is_object
        CHECK (jsonb_typeof(authorization_evidence) = 'object');

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_review_batch_candidate_authorization_evidence()
RETURNS TRIGGER AS $$
BEGIN
    -- Legacy QA-only rows remain explicitly empty. Any populated capture is a
    -- formal release-policy authorization receipt and must be self-describing
    -- and value-minimized. The Owner Truth schema policy is deliberately
    -- distinct from the release policy recorded in this JSON object.
    IF NEW.authorization_evidence = '{}'::JSONB THEN
        RETURN NEW;
    END IF;

    IF jsonb_typeof(NEW.authorization_evidence) IS DISTINCT FROM 'object'
       OR NEW.authorization_evidence->>'schemaVersion'
            IS DISTINCT FROM 'owner-truth-command-authorization-capture-v1'
       OR COALESCE(NEW.authorization_evidence->>'feature', '') = ''
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

CREATE TRIGGER owner_truth_batch_decision_auth_evidence_validate
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_interview_review_batch_candidate_authorization_evidence();

CREATE TABLE owner_truth.interview_review_batch_candidate_decision_receipts (
    vault_id TEXT NOT NULL,
    batch_decision_id UUID NOT NULL,
    decision_receipt_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    candidate_command_id_hash TEXT NOT NULL
        CHECK (candidate_command_id_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, batch_decision_id, decision_receipt_id),
    UNIQUE (vault_id, decision_receipt_id),
    FOREIGN KEY (vault_id, batch_decision_id)
        REFERENCES owner_truth.interview_review_batch_candidate_decisions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, decision_receipt_id)
        REFERENCES owner_truth.decision_receipts(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, candidate_id)
        REFERENCES owner_truth.memory_candidates(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_review_batch_candidate_decision_receipt()
RETURNS TRIGGER AS $$
DECLARE
    root_owner_subject_id TEXT;
    root_authority_epoch BIGINT;
    root_review_batch_id UUID;
    admission_source_id UUID;
    admission_source_version BIGINT;
    receipt_candidate_id UUID;
    receipt_actor_subject_id TEXT;
    receipt_authority_epoch BIGINT;
    receipt_command_id_hash TEXT;
    candidate_owner_subject_id TEXT;
    candidate_source_id UUID;
    candidate_source_version BIGINT;
BEGIN
    SELECT owner_subject_id, authority_epoch, review_batch_id
    INTO root_owner_subject_id, root_authority_epoch, root_review_batch_id
    FROM owner_truth.interview_review_batch_candidate_decisions
    WHERE vault_id = NEW.vault_id AND id = NEW.batch_decision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'interview batch decision receipt link requires a root command';
    END IF;

    SELECT source_id, source_version
    INTO admission_source_id, admission_source_version
    FROM owner_truth.interview_review_batch_candidate_admissions
    WHERE vault_id = NEW.vault_id
      AND review_batch_id = root_review_batch_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'interview batch decision receipt link requires an admitted Source';
    END IF;

    SELECT receipt.candidate_id, receipt.actor_subject_id, receipt.authority_epoch,
           receipt.command_id_hash, candidate.owner_subject_id, candidate.source_id,
           extraction.source_version
    INTO receipt_candidate_id, receipt_actor_subject_id, receipt_authority_epoch,
         receipt_command_id_hash, candidate_owner_subject_id, candidate_source_id,
         candidate_source_version
    FROM owner_truth.decision_receipts AS receipt
    JOIN owner_truth.memory_candidates AS candidate
      ON candidate.vault_id = receipt.vault_id
     AND candidate.id = receipt.candidate_id
    JOIN owner_truth.extraction_results AS extraction
      ON extraction.vault_id = candidate.vault_id
     AND extraction.id = candidate.extraction_result_id
    WHERE receipt.vault_id = NEW.vault_id
      AND receipt.id = NEW.decision_receipt_id;
    IF NOT FOUND
       OR receipt_candidate_id IS DISTINCT FROM NEW.candidate_id
       OR receipt_actor_subject_id IS DISTINCT FROM root_owner_subject_id
       OR receipt_authority_epoch IS DISTINCT FROM root_authority_epoch
       OR receipt_command_id_hash IS DISTINCT FROM NEW.candidate_command_id_hash
       OR candidate_owner_subject_id IS DISTINCT FROM root_owner_subject_id
       OR candidate_source_id IS DISTINCT FROM admission_source_id
       OR candidate_source_version IS DISTINCT FROM admission_source_version
    THEN
        RAISE EXCEPTION 'interview batch decision receipt link does not match root authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decision_receipts_validate
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_interview_review_batch_candidate_decision_receipt();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decision_receipts_no_update
BEFORE UPDATE ON owner_truth.interview_review_batch_candidate_decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decision_receipts_no_delete
BEFORE DELETE ON owner_truth.interview_review_batch_candidate_decision_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE INDEX owner_truth_interview_batch_decision_receipts_lookup
    ON owner_truth.interview_review_batch_candidate_decision_receipts(
        vault_id,
        batch_decision_id,
        created_at ASC
    );
