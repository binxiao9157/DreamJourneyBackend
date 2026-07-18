-- migration:owner_truth_legacy_migration_inventory
--
-- Read-only legacy migration evidence.  These rows audit an inventory pass;
-- they do not create V4 Sources, Candidates or MemoryVersions, and do not
-- mutate legacy Archive/KBLite/memories tables.

CREATE TABLE owner_truth.legacy_migration_runs (
    id UUID PRIMARY KEY,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    inventory_hash TEXT NOT NULL CHECK (inventory_hash ~ '^[a-f0-9]{64}$'),
    entry_count BIGINT NOT NULL CHECK (entry_count >= 0),
    summary JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vault_id, classifier_version, inventory_hash)
);

CREATE TABLE owner_truth.legacy_migration_entries (
    run_id UUID NOT NULL REFERENCES owner_truth.legacy_migration_runs(id) ON DELETE RESTRICT,
    domain TEXT NOT NULL CHECK (domain IN (
        'archiveItem', 'kbSnapshot', 'kbChange', 'kbReceipt', 'memory', 'conversationCache'
    )),
    legacy_id_hash TEXT NOT NULL CHECK (legacy_id_hash ~ '^[a-f0-9]{64}$'),
    record_hash TEXT NOT NULL CHECK (record_hash ~ '^[a-f0-9]{64}$'),
    classification TEXT NOT NULL CHECK (classification IN (
        'proven_confirmed', 'needs_review', 'observed_candidate', 'quarantine', 'do_not_migrate'
    )),
    disposition TEXT NOT NULL CHECK (disposition IN (
        'memoryV1Eligible', 'candidateOnly', 'reviewQueue', 'quarantine', 'excluded'
    )),
    owner_evidence_state TEXT NOT NULL CHECK (owner_evidence_state IN (
        'verified', 'missing', 'ambiguous', 'notApplicable'
    )),
    source_evidence_state TEXT NOT NULL CHECK (source_evidence_state IN (
        'verified', 'missing', 'ambiguous', 'notApplicable'
    )),
    decision_evidence_state TEXT NOT NULL CHECK (decision_evidence_state IN (
        'verified', 'missing', 'ambiguous', 'notApplicable'
    )),
    reason_code TEXT NOT NULL,
    target_state TEXT NOT NULL CHECK (target_state = 'notCreated'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, domain, legacy_id_hash),
    CHECK (
        (classification = 'proven_confirmed' AND disposition = 'memoryV1Eligible')
        OR (classification = 'needs_review' AND disposition = 'reviewQueue')
        OR (classification = 'observed_candidate' AND disposition = 'candidateOnly')
        OR (classification = 'quarantine' AND disposition = 'quarantine')
        OR (classification = 'do_not_migrate' AND disposition = 'excluded')
    )
);

CREATE INDEX owner_truth_legacy_migration_entries_run_classification
    ON owner_truth.legacy_migration_entries(run_id, classification, domain);

CREATE TABLE owner_truth.legacy_migration_checkpoints (
    vault_id TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    domain TEXT NOT NULL CHECK (domain IN (
        'archiveItem', 'kbSnapshot', 'kbChange', 'kbReceipt', 'memory', 'conversationCache'
    )),
    run_id UUID NOT NULL REFERENCES owner_truth.legacy_migration_runs(id) ON DELETE RESTRICT,
    inventory_hash TEXT NOT NULL CHECK (inventory_hash ~ '^[a-f0-9]{64}$'),
    availability TEXT NOT NULL CHECK (availability IN ('available', 'unavailable')),
    entry_count BIGINT NOT NULL CHECK (entry_count >= 0),
    classification_counts JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vault_id, classifier_version, domain)
);

CREATE OR REPLACE FUNCTION owner_truth.legacy_migration_runs_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth legacy migration runs are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_runs_no_update
BEFORE UPDATE ON owner_truth.legacy_migration_runs
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_runs_append_only();

CREATE TRIGGER owner_truth_legacy_migration_runs_no_delete
BEFORE DELETE ON owner_truth.legacy_migration_runs
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_runs_append_only();

CREATE OR REPLACE FUNCTION owner_truth.legacy_migration_entries_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth legacy migration entries are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_entries_no_update
BEFORE UPDATE ON owner_truth.legacy_migration_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_entries_append_only();

CREATE TRIGGER owner_truth_legacy_migration_entries_no_delete
BEFORE DELETE ON owner_truth.legacy_migration_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_entries_append_only();
