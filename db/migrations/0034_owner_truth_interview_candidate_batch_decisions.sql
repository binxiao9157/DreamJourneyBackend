-- migration:owner_truth_interview_candidate_batch_decisions
--
-- Record the root idempotency boundary for an Owner's partial acceptance of
-- ordinary Candidates from one acknowledged interview review batch. Individual
-- Candidate terminal decisions and DecisionReceipts remain in their existing
-- authority lane; this table stores no Candidate content and never activates a
-- MemoryVersion, projection, public route, or Provider effect.

CREATE TABLE owner_truth.interview_review_batch_candidate_decisions (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    review_batch_id UUID NOT NULL,
    command_id_hash TEXT NOT NULL CHECK (BTRIM(command_id_hash) <> ''),
    payload_hash TEXT NOT NULL CHECK (BTRIM(payload_hash) <> ''),
    selection_count INTEGER NOT NULL CHECK (selection_count BETWEEN 1 AND 50),
    actor_subject_id TEXT NOT NULL CHECK (BTRIM(actor_subject_id) <> ''),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, command_id_hash),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, review_batch_id)
        REFERENCES owner_truth.interview_review_batches(vault_id, id)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.validate_interview_review_batch_candidate_decision()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_status TEXT;
    vault_authority_epoch BIGINT;
    batch_owner_subject_id TEXT;
    batch_state TEXT;
    batch_authority_epoch BIGINT;
    admission_owner_subject_id TEXT;
    admission_authority_epoch BIGINT;
BEGIN
    SELECT owner_subject_id, status, authority_epoch
    INTO vault_owner_subject_id, vault_status, vault_authority_epoch
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    SELECT owner_subject_id, state, authority_epoch
    INTO batch_owner_subject_id, batch_state, batch_authority_epoch
    FROM owner_truth.interview_review_batches
    WHERE vault_id = NEW.vault_id AND id = NEW.review_batch_id;

    SELECT owner_subject_id, authority_epoch
    INTO admission_owner_subject_id, admission_authority_epoch
    FROM owner_truth.interview_review_batch_candidate_admissions
    WHERE vault_id = NEW.vault_id AND review_batch_id = NEW.review_batch_id;

    IF NOT FOUND
       OR vault_status IS DISTINCT FROM 'active'
       OR vault_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR NEW.actor_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR batch_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR admission_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR batch_state IS DISTINCT FROM 'acknowledged'
       OR vault_authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR batch_authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR admission_authority_epoch IS DISTINCT FROM NEW.authority_epoch THEN
        RAISE EXCEPTION 'interview batch Candidate decision requires an active Owner, acknowledged batch and admitted Source';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decisions_bind_vault_authority
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decisions_validate
BEFORE INSERT ON owner_truth.interview_review_batch_candidate_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_interview_review_batch_candidate_decision();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decisions_no_update
BEFORE UPDATE ON owner_truth.interview_review_batch_candidate_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE TRIGGER owner_truth_interview_review_batch_candidate_decisions_no_delete
BEFORE DELETE ON owner_truth.interview_review_batch_candidate_decisions
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

CREATE INDEX owner_truth_interview_review_batch_candidate_decisions_vault_batch_created
    ON owner_truth.interview_review_batch_candidate_decisions(
        vault_id,
        review_batch_id,
        created_at DESC
    );
