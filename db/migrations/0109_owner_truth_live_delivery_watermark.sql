-- migration:owner_truth_live_delivery_watermark
--
-- Live turns remain private conversation records.  The client sequence is an
-- operational delivery watermark only: it prevents an incomplete transcript
-- from being closed and handed to the candidate-extraction workflow.

ALTER TABLE owner_truth.interview_sessions
    ADD COLUMN continuous_client_sequence BIGINT NOT NULL DEFAULT 0
        CHECK (continuous_client_sequence >= 0),
    ADD COLUMN close_requested_client_sequence BIGINT
        CHECK (
            close_requested_client_sequence IS NULL
            OR close_requested_client_sequence >= 1
        );

ALTER TABLE owner_truth.conversation_messages
    ADD COLUMN client_sequence_number BIGINT
        CHECK (
            client_sequence_number IS NULL
            OR client_sequence_number >= 1
        ),
    ADD COLUMN captured_at TIMESTAMPTZ;

-- A retry uses the same command receipt.  A different command may never
-- claim the same ordered Live turn in the same private interview session.
CREATE UNIQUE INDEX owner_truth_conversation_messages_session_client_sequence_unique
    ON owner_truth.conversation_messages(
        vault_id,
        session_id,
        client_sequence_number
    )
    WHERE client_sequence_number IS NOT NULL;

CREATE INDEX owner_truth_interview_sessions_live_delivery_watermark
    ON owner_truth.interview_sessions(
        vault_id,
        state,
        continuous_client_sequence,
        updated_at DESC
    );
