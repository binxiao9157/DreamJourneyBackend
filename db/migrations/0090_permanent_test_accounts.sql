-- migration:permanent_test_accounts
--
-- Test accounts remain active until an administrator explicitly disables
-- them. Existing allowlist rows are promoted to permanent validity.

ALTER TABLE test_account_allowlist
    ALTER COLUMN expires_at DROP NOT NULL;

UPDATE test_account_allowlist
SET expires_at = NULL,
    updated_at = NOW()
WHERE expires_at IS NOT NULL;

DROP INDEX IF EXISTS test_account_allowlist_active_target;

CREATE INDEX test_account_allowlist_active_target
    ON test_account_allowlist (
        identity_type,
        target_hash_key_version,
        target_hash
    )
    WHERE status = 'active';
