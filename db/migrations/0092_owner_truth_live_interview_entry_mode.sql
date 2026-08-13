-- migration:owner_truth_live_interview_entry_mode
--
-- Live voice capture shares the private Owner Truth conversation tables, but
-- remains a distinct session type so it cannot resume an ordinary text
-- interview. This expand migration only widens the existing enum-like check.

ALTER TABLE owner_truth.conversation_threads
    DROP CONSTRAINT conversation_threads_entry_mode_check;

ALTER TABLE owner_truth.conversation_threads
    ADD CONSTRAINT conversation_threads_entry_mode_check
    CHECK (entry_mode IN ('naturalInput', 'recommendation', 'resume', 'live'));
