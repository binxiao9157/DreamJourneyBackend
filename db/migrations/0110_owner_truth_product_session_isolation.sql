-- migration:owner_truth_product_session_isolation
--
-- A Live product session is not interchangeable with its provider connection
-- or with the legacy natural-input lane.  Keep the existing metadata value for
-- backwards readers, but materialize a bounded selector so reconnect, delayed
-- delivery and a newly opened Live conversation cannot share a "current"
-- session accidentally.

ALTER TABLE owner_truth.conversation_threads
    ADD COLUMN IF NOT EXISTS product_session_id TEXT
        CHECK (product_session_id IS NULL OR (BTRIM(product_session_id) <> '' AND LENGTH(product_session_id) <= 128));

ALTER TABLE owner_truth.interview_sessions
    ADD COLUMN IF NOT EXISTS product_session_id TEXT
        CHECK (product_session_id IS NULL OR (BTRIM(product_session_id) <> '' AND LENGTH(product_session_id) <= 128));

UPDATE owner_truth.conversation_threads
SET product_session_id = NULLIF(BTRIM(metadata ->> 'productSessionId'), '')
WHERE product_session_id IS NULL
  AND NULLIF(BTRIM(metadata ->> 'productSessionId'), '') IS NOT NULL;

UPDATE owner_truth.interview_sessions
SET product_session_id = NULLIF(BTRIM(metadata ->> 'productSessionId'), '')
WHERE product_session_id IS NULL
  AND NULLIF(BTRIM(metadata ->> 'productSessionId'), '') IS NOT NULL;

-- 0029 enforced one active session for every Vault.  Preserve that rule for
-- legacy/natural input, while allowing different Live product sessions to be
-- recovered independently.  A product session itself remains unique.
DROP INDEX IF EXISTS owner_truth.owner_truth_interview_sessions_one_active_per_vault;

CREATE UNIQUE INDEX IF NOT EXISTS owner_truth_interview_sessions_one_active_natural_per_vault
    ON owner_truth.interview_sessions(vault_id)
    WHERE state = 'active' AND product_session_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS owner_truth_interview_sessions_one_active_per_product_session
    ON owner_truth.interview_sessions(vault_id, product_session_id)
    WHERE state = 'active' AND product_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS owner_truth_interview_sessions_product_resume_lookup
    ON owner_truth.interview_sessions(vault_id, owner_subject_id, authority_epoch, product_session_id, updated_at DESC)
    WHERE state = 'active';

CREATE INDEX IF NOT EXISTS owner_truth_conversation_threads_product_session_lookup
    ON owner_truth.conversation_threads(vault_id, owner_subject_id, authority_epoch, product_session_id)
    WHERE state = 'active';
