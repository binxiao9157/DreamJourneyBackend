-- Freeze the exact policy used when a new Live organization run is created.
-- Existing runs remain nullable: their historical allowance cannot be inferred
-- from the policy version string alone.
ALTER TABLE owner_truth.live_memory_runs
    ADD COLUMN IF NOT EXISTS budget_policy_snapshot JSONB,
    ADD COLUMN IF NOT EXISTS budget_policy_hash CHAR(64);

ALTER TABLE owner_truth.live_memory_runs
    ADD CONSTRAINT live_memory_runs_budget_snapshot_pair_check
    CHECK (
        (budget_policy_snapshot IS NULL AND budget_policy_hash IS NULL)
        OR (budget_policy_snapshot IS NOT NULL AND budget_policy_hash IS NOT NULL)
    );
