-- migration:owner_truth_legacy_backfill_admission_plan
--
-- C03 persists an immutable, value-free admission plan over an already
-- collected legacy inventory.  It deliberately does not create a V4 Source,
-- Candidate, DecisionReceipt, MemoryVersion, cutover, or legacy retirement.

CREATE TABLE owner_truth.legacy_migration_backfill_plans (
    id UUID PRIMARY KEY,
    inventory_run_id UUID NOT NULL
        REFERENCES owner_truth.legacy_migration_runs(id) ON DELETE RESTRICT,
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    classifier_version TEXT NOT NULL CHECK (BTRIM(classifier_version) <> ''),
    inventory_hash TEXT NOT NULL CHECK (inventory_hash ~ '^[a-f0-9]{64}$'),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    entry_count BIGINT NOT NULL CHECK (entry_count >= 0),
    action_counts JSONB NOT NULL CHECK (jsonb_typeof(action_counts) = 'object'),
    scope_hash TEXT NOT NULL CHECK (scope_hash ~ '^[a-f0-9]{64}$'),
    plan_hash TEXT NOT NULL CHECK (plan_hash ~ '^[a-f0-9]{64}$'),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'owner-truth-legacy-backfill-admission-plan-v1'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (inventory_run_id, authority_epoch, plan_hash)
);

