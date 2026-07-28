-- migration:owner_truth_migration_parity_shadow
--
-- C05 persists a value-free legacy/V4 comparison report. It is an additive,
-- QA-only ledger: no Source, Candidate, DecisionReceipt, MemoryVersion,
-- async effect, object operation, Provider call, authority mutation, cutover
-- or legacy-writer retirement can be created from these tables.

CREATE TABLE owner_truth.migration_parity_shadow_reports (
    report_hash TEXT PRIMARY KEY CHECK (report_hash ~ '^[a-f0-9]{64}$'),
    vault_id TEXT NOT NULL,
    owner_subject_id TEXT NOT NULL CHECK (BTRIM(owner_subject_id) <> ''),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 0),
    window_reference_hash TEXT NOT NULL CHECK (window_reference_hash ~ '^[a-f0-9]{64}$'),
    scope_hash TEXT NOT NULL CHECK (scope_hash ~ '^[a-f0-9]{64}$'),
    denominator_source_hash TEXT NOT NULL CHECK (denominator_source_hash ~ '^[a-f0-9]{64}$'),
    threshold_source_hash TEXT NOT NULL CHECK (threshold_source_hash ~ '^[a-f0-9]{64}$'),
    expected_sample_count BIGINT NOT NULL CHECK (expected_sample_count > 0),
    observed_sample_count BIGINT NOT NULL CHECK (
        observed_sample_count = expected_sample_count
    ),
    comparison_count BIGINT NOT NULL CHECK (comparison_count >= observed_sample_count),
    duplicate_input_count BIGINT NOT NULL CHECK (duplicate_input_count >= 0),
    match_count BIGINT NOT NULL CHECK (match_count >= 0),
    mismatch_count BIGINT NOT NULL CHECK (mismatch_count >= 0),
    blocking_mismatch_count BIGINT NOT NULL CHECK (blocking_mismatch_count >= 0),
    approved_m08_difference_count BIGINT NOT NULL CHECK (
        approved_m08_difference_count >= 0
    ),
    unresolved_m08_difference_count BIGINT NOT NULL CHECK (
        unresolved_m08_difference_count >= 0
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'owner-truth-migration-parity-shadow-v1'
    ),
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE CHECK (shadow_only = TRUE),
    command_effect_execution_count BIGINT NOT NULL DEFAULT 0 CHECK (
        command_effect_execution_count = 0
    ),
    object_copy_execution_count BIGINT NOT NULL DEFAULT 0 CHECK (
        object_copy_execution_count = 0
    ),
    provider_call_count BIGINT NOT NULL DEFAULT 0 CHECK (provider_call_count = 0),
    provider_cost_charged BOOLEAN NOT NULL DEFAULT FALSE CHECK (
        provider_cost_charged = FALSE
    ),
    write_operation_count BIGINT NOT NULL DEFAULT 0 CHECK (write_operation_count = 0),
    cutover_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (cutover_allowed = FALSE),
    legacy_writer_retired BOOLEAN NOT NULL DEFAULT FALSE CHECK (
        legacy_writer_retired = FALSE
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (comparison_count = match_count + mismatch_count),
    CHECK (
        mismatch_count = blocking_mismatch_count
            + approved_m08_difference_count
            + unresolved_m08_difference_count
    )
);

