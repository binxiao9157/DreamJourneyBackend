-- migration:owner_truth_legacy_tail_shadow
--
-- C04 persists only a value-free, append-only would-run report over a C03
-- admission plan. It must never create an async effect, outbox/job row,
-- object reference, Provider receipt, callback receipt, Owner Truth target,
-- authority transition, or legacy writer retirement.

CREATE TABLE owner_truth.legacy_migration_tail_shadow_reports (
    plan_id UUID NOT NULL
        REFERENCES owner_truth.legacy_migration_backfill_plans(id) ON DELETE RESTRICT,
    report_hash TEXT NOT NULL CHECK (report_hash ~ '^[a-f0-9]{64}$'),
    plan_hash TEXT NOT NULL CHECK (plan_hash ~ '^[a-f0-9]{64}$'),
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    input_operation_count BIGINT NOT NULL CHECK (input_operation_count >= 0),
    duplicate_input_count BIGINT NOT NULL CHECK (duplicate_input_count >= 0),
    required_outbox_entry_count BIGINT NOT NULL CHECK (required_outbox_entry_count >= 0),
    missing_outbox_mapping_count BIGINT NOT NULL CHECK (
        missing_outbox_mapping_count >= 0
        AND missing_outbox_mapping_count <= required_outbox_entry_count
    ),
    archive_object_evidence_gap_count BIGINT NOT NULL CHECK (
        archive_object_evidence_gap_count >= 0
    ),
    unmapped_provider_catalog_keys JSONB NOT NULL CHECK (
        jsonb_typeof(unmapped_provider_catalog_keys) = 'array'
    ),
    mapping_count BIGINT NOT NULL CHECK (
        mapping_count >= 0
        AND input_operation_count = mapping_count + duplicate_input_count
    ),
    tail_checkpoint_hash TEXT NOT NULL CHECK (tail_checkpoint_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'owner-truth-legacy-tail-shadow-v1'
    ),
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE CHECK (shadow_only = TRUE),
    effect_execution_count BIGINT NOT NULL DEFAULT 0 CHECK (effect_execution_count = 0),
    outbox_write_count BIGINT NOT NULL DEFAULT 0 CHECK (outbox_write_count = 0),
    job_write_count BIGINT NOT NULL DEFAULT 0 CHECK (job_write_count = 0),
    object_storage_operation_count BIGINT NOT NULL DEFAULT 0 CHECK (
        object_storage_operation_count = 0
    ),
    provider_call_count BIGINT NOT NULL DEFAULT 0 CHECK (provider_call_count = 0),
    provider_callback_processed_count BIGINT NOT NULL DEFAULT 0 CHECK (
        provider_callback_processed_count = 0
    ),
    callback_accepted_count BIGINT NOT NULL DEFAULT 0 CHECK (
        callback_accepted_count = 0
    ),
    cutover_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (cutover_allowed = FALSE),
    legacy_writer_retired BOOLEAN NOT NULL DEFAULT FALSE CHECK (
        legacy_writer_retired = FALSE
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (plan_id, report_hash)
);

CREATE TABLE owner_truth.legacy_migration_tail_shadow_mappings (
    plan_id UUID NOT NULL,
    report_hash TEXT NOT NULL CHECK (report_hash ~ '^[a-f0-9]{64}$'),
    mapping_hash TEXT NOT NULL CHECK (mapping_hash ~ '^[a-f0-9]{64}$'),
    channel TEXT NOT NULL CHECK (channel IN (
        'outboxJob', 'objectReference', 'providerEffect'
    )),
    source_domain TEXT NOT NULL CHECK (source_domain IN (
        'archiveItem', 'kbSnapshot', 'kbChange', 'kbReceipt', 'memory', 'conversationCache'
    )),
    source_legacy_id_hash TEXT NOT NULL CHECK (source_legacy_id_hash ~ '^[a-f0-9]{64}$'),
    source_record_hash TEXT NOT NULL CHECK (source_record_hash ~ '^[a-f0-9]{64}$'),
    action TEXT NOT NULL CHECK (action IN (
        'requireIndependentLineageReplay', 'requireOwnerCandidateReview',
        'requireEvidenceReview'
    )),
    operation_stable_key TEXT NOT NULL CHECK (operation_stable_key ~ '^[a-f0-9]{64}$'),
    provider_catalog_key TEXT CHECK (
        provider_catalog_key IS NULL
        OR provider_catalog_key ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
    ),
    provider_query_reconcile_support TEXT CHECK (
        provider_query_reconcile_support IS NULL
        OR provider_query_reconcile_support ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
    ),
    object_reference_hash TEXT CHECK (
        object_reference_hash IS NULL OR object_reference_hash ~ '^[a-f0-9]{64}$'
    ),
    callback_fixture_hash TEXT CHECK (
        callback_fixture_hash IS NULL OR callback_fixture_hash ~ '^[a-f0-9]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (plan_id, report_hash, mapping_hash),
    FOREIGN KEY (plan_id, report_hash)
        REFERENCES owner_truth.legacy_migration_tail_shadow_reports(plan_id, report_hash)
        ON DELETE RESTRICT,
    CHECK (
        (channel = 'outboxJob'
            AND provider_catalog_key IS NULL
            AND provider_query_reconcile_support IS NULL
            AND object_reference_hash IS NULL
            AND callback_fixture_hash IS NULL)
        OR (channel = 'objectReference'
            AND provider_catalog_key IS NULL
            AND provider_query_reconcile_support IS NULL
            AND object_reference_hash IS NOT NULL
            AND callback_fixture_hash IS NULL)
        OR (channel = 'providerEffect'
            AND provider_catalog_key IS NOT NULL
            AND provider_query_reconcile_support IS NOT NULL
            AND object_reference_hash IS NULL
            AND callback_fixture_hash IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_report()
RETURNS TRIGGER AS $$
DECLARE
    plan_vault_id TEXT;
    plan_owner_subject_id TEXT;
    plan_authority_epoch BIGINT;
    plan_hash_value TEXT;
BEGIN
    SELECT vault_id, owner_subject_id, authority_epoch, plan_hash
    INTO plan_vault_id, plan_owner_subject_id, plan_authority_epoch, plan_hash_value
    FROM owner_truth.legacy_migration_backfill_plans
    WHERE id = NEW.plan_id;

    IF NOT FOUND
       OR plan_vault_id IS DISTINCT FROM NEW.vault_id
       OR plan_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR plan_authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR plan_hash_value IS DISTINCT FROM NEW.plan_hash
    THEN
        RAISE EXCEPTION 'legacy tail shadow report is not bound to the immutable C03 plan';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_tail_shadow_reports_validate_plan
BEFORE INSERT ON owner_truth.legacy_migration_tail_shadow_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_report();

CREATE OR REPLACE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_mapping()
RETURNS TRIGGER AS $$
DECLARE
    plan_entry_record_hash TEXT;
    plan_entry_action TEXT;
BEGIN
    SELECT entry.record_hash, entry.action
    INTO plan_entry_record_hash, plan_entry_action
    FROM owner_truth.legacy_migration_backfill_plan_entries AS entry
    WHERE entry.plan_id = NEW.plan_id
      AND entry.domain = NEW.source_domain
      AND entry.legacy_id_hash = NEW.source_legacy_id_hash;

    IF NOT FOUND
       OR plan_entry_record_hash IS DISTINCT FROM NEW.source_record_hash
       OR plan_entry_action IS DISTINCT FROM NEW.action
       OR NEW.action NOT IN (
           'requireIndependentLineageReplay', 'requireOwnerCandidateReview',
           'requireEvidenceReview'
       )
    THEN
        RAISE EXCEPTION 'legacy tail shadow mapping does not match an eligible C03 plan entry';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_tail_shadow_mappings_validate_plan
BEFORE INSERT ON owner_truth.legacy_migration_tail_shadow_mappings
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_mapping();

CREATE OR REPLACE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_mapping_count()
RETURNS TRIGGER AS $$
DECLARE
    expected_mapping_count BIGINT;
    actual_mapping_count BIGINT;
    target_plan_id UUID;
    target_report_hash TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_plan_id := OLD.plan_id;
        target_report_hash := OLD.report_hash;
    ELSE
        target_plan_id := NEW.plan_id;
        target_report_hash := NEW.report_hash;
    END IF;
    SELECT mapping_count
    INTO expected_mapping_count
    FROM owner_truth.legacy_migration_tail_shadow_reports
    WHERE plan_id = target_plan_id
      AND report_hash = target_report_hash;
    SELECT COUNT(*)
    INTO actual_mapping_count
    FROM owner_truth.legacy_migration_tail_shadow_mappings
    WHERE plan_id = target_plan_id
      AND report_hash = target_report_hash;

    IF NOT FOUND OR expected_mapping_count IS DISTINCT FROM actual_mapping_count THEN
        RAISE EXCEPTION 'legacy tail shadow report mapping count is incomplete or inconsistent';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER owner_truth_legacy_migration_tail_shadow_reports_mapping_count
AFTER INSERT OR UPDATE ON owner_truth.legacy_migration_tail_shadow_reports
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_mapping_count();

CREATE CONSTRAINT TRIGGER owner_truth_legacy_migration_tail_shadow_mappings_mapping_count
AFTER INSERT OR UPDATE OR DELETE ON owner_truth.legacy_migration_tail_shadow_mappings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_legacy_migration_tail_shadow_mapping_count();

CREATE OR REPLACE FUNCTION owner_truth.legacy_migration_tail_shadow_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'legacy tail shadow reports are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_tail_shadow_reports_no_update
BEFORE UPDATE ON owner_truth.legacy_migration_tail_shadow_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_tail_shadow_append_only();

CREATE TRIGGER owner_truth_legacy_migration_tail_shadow_reports_no_delete
BEFORE DELETE ON owner_truth.legacy_migration_tail_shadow_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_tail_shadow_append_only();

CREATE TRIGGER owner_truth_legacy_migration_tail_shadow_mappings_no_update
BEFORE UPDATE ON owner_truth.legacy_migration_tail_shadow_mappings
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_tail_shadow_append_only();

CREATE TRIGGER owner_truth_legacy_migration_tail_shadow_mappings_no_delete
BEFORE DELETE ON owner_truth.legacy_migration_tail_shadow_mappings
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_tail_shadow_append_only();

CREATE INDEX owner_truth_legacy_tail_shadow_reports_vault_epoch
    ON owner_truth.legacy_migration_tail_shadow_reports(
        vault_id, authority_epoch, created_at DESC
    );

CREATE INDEX owner_truth_legacy_tail_shadow_mappings_channel
    ON owner_truth.legacy_migration_tail_shadow_mappings(plan_id, report_hash, channel);
