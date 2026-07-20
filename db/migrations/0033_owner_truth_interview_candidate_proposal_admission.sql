-- migration:owner_truth_interview_candidate_proposal_admission
--
-- An Owner may explicitly admit one already acknowledged private interview
-- review batch into the existing Source candidate-extraction lane. This is an
-- append-only provenance record. It never creates a Candidate decision,
-- DecisionReceipt, MemoryVersion, projection, public route, or Provider call.

CREATE TABLE owner_truth.interview_review_batch_candidate_admissions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    review_batch_id UUID NOT NULL,
    source_id UUID NOT NULL,
    source_version BIGINT NOT NULL CHECK (source_version >= 1),
    source_content_hash TEXT NOT NULL CHECK (BTRIM(source_content_hash) <> ''),
    effect_operation_id UUID NOT NULL,
    command_id_hash TEXT NOT NULL CHECK (BTRIM(command_id_hash) <> ''),
    payload_hash TEXT NOT NULL CHECK (BTRIM(payload_hash) <> ''),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    owner_message_count INTEGER NOT NULL CHECK (owner_message_count >= 1),
    first_message_sequence BIGINT NOT NULL CHECK (first_message_sequence >= 1),
    last_message_sequence BIGINT NOT NULL
        CHECK (last_message_sequence >= first_message_sequence),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, review_batch_id),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, review_batch_id)
        REFERENCES owner_truth.interview_review_batches(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, source_id)
        REFERENCES owner_truth.sources(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_review_batch_candidate_admission()
RETURNS TRIGGER AS $$
DECLARE
    batch_owner_subject_id TEXT;
    batch_state TEXT;
    batch_authority_epoch BIGINT;
    source_owner_subject_id TEXT;
    source_kind_value TEXT;
    source_state_value TEXT;
    source_version_value BIGINT;
    source_authority_epoch BIGINT;
    source_metadata JSONB;
BEGIN
    SELECT owner_subject_id, state, authority_epoch
    INTO batch_owner_subject_id, batch_state, batch_authority_epoch
    FROM owner_truth.interview_review_batches
    WHERE vault_id = NEW.vault_id AND id = NEW.review_batch_id;

    IF NOT FOUND
       OR batch_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR batch_state IS DISTINCT FROM 'acknowledged'
       OR batch_authority_epoch IS DISTINCT FROM NEW.authority_epoch THEN
        RAISE EXCEPTION 'review batch candidate admission requires an acknowledged active Owner batch';
    END IF;

    SELECT owner_subject_id, source_kind, state, source_version, authority_epoch, metadata
    INTO source_owner_subject_id, source_kind_value, source_state_value,
        source_version_value, source_authority_epoch, source_metadata
    FROM owner_truth.sources
    WHERE vault_id = NEW.vault_id AND id = NEW.source_id;

    IF NOT FOUND
       OR source_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR source_kind_value IS DISTINCT FROM 'conversation'
       OR source_state_value IS DISTINCT FROM 'active'
       OR source_version_value IS DISTINCT FROM NEW.source_version
       OR source_authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR COALESCE(source_metadata ->> 'origin', '')
            IS DISTINCT FROM 'interviewReviewBatchCandidateProposal'
       OR COALESCE(source_metadata ->> 'reviewBatchId', '')
            IS DISTINCT FROM NEW.review_batch_id::TEXT THEN
        RAISE EXCEPTION 'review batch candidate admission requires its matching active conversation Source';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_interview_review_batch_candidate_admissions_bind_vault_authority
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_admissions
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_admissions_validate
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_admissions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_interview_review_batch_candidate_admission();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_admissions_no_update
BEFORE UPDATE ON owner_truth.interview_review_batch_candidate_admissions
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_admissions_no_delete
BEFORE DELETE ON owner_truth.interview_review_batch_candidate_admissions
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE INDEX owner_truth_interview_review_batch_candidate_admissions_vault_created
    ON owner_truth.interview_review_batch_candidate_admissions(vault_id, created_at DESC);