CREATE TABLE owner_truth.migration_parity_shadow_mismatches (
    report_hash TEXT NOT NULL
        REFERENCES owner_truth.migration_parity_shadow_reports(report_hash) ON DELETE RESTRICT,
    observation_hash TEXT NOT NULL CHECK (observation_hash ~ '^[a-f0-9]{64}$'),
    sample_id_hash TEXT NOT NULL CHECK (sample_id_hash ~ '^[a-f0-9]{64}$'),
    surface TEXT NOT NULL CHECK (surface IN (
        'read', 'command', 'projection', 'context', 'objectCopy'
    )),
    dimension TEXT NOT NULL,
    mismatch_code TEXT NOT NULL CHECK (mismatch_code IN (
        'M01', 'M02', 'M03', 'M04', 'M05', 'M06', 'M07', 'M08'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('blocker', 'high', 'reviewable')),
    legacy_value_hash TEXT CHECK (
        legacy_value_hash IS NULL OR legacy_value_hash ~ '^[a-f0-9]{64}$'
    ),
    v4_value_hash TEXT CHECK (
        v4_value_hash IS NULL OR v4_value_hash ~ '^[a-f0-9]{64}$'
    ),
    allowance_status TEXT NOT NULL CHECK (allowance_status IN (
        'notApplicable', 'approved', 'missing', 'expired'
    )),
    allowance_reason_code TEXT,
    approval_reference_hash TEXT CHECK (
        approval_reference_hash IS NULL OR approval_reference_hash ~ '^[a-f0-9]{64}$'
    ),
    allowance_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (report_hash, observation_hash),
    CHECK (legacy_value_hash IS NOT NULL OR v4_value_hash IS NOT NULL),
    CHECK (
        (mismatch_code = 'M01' AND severity = 'blocker' AND dimension IN (
            'ownerSubjectId', 'vaultId', 'principal', 'recipient'
        ))
        OR (mismatch_code = 'M02' AND severity = 'blocker' AND dimension IN (
            'resourceIdentity', 'legacyLocator', 'deterministicTargetId'
        ))
        OR (mismatch_code = 'M03' AND severity = 'blocker' AND dimension IN (
            'visibility', 'accessGrant', 'deletedState', 'suspendedState', 'claimPendingState'
        ))
        OR (mismatch_code = 'M04' AND severity = 'blocker' AND dimension IN (
            'terminalDecision', 'activeMemoryVersion', 'rowVersion', 'authorityEpoch'
        ))
        OR (mismatch_code = 'M05' AND severity = 'high' AND dimension IN (
            'canonicalContentHash', 'versionOrder', 'stateTransition'
        ))
        OR (mismatch_code = 'M06' AND severity = 'high' AND dimension IN (
            'sourceLineage', 'evidenceLineage', 'citationLineage', 'objectState',
            'objectCopyHash', 'commandEffectPlan', 'providerEffectKey',
            'providerState', 'costEnvelope'
        ))
        OR (mismatch_code = 'M07' AND severity = 'high' AND dimension IN (
            'count', 'sort', 'utcTime', 'pagination', 'cursor', 'projectionCheckpoint'
        ))
        OR (mismatch_code = 'M08' AND severity = 'reviewable' AND dimension IN (
            'displayNormalization', 'nonAuthoritativeSort', 'optionalLegacyMetadata'
        ))
    ),
    CHECK (
        (mismatch_code IN ('M01', 'M02', 'M03', 'M04', 'M05', 'M06', 'M07')
            AND allowance_status = 'notApplicable'
            AND allowance_reason_code IS NULL
            AND approval_reference_hash IS NULL
            AND allowance_expires_at IS NULL)
        OR (mismatch_code = 'M08'
            AND surface NOT IN ('command', 'objectCopy')
            AND (
                (allowance_status = 'missing'
                    AND allowance_reason_code IS NULL
                    AND approval_reference_hash IS NULL
                    AND allowance_expires_at IS NULL)
                OR (allowance_status IN ('approved', 'expired')
                    AND allowance_reason_code IS NOT NULL
                    AND allowance_reason_code ~ '^[A-Za-z][A-Za-z0-9_.:-]{0,127}$'
                    AND approval_reference_hash IS NOT NULL
                    AND allowance_expires_at IS NOT NULL)
            )
        )
    )
);

CREATE INDEX migration_parity_shadow_reports_vault_owner_created_idx
    ON owner_truth.migration_parity_shadow_reports (
        vault_id, owner_subject_id, created_at DESC
    );
CREATE INDEX migration_parity_shadow_mismatches_report_code_idx
    ON owner_truth.migration_parity_shadow_mismatches (report_hash, mismatch_code);

CREATE OR REPLACE FUNCTION owner_truth.validate_migration_parity_shadow_report()
RETURNS TRIGGER AS $$
DECLARE
    vault_owner_subject_id TEXT;
    vault_authority_epoch BIGINT;
    vault_status TEXT;
BEGIN
    SELECT owner_subject_id, authority_epoch, status
    INTO vault_owner_subject_id, vault_authority_epoch, vault_status
    FROM owner_truth.vaults
    WHERE vault_id = NEW.vault_id;

    IF NOT FOUND
       OR vault_owner_subject_id IS DISTINCT FROM NEW.owner_subject_id
       OR vault_authority_epoch IS DISTINCT FROM NEW.authority_epoch
       OR vault_status IS DISTINCT FROM 'active'
    THEN
        RAISE EXCEPTION 'migration parity shadow report is not bound to active vault authority';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_migration_parity_shadow_reports_validate_authority
BEFORE INSERT ON owner_truth.migration_parity_shadow_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_migration_parity_shadow_report();

CREATE OR REPLACE FUNCTION owner_truth.validate_migration_parity_shadow_mismatch_count()
RETURNS TRIGGER AS $$
DECLARE
    expected_mismatch_count BIGINT;
    actual_mismatch_count BIGINT;
    target_report_hash TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_report_hash := OLD.report_hash;
    ELSE
        target_report_hash := NEW.report_hash;
    END IF;

    SELECT mismatch_count
    INTO expected_mismatch_count
    FROM owner_truth.migration_parity_shadow_reports
    WHERE report_hash = target_report_hash;
    SELECT COUNT(*)
    INTO actual_mismatch_count
    FROM owner_truth.migration_parity_shadow_mismatches
    WHERE report_hash = target_report_hash;

    IF NOT FOUND OR expected_mismatch_count IS DISTINCT FROM actual_mismatch_count THEN
        RAISE EXCEPTION 'migration parity shadow mismatch count is incomplete or inconsistent';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER owner_truth_migration_parity_shadow_reports_mismatch_count
AFTER INSERT ON owner_truth.migration_parity_shadow_reports
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_migration_parity_shadow_mismatch_count();

CREATE CONSTRAINT TRIGGER owner_truth_migration_parity_shadow_mismatches_mismatch_count
AFTER INSERT OR UPDATE OR DELETE ON owner_truth.migration_parity_shadow_mismatches
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION owner_truth.validate_migration_parity_shadow_mismatch_count();

CREATE OR REPLACE FUNCTION owner_truth.migration_parity_shadow_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'migration parity shadow evidence is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER owner_truth_migration_parity_shadow_reports_no_update
BEFORE UPDATE OR DELETE ON owner_truth.migration_parity_shadow_reports
FOR EACH ROW EXECUTE FUNCTION owner_truth.migration_parity_shadow_append_only();

CREATE TRIGGER owner_truth_migration_parity_shadow_mismatches_no_update
BEFORE UPDATE OR DELETE ON owner_truth.migration_parity_shadow_mismatches
FOR EACH ROW EXECUTE FUNCTION owner_truth.migration_parity_shadow_append_only();