CREATE TABLE owner_truth.legacy_migration_backfill_plan_entries (
    plan_id UUID NOT NULL
        REFERENCES owner_truth.legacy_migration_backfill_plans(id) ON DELETE RESTRICT,
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
    action TEXT NOT NULL CHECK (action IN (
        'requireIndependentLineageReplay', 'requireOwnerCandidateReview',
        'requireEvidenceReview', 'quarantined', 'excluded'
    )),
    reason_code TEXT NOT NULL,
    target_state TEXT NOT NULL CHECK (target_state = 'notCreated'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (plan_id, domain, legacy_id_hash),
    CHECK (
        (classification = 'proven_confirmed' AND disposition = 'memoryV1Eligible')
        OR (classification = 'needs_review' AND disposition = 'reviewQueue')
        OR (classification = 'observed_candidate' AND disposition = 'candidateOnly')
        OR (classification = 'quarantine' AND disposition = 'quarantine')
        OR (classification = 'do_not_migrate' AND disposition = 'excluded')
    )
);

CREATE OR REPLACE FUNCTION owner_truth.validate_legacy_migration_backfill_plan()
RETURNS TRIGGER AS $$
DECLARE
    inventory_vault_id TEXT;
    inventory_owner_subject_id TEXT;
    inventory_classifier_version TEXT;
    inventory_hash_value TEXT;
    inventory_entry_count BIGINT;
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    SELECT vault_id, owner_subject_id, classifier_version, inventory_hash, entry_count
    INTO inventory_vault_id, inventory_owner_subject_id, inventory_classifier_version,
        inventory_hash_value, inventory_entry_count
    FROM owner_truth.legacy_migration_runs
    WHERE id = NEW.inventory_run_id;

    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND
       OR inventory_vault_id IS DISTINCT FROM NEW.vault_id
       OR inventory_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR inventory_classifier_version IS DISTINCT FROM NEW.classifier_version
       OR inventory_hash_value IS DISTINCT FROM NEW.inventory_hash
       OR inventory_entry_count IS DISTINCT FROM NEW.entry_count
       OR vault_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR vault_authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR vault_status IS DISTINCT FROM 'active'
    THEN
        RAISE EXCEPTION 'owner truth legacy backfill plan is not bound to current immutable inventory and vault authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_backfill_plans_validate_inputs
BEFORE INSERT ON owner_truth.legacy_migration_backfill_plans
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_legacy_migration_backfill_plan();

CREATE OR REPLACE FUNCTION owner_truth.validate_legacy_migration_backfill_plan_entry()
RETURNS TRIGGER AS $$
DECLARE
    inventory_classification TEXT;
    inventory_disposition TEXT;
    inventory_record_hash TEXT;
    expected_action TEXT;
    expected_reason_code TEXT;
BEGIN
    SELECT entry.classification, entry.disposition, entry.record_hash
    INTO inventory_classification, inventory_disposition, inventory_record_hash
    FROM owner_truth.legacy_migration_backfill_plans AS plan
    JOIN owner_truth.legacy_migration_entries AS entry
      ON entry.run_id = plan.inventory_run_id
     AND entry.domain = NEW.domain
     AND entry.legacy_id_hash = NEW.legacy_id_hash
    WHERE plan.id = NEW.plan_id;

    IF NOT FOUND
       OR inventory_classification IS DISTINCT FROM NEW.classification
       OR inventory_disposition IS DISTINCT FROM NEW.disposition
       OR inventory_record_hash IS DISTINCT FROM NEW.record_hash
    THEN
        RAISE EXCEPTION 'owner truth legacy backfill entry does not match immutable inventory';
    END IF;

    expected_action := CASE inventory_disposition
        WHEN 'memoryV1Eligible' THEN 'requireIndependentLineageReplay'
        WHEN 'candidateOnly' THEN 'requireOwnerCandidateReview'
        WHEN 'reviewQueue' THEN 'requireEvidenceReview'
        WHEN 'quarantine' THEN 'quarantined'
        WHEN 'excluded' THEN 'excluded'
        ELSE NULL
    END;
    expected_reason_code := CASE inventory_disposition
        WHEN 'memoryV1Eligible' THEN 'provenLegacyEvidenceRequiresIndependentLineageReplay'
        WHEN 'candidateOnly' THEN 'legacyObservationRequiresOwnerCandidateReview'
        WHEN 'reviewQueue' THEN 'legacyEvidenceRequiresReview'
        WHEN 'quarantine' THEN 'legacyOwnerOrAuthorityConflictQuarantined'
        WHEN 'excluded' THEN 'legacyDomainExcludedFromBackfill'
        ELSE NULL
    END;
    IF expected_action IS NULL
       OR NEW.action IS DISTINCT FROM expected_action
       OR NEW.reason_code IS DISTINCT FROM expected_reason_code
    THEN
        RAISE EXCEPTION 'owner truth legacy backfill entry admission action is invalid';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_backfill_plan_entries_validate_inventory
BEFORE INSERT ON owner_truth.legacy_migration_backfill_plan_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_legacy_migration_backfill_plan_entry();

CREATE OR REPLACE FUNCTION owner_truth.legacy_migration_backfill_plan_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'owner truth legacy backfill plans are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_legacy_migration_backfill_plans_no_update
BEFORE UPDATE ON owner_truth.legacy_migration_backfill_plans
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_backfill_plan_append_only();

CREATE TRIGGER owner_truth_legacy_migration_backfill_plans_no_delete
BEFORE DELETE ON owner_truth.legacy_migration_backfill_plans
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_backfill_plan_append_only();

CREATE TRIGGER owner_truth_legacy_migration_backfill_plan_entries_no_update
BEFORE UPDATE ON owner_truth.legacy_migration_backfill_plan_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_backfill_plan_append_only();

CREATE TRIGGER owner_truth_legacy_migration_backfill_plan_entries_no_delete
BEFORE DELETE ON owner_truth.legacy_migration_backfill_plan_entries
FOR EACH ROW EXECUTE FUNCTION owner_truth.legacy_migration_backfill_plan_append_only();

CREATE INDEX owner_truth_legacy_backfill_plans_vault_epoch
    ON owner_truth.legacy_migration_backfill_plans(vault_id, authority_epoch, created_at DESC);

CREATE INDEX owner_truth_legacy_backfill_plan_entries_action
    ON owner_truth.legacy_migration_backfill_plan_entries(plan_id, action, domain);
