-- migration:owner_truth_interview_pacing_state
--
-- Add recoverable, value-free pacing state to the private M0-A interview
-- session. This migration does not enable a route, model generation, Candidate
-- creation, review batch creation, or MemoryVersion promotion.

ALTER TABLE owner_truth.interview_sessions
    ADD COLUMN deepening_turn_count INTEGER NOT NULL DEFAULT 0
        CHECK (deepening_turn_count >= 0),
    ADD COLUMN candidate_batch_turn_count INTEGER NOT NULL DEFAULT 0
        CHECK (candidate_batch_turn_count >= 0),
    ADD COLUMN fatigue TEXT NOT NULL DEFAULT 'normal'
        CHECK (fatigue IN ('normal', 'guarded', 'exhausted'));

-- Existing private sessions have only a cumulative owner turn count. Preserve
-- its meaning as the initial unreviewed-batch count without reading messages.
UPDATE owner_truth.interview_sessions
SET candidate_batch_turn_count = turn_count
WHERE candidate_batch_turn_count = 0;

-- Extend the append-only command receipt allow-list. These are the stable
-- PostgreSQL names created by the column-level and table-level CHECK clauses
-- in migration 0029; the migration fails closed if its prerequisite schema is
-- not present rather than guessing at an unrelated constraint.
ALTER TABLE owner_truth.conversation_command_receipts
    DROP CONSTRAINT conversation_command_receipts_command_type_check,
    DROP CONSTRAINT conversation_command_receipts_check;

ALTER TABLE owner_truth.conversation_command_receipts
    ADD CONSTRAINT owner_truth_conversation_command_receipts_command_type_check
        CHECK (command_type IN (
            'startInterviewSession',
            'appendInterviewMessage',
            'setInterviewBoundary',
            'recordInterviewPacing'
        )),
    ADD CONSTRAINT owner_truth_conversation_command_receipts_command_shape_check
        CHECK (
            (command_type = 'startInterviewSession'
                AND result_message_id IS NULL
                AND expected_thread_version = 0
                AND expected_session_version IS NULL)
            OR
            (command_type = 'appendInterviewMessage'
                AND result_message_id IS NOT NULL
                AND expected_thread_version IS NOT NULL
                AND expected_session_version IS NOT NULL)
            OR
            (command_type = 'setInterviewBoundary'
                AND result_message_id IS NULL
                AND expected_thread_version IS NULL
                AND expected_session_version IS NOT NULL)
            OR
            (command_type = 'recordInterviewPacing'
                AND result_message_id IS NULL
                AND expected_thread_version IS NULL
                AND expected_session_version IS NOT NULL)
        );
