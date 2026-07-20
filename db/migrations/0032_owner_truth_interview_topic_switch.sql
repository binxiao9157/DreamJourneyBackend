-- migration:owner_truth_interview_topic_switch
--
-- A topic switch is a private lifecycle fence. It may pause the current
-- ConversationThread and InterviewSession, but it never stores the topic
-- content, starts a replacement session, creates authority records, or emits
-- provider/public effects.

ALTER TABLE owner_truth.conversation_command_receipts
    DROP CONSTRAINT owner_truth_conversation_command_receipts_command_type_check,
    DROP CONSTRAINT owner_truth_conversation_command_receipts_command_shape_check;

ALTER TABLE owner_truth.conversation_command_receipts
    ADD CONSTRAINT owner_truth_conversation_command_receipts_command_type_check
        CHECK (command_type IN (
            'startInterviewSession',
            'appendInterviewMessage',
            'setInterviewBoundary',
            'pauseInterviewForTopicSwitch',
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
            (command_type = 'pauseInterviewForTopicSwitch'
                AND result_message_id IS NULL
                AND result_review_batch_id IS NULL
                AND expected_thread_version IS NOT NULL
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
