-- migration:owner_truth_interview_review_batches
--
-- M0-A review boundaries are private, value-free workflow records. They do
-- not create Sources, Candidates, DecisionReceipts, MemoryVersions, public
-- routes, provider effects, or any visitor-visible projection.

ALTER TABLE owner_truth.interview_sessions
    ADD COLUMN pending_review_batch_id UUID;

CREATE TABLE owner_truth.interview_review_batches (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    session_id UUID NOT NULL,
    thread_id UUID NOT NULL,
    trigger TEXT NOT NULL
        CHECK (trigger IN ('turnThreshold', 'sessionExit')),
    state TEXT NOT NULL DEFAULT 'pendingAcknowledgement'
        CHECK (state IN ('pendingAcknowledgement', 'acknowledged')),
    captured_candidate_batch_turn_count INTEGER NOT NULL
        CHECK (captured_candidate_batch_turn_count >= 1),
    owner_turn_start_count INTEGER NOT NULL CHECK (owner_turn_start_count >= 1),
    owner_turn_end_count INTEGER NOT NULL
        CHECK (owner_turn_end_count >= owner_turn_start_count),
    through_message_sequence BIGINT NOT NULL CHECK (through_message_sequence >= 1),
    policy_version TEXT NOT NULL CHECK (BTRIM(policy_version) <> ''),
    authority_epoch BIGINT NOT NULL DEFAULT 0 CHECK (authority_epoch >= 0),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    UNIQUE (vault_id, id),
    UNIQUE (vault_id, session_id, thread_id, through_message_sequence),
    CHECK (
        owner_turn_end_count - owner_turn_start_count + 1
        = captured_candidate_batch_turn_count
    ),
    CHECK (
        (state = 'pendingAcknowledgement' AND acknowledged_at IS NULL)
        OR (state = 'acknowledged' AND acknowledged_at IS NOT NULL)
    ),
    FOREIGN KEY (vault_id)
        REFERENCES owner_truth.vaults(vault_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, session_id)
        REFERENCES owner_truth.interview_sessions(vault_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (vault_id, thread_id)
        REFERENCES owner_truth.conversation_threads(vault_id, id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX owner_truth_interview_review_batches_one_pending_per_session
    ON owner_truth.interview_review_batches(vault_id, session_id)
    WHERE state = 'pendingAcknowledgement';

ALTER TABLE owner_truth.interview_sessions
    ADD CONSTRAINT owner_truth_interview_sessions_pending_review_batch_fk
        FOREIGN KEY (vault_id, pending_review_batch_id)
        REFERENCES owner_truth.interview_review_batches(vault_id, id)
        ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION owner_truth.guard_interview_review_batch_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.vault_id IS DISTINCT FROM NEW.vault_id
       OR OLD.owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR OLD.session_id IS DISTINCT FROM NEW.session_id
       OR OLD.thread_id IS DISTINCT FROM NEW.thread_id
       OR OLD.trigger IS DISTINCT FROM NEW.trigger
       OR OLD.captured_candidate_batch_turn_count IS DISTINCT FROM NEW.captured_candidate_batch_turn_count
       OR OLD.owner_turn_start_count IS DISTINCT FROM NEW.owner_turn_start_count
       OR OLD.owner_turn_end_count IS DISTINCT FROM NEW.owner_turn_end_count
       OR OLD.through_message_sequence IS DISTINCT FROM NEW.through_message_sequence
       OR OLD.policy_version IS DISTINCT FROM NEW.policy_version
       OR OLD.authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION 'owner truth review batch identity is immutable';
    END IF;
    IF OLD.state <> 'pendingAcknowledgement'
       OR NEW.state <> 'acknowledged'
       OR NEW.acknowledged_at IS NULL THEN
        RAISE EXCEPTION 'owner truth review batch acknowledgement is terminal';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_interview_review_batches_bind_vault_authority
BEFORE INSERT OR UPDATE ON owner_truth.interview_review_batches
FOR EACH ROW EXECUTE FUNCTION owner_truth.bind_vault_authority();

CREATE TRIGGER owner_truth_interview_review_batches_guard_transition
BEFORE UPDATE ON owner_truth.interview_review_batches
FOR EACH ROW EXECUTE FUNCTION owner_truth.guard_interview_review_batch_transition();

CREATE TRIGGER owner_truth_interview_review_batches_no_delete
BEFORE DELETE ON owner_truth.interview_review_batches
FOR EACH ROW EXECUTE FUNCTION owner_truth.conversation_append_only();

ALTER TABLE owner_truth.conversation_command_receipts
    ADD COLUMN result_review_batch_id UUID,
    ADD COLUMN expected_review_batch_version BIGINT
        CHECK (expected_review_batch_version IS NULL OR expected_review_batch_version >= 1),
    ADD CONSTRAINT owner_truth_conversation_command_receipts_review_batch_fk
        FOREIGN KEY (vault_id, result_review_batch_id)
        REFERENCES owner_truth.interview_review_batches(vault_id, id)
        ON DELETE RESTRICT;

-- These are the explicit names introduced by migration 0030. Fail closed if
-- the prerequisite private conversation schema is not the expected version.
ALTER TABLE owner_truth.conversation_command_receipts
    DROP CONSTRAINT owner_truth_conversation_command_receipts_command_type_check,
    DROP CONSTRAINT owner_truth_conversation_command_receipts_command_shape_check;

ALTER TABLE owner_truth.conversation_command_receipts
    ADD CONSTRAINT owner_truth_conversation_command_receipts_command_type_check
        CHECK (command_type IN (
            'startInterviewSession',
            'appendInterviewMessage',
            'setInterviewBoundary',
            'recordInterviewPacing',
            'createInterviewReviewBatch',
            'acknowledgeInterviewReviewBatch'
        )),
    ADD CONSTRAINT owner_truth_conversation_command_receipts_command_shape_check
        CHECK (
            (command_type = 'startInterviewSession'
                AND result_message_id IS NULL
                AND result_review_batch_id IS NULL
                AND expected_thread_version = 0
                AND expected_session_version IS NULL
                AND expected_review_batch_version IS NULL)
            OR
            (command_type = 'appendInterviewMessage'
                AND result_message_id IS NOT NULL
                AND result_review_batch_id IS NULL
                AND expected_thread_version IS NOT NULL
                AND expected_session_version IS NOT NULL
                AND expected_review_batch_version IS NULL)
            OR
            (command_type = 'setInterviewBoundary'
                AND result_message_id IS NULL
                AND result_review_batch_id IS NULL
                AND expected_thread_version IS NULL
                AND expected_session_version IS NOT NULL
                AND expected_review_batch_version IS NULL)
            OR
            (command_type = 'recordInterviewPacing'
                AND result_message_id IS NULL
                AND result_review_batch_id IS NULL
                AND expected_thread_version IS NULL
                AND expected_session_version IS NOT NULL
                AND expected_review_batch_version IS NULL)
            OR
            (command_type = 'createInterviewReviewBatch'
                AND result_message_id IS NULL
                AND result_review_batch_id IS NOT NULL
                AND expected_thread_version IS NULL
                AND expected_session_version IS NOT NULL
                AND expected_review_batch_version IS NULL)
            OR
            (command_type = 'acknowledgeInterviewReviewBatch'
                AND result_message_id IS NULL
                AND result_review_batch_id IS NOT NULL
                AND expected_thread_version IS NULL
                AND expected_session_version IS NOT NULL
                AND expected_review_batch_version IS NOT NULL)
        );
