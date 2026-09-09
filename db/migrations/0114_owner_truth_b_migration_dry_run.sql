-- migration:owner_truth_b_migration_dry_run
--
-- Append-only, value-free evidence for B migration rehearsal.  This schema has
-- no foreign key to current formal memories because a dry-run must never
-- create one.  The only mutable system action after a report is an explicit,
-- separately approved replay through the current Source/Candidate/Review path.

CREATE TABLE owner_truth.b_migration_dry_run_reports (
    id UUID PRIMARY KEY,
    inventory_run_id UUID NOT NULL
        REFERENCES owner_truth.legacy_migration_runs(id) ON DELETE RESTRICT,
    plan_id UUID NOT NULL
        REFERENCES owner_truth.legacy_migration_backfill_plans(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    inventory_hash TEXT NOT NULL CHECK (inventory_hash ~ '^[a-f0-9]{64}$'),
    plan_hash TEXT NOT NULL CHECK (plan_hash ~ '^[a-f0-9]{64}$'),
    scope_hash TEXT NOT NULL CHECK (scope_hash ~ '^[a-f0-9]{64}$'),
    report_hash TEXT NOT NULL CHECK (report_hash ~ '^[a-f0-9]{64}$'),
    entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
    disposition_counts JSONB NOT NULL,
    owner_review_required_count INTEGER NOT NULL CHECK (owner_review_required_count >= 0),
    formal_memory_write_count INTEGER NOT NULL DEFAULT 0
        CHECK (formal_memory_write_count = 0),
    target_state TEXT NOT NULL DEFAULT 'notCreated'
        CHECK (target_state = 'notCreated'),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-b-migration-dry-run-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (inventory_run_id, plan_id, authority_epoch, report_hash)
);

CREATE TABLE owner_truth.b_migration_dry_run_entries (
    report_id UUID NOT NULL
        REFERENCES owner_truth.b_migration_dry_run_reports(id) ON DELETE RESTRICT,
    domain TEXT NOT NULL CHECK (BTRIM(domain) <> ''),
    legacy_id_hash TEXT NOT NULL CHECK (legacy_id_hash ~ '^[a-f0-9]{64}$'),
    record_hash TEXT NOT NULL CHECK (record_hash ~ '^[a-f0-9]{64}$'),
    admission_action TEXT NOT NULL CHECK (BTRIM(admission_action) <> ''),
    disposition TEXT NOT NULL CHECK (BTRIM(disposition) <> ''),
    requires_owner_review BOOLEAN NOT NULL,
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    semantic_field_policy JSONB NOT NULL,
    target_state TEXT NOT NULL DEFAULT 'notCreated'
        CHECK (target_state = 'notCreated'),
    PRIMARY KEY (report_id, domain, legacy_id_hash)
);

CREATE OR REPLACE FUNCTION owner_truth.b_migration_dry_run_reports_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth B migration dry-run reports are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_b_migration_dry_run_reports_no_update
BEFORE UPDATE ON owner_truth.b_migration_dry_run_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.b_migration_dry_run_reports_append_only();

CREATE TRIGGER owner_truth_b_migration_dry_run_reports_no_delete
BEFORE DELETE ON owner_truth.b_migration_dry_run_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.b_migration_dry_run_reports_append_only();

CREATE TRIGGER owner_truth_b_migration_dry_run_entries_no_update
BEFORE UPDATE ON owner_truth.b_migration_dry_run_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.b_migration_dry_run_reports_append_only();

CREATE TRIGGER owner_truth_b_migration_dry_run_entries_no_delete
BEFORE DELETE ON owner_truth.b_migration_dry_run_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.b_migration_dry_run_reports_append_only();

CREATE INDEX owner_truth_b_migration_dry_run_reports_scope
    ON owner_truth.b_migration_dry_run_reports(
        vault_id, authority_epoch, created_at DESC
    );
