-- migration:test_account_authorization
-- Additive, revisioned product entitlements for synthetic test accounts.
-- Login allowlisting remains independent and grants no product capability.

ALTER TABLE test_account_allowlist
    ADD COLUMN test_role TEXT,
    ADD COLUMN feature_entitlements JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN scenario_bindings JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN entitlement_revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN entitlement_snapshot_id TEXT,
    ADD COLUMN updated_by_hash TEXT,
    ADD COLUMN entitlement_updated_at TIMESTAMPTZ;

UPDATE test_account_allowlist
SET entitlement_snapshot_id = 'tae_' || SUBSTRING(MD5(id || ':1') FROM 1 FOR 32),
    updated_by_hash = created_by_hash,
    entitlement_updated_at = updated_at,
    contract_version = 2
WHERE entitlement_snapshot_id IS NULL;

ALTER TABLE test_account_allowlist
    ALTER COLUMN entitlement_snapshot_id SET NOT NULL,
    ALTER COLUMN updated_by_hash SET NOT NULL,
    ALTER COLUMN entitlement_updated_at SET NOT NULL,
    ALTER COLUMN contract_version SET DEFAULT 2,
    DROP CONSTRAINT IF EXISTS test_account_allowlist_contract_version_check,
    ADD CONSTRAINT test_account_allowlist_test_role_check
        CHECK (
            test_role IS NULL OR test_role IN (
                'superTest', 'ownerTest', 'familyTest', 'operatorTest'
            )
        ),
    ADD CONSTRAINT test_account_allowlist_feature_entitlements_check
        CHECK (jsonb_typeof(feature_entitlements) = 'array'),
    ADD CONSTRAINT test_account_allowlist_scenario_bindings_check
        CHECK (jsonb_typeof(scenario_bindings) = 'object'),
    ADD CONSTRAINT test_account_allowlist_entitlement_revision_check
        CHECK (entitlement_revision >= 1),
    ADD CONSTRAINT test_account_allowlist_entitlement_snapshot_id_check
        CHECK (entitlement_snapshot_id ~ '^tae_[a-f0-9]{32}$'),
    ADD CONSTRAINT test_account_allowlist_updated_by_hash_check
        CHECK (updated_by_hash ~ '^[a-f0-9]{64}$'),
    ADD CONSTRAINT test_account_allowlist_contract_version_check
        CHECK (contract_version IN (1, 2));

CREATE INDEX test_account_allowlist_active_role
    ON test_account_allowlist(test_role, entitlement_revision)
    WHERE status = 'active' AND test_role IS NOT NULL;

REVOKE ALL ON TABLE test_account_allowlist FROM PUBLIC;
