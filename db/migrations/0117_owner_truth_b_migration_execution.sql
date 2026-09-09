-- migration:owner_truth_b_migration_execution
--
-- Resumable checkpoints for replaying immutable legacy text through the
-- current Source -> Candidate -> Owner review lane.  No table in this
-- migration can create or update a formal MemoryVersion.

CREATE TABLE owner_truth.b_migration_execution_runs (
    id UUID PRIMARY KEY,
    report_id UUID NOT NULL UNIQUE
        REFERENCES owner_truth.b_migration_dry_run_reports(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL CHECK (BTRIM(vault_id) <> ''),
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    report_hash TEXT NOT NULL CHECK (report_hash ~ '^[a-f0-9]{64}$'),
    entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
    processed_count INTEGER NOT NULL DEFAULT 0
        CHECK (processed_count >= 0 AND processed_count <= entry_count),
    checkpoint_ordinal INTEGER NOT NULL DEFAULT 0
        CHECK (checkpoint_ordinal >= 0 AND checkpoint_ordinal <= entry_count),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'running', 'completed', 'blocked')),
    schema_version TEXT NOT NULL
        CHECK (schema_version = 'owner-truth-b-migration-execution-v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CHECK (
        (state IN ('pending', 'running') AND completed_at IS NULL)
        OR (state IN ('completed', 'blocked') AND completed_at IS NOT NULL)
    )
);

CREATE TABLE owner_truth.b_migration_execution_entries (
    run_id UUID NOT NULL
        REFERENCES owner_truth.b_migration_execution_runs(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    domain TEXT NOT NULL CHECK (domain IN (
        'archiveItem', 'kbSnapshot', 'kbChange', 'kbReceipt', 'memory', 'conversationCache'
    )),
    legacy_id_hash TEXT NOT NULL CHECK (legacy_id_hash ~ '^[a-f0-9]{64}$'),
    record_hash TEXT NOT NULL CHECK (record_hash ~ '^[a-f0-9]{64}$'),
    dry_run_disposition TEXT NOT NULL CHECK (dry_run_disposition IN (
        'replayCurrentReviewPath', 'ownerReviewRequired', 'evidenceReviewRequired',
        'quarantined', 'excluded'
    )),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
        'pending', 'processing', 'sourceQueuedForReview', 'manualEvidenceReview',
        'quarantined', 'excluded', 'blockedRecordChanged', 'failedRetryable'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    reason_code TEXT,
    source_id UUID REFERENCES owner_truth.sources(id) ON DELETE RESTRICT,
    source_receipt_id UUID
        REFERENCES owner_truth.source_command_receipts(id) ON DELETE RESTRICT,
    effect_operation_id UUID
        REFERENCES async_effects.operations(operation_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, ordinal),
    UNIQUE (run_id, domain, legacy_id_hash),
    CHECK (
        (state = 'sourceQueuedForReview'
            AND source_id IS NOT NULL
            AND source_receipt_id IS NOT NULL
            AND effect_operation_id IS NOT NULL)
        OR (state <> 'sourceQueuedForReview'
            AND source_id IS NULL
            AND source_receipt_id IS NULL
            AND effect_operation_id IS NULL)
    )
);

CREATE TABLE owner_truth.b_migration_execution_receipts (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    ordinal INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
    disposition TEXT NOT NULL CHECK (disposition IN (
        'sourceQueuedForReview', 'manualEvidenceReview', 'quarantined',
        'excluded', 'blockedRecordChanged', 'failedRetryable'
    )),
    reason_code TEXT NOT NULL CHECK (BTRIM(reason_code) <> ''),
    receipt_hash TEXT NOT NULL CHECK (receipt_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, ordinal, attempt_count),
    FOREIGN KEY (run_id, ordinal)
        REFERENCES owner_truth.b_migration_execution_entries(run_id, ordinal)
        ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION owner_truth.guard_b_migration_execution_run()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.report_id IS DISTINCT FROM NEW.report_id
       OR OLD.vault_id IS DISTINCT FROM NEW.vault_id
       OR OLD.owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR OLD.authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR OLD.report_hash IS DISTINCT FROM NEW.report_hash
       OR OLD.entry_count IS DISTINCT FROM NEW.entry_count
       OR OLD.schema_version IS DISTINCT FROM NEW.schema_version THEN
        RAISE EXCEPTION 'owner truth B migration execution identity is immutable';
    END IF;
    IF OLD.state IN ('completed', 'blocked') AND OLD IS DISTINCT FROM NEW THEN
        RAISE EXCEPTION 'terminal owner truth B migration execution is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_b_migration_execution_runs_guard
BEFORE UPDATE ON owner_truth.b_migration_execution_runs
FOR EACH ROW EXECUTE FUNCTION owner_truth.guard_b_migration_execution_run();

CREATE OR REPLACE FUNCTION owner_truth.guard_b_migration_execution_entry()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.run_id IS DISTINCT FROM NEW.run_id
       OR OLD.ordinal IS DISTINCT FROM NEW.ordinal
       OR OLD.domain IS DISTINCT FROM NEW.domain
       OR OLD.legacy_id_hash IS DISTINCT FROM NEW.legacy_id_hash
       OR OLD.record_hash IS DISTINCT FROM NEW.record_hash
       OR OLD.dry_run_disposition IS DISTINCT FROM NEW.dry_run_disposition THEN
        RAISE EXCEPTION 'owner truth B migration execution entry identity is immutable';
    END IF;
    IF OLD.state IN (
        'sourceQueuedForReview', 'manualEvidenceReview', 'quarantined',
        'excluded', 'blockedRecordChanged'
    ) AND OLD IS DISTINCT FROM NEW THEN
        RAISE EXCEPTION 'terminal owner truth B migration execution entry is immutable';
    END IF;
    IF NOT (
        (OLD.state IN ('pending', 'failedRetryable') AND NEW.state = 'processing')
        OR (OLD.state = 'processing' AND NEW.state IN (
            'sourceQueuedForReview', 'manualEvidenceReview', 'quarantined',
            'excluded', 'blockedRecordChanged', 'failedRetryable'
        ))
    ) THEN
        RAISE EXCEPTION 'invalid owner truth B migration execution transition';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_b_migration_execution_entries_guard
BEFORE UPDATE ON owner_truth.b_migration_execution_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.guard_b_migration_execution_entry();

CREATE OR REPLACE FUNCTION owner_truth.b_migration_execution_receipts_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth B migration execution receipts are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_b_migration_execution_receipts_no_update
BEFORE UPDATE ON owner_truth.b_migration_execution_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.b_migration_execution_receipts_append_only();

CREATE TRIGGER owner_truth_b_migration_execution_receipts_no_delete
BEFORE DELETE ON owner_truth.b_migration_execution_receipts
FOR EACH ROW EXECUTE FUNCTION owner_truth.b_migration_execution_receipts_append_only();

CREATE INDEX owner_truth_b_migration_execution_claim
    ON owner_truth.b_migration_execution_entries(run_id, state, ordinal);
