-- migration:owner_truth_source_dependency_recovery
--
-- 0120 intentionally stopped new, unreviewed Sources from invalidating formal
-- projections. This additive follow-up closes two evidence gaps: a current
-- MemoryVersion can cite more than its primary source through evidenceRefs,
-- and every durable rebuild request needs bounded worker recovery semantics.

ALTER TABLE async_effects.job_attempts
    ADD COLUMN terminal_reason_code TEXT;

ALTER TABLE owner_truth.source_projection_rebuild_requests
    ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    ADD COLUMN available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN lease_owner TEXT,
    ADD COLUMN lease_until TIMESTAMPTZ,
    ADD COLUMN heartbeat_at TIMESTAMPTZ,
    ADD COLUMN last_error_code TEXT,
    ADD COLUMN completed_at TIMESTAMPTZ;

-- Rows created by 0120 predate lease metadata. Preserve completed evidence and
-- make a legacy in-flight row safely reclaimable before adding constraints.
UPDATE owner_truth.source_projection_rebuild_requests
   SET completed_at = updated_at
 WHERE state = 'completed'
   AND completed_at IS NULL;

UPDATE owner_truth.source_projection_rebuild_requests
   SET state = 'pending',
       available_at = NOW(),
       lease_owner = NULL,
       lease_until = NULL,
       heartbeat_at = NULL,
       updated_at = NOW()
 WHERE state = 'processing';

ALTER TABLE owner_truth.source_projection_rebuild_requests
    ADD CONSTRAINT owner_truth_source_projection_rebuild_lease_shape
        CHECK (
            (
                state = 'processing'
                AND lease_owner IS NOT NULL
                AND lease_until IS NOT NULL
                AND heartbeat_at IS NOT NULL
                AND completed_at IS NULL
            )
            OR (
                state <> 'processing'
                AND lease_owner IS NULL
                AND lease_until IS NULL
                AND heartbeat_at IS NULL
            )
        ),
    ADD CONSTRAINT owner_truth_source_projection_rebuild_terminal_shape
        CHECK (
            (state = 'completed' AND completed_at IS NOT NULL)
            OR (state <> 'completed' AND completed_at IS NULL)
        );

CREATE INDEX owner_truth_source_projection_rebuild_claim_idx
    ON owner_truth.source_projection_rebuild_requests(
        state, available_at, lease_until, request_id
    )
    WHERE state IN ('pending', 'processing');

CREATE OR REPLACE FUNCTION owner_truth.source_has_current_formal_dependency(
    p_vault_id TEXT,
    p_source_id UUID,
    p_source_version BIGINT
) RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
          FROM owner_truth.memory_versions AS version
          JOIN owner_truth.memories AS memory
            ON memory.vault_id = version.vault_id
           AND memory.id = version.memory_id
         WHERE version.vault_id = p_vault_id
           AND version.is_current = TRUE
           AND memory.status = 'active'
           AND memory.owner_subject_id IS NOT DISTINCT FROM (
                SELECT vault.owner_subject_id
                  FROM owner_truth.vaults AS vault
                 WHERE vault.vault_id = p_vault_id
           )
           AND (
                (
                    version.source_id = p_source_id
                    AND version.source_version = p_source_version
                )
                OR EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(version.payload -> 'evidenceRefs') = 'array'
                                THEN version.payload -> 'evidenceRefs'
                                ELSE '[]'::JSONB
                            END
                      ) AS evidence(reference)
                     WHERE evidence.reference ->> 'sourceId' = p_source_id::TEXT
                       AND COALESCE(evidence.reference ->> 'sourceVersion', '') ~ '^[0-9]+$'
                       AND (evidence.reference ->> 'sourceVersion')::BIGINT = p_source_version
                )
           )
    );
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION owner_truth.invalidate_source_memory_derivatives_if_referenced()
RETURNS TRIGGER AS $$
DECLARE
    v_vault_id TEXT;
    v_owner_subject_id TEXT;
    v_source_id UUID;
    v_source_version BIGINT;
    v_authority_epoch BIGINT;
    v_memory_revision BIGINT;
    v_rights_revision BIGINT;
    v_old_referenced BOOLEAN := FALSE;
    v_new_referenced BOOLEAN := FALSE;
BEGIN
    -- A Source insert is unreviewed evidence. Formal dependency can only be
    -- introduced by a later, explicitly reviewed MemoryVersion write.
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    v_old_referenced := owner_truth.source_has_current_formal_dependency(
        OLD.vault_id, OLD.id, OLD.source_version
    );
    IF TG_OP = 'UPDATE' THEN
        v_new_referenced := owner_truth.source_has_current_formal_dependency(
            NEW.vault_id, NEW.id, NEW.source_version
        );
    END IF;

    IF NOT v_old_referenced AND NOT v_new_referenced THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    -- Preserve the referenced side of an OLD/NEW version transition in the
    -- recovery key. The active Vault remains the authority coordinate.
    IF v_old_referenced THEN
        v_vault_id := OLD.vault_id;
        v_source_id := OLD.id;
        v_source_version := OLD.source_version;
    ELSE
        v_vault_id := NEW.vault_id;
        v_source_id := NEW.id;
        v_source_version := NEW.source_version;
    END IF;

    SELECT vault.owner_subject_id, vault.authority_epoch
      INTO v_owner_subject_id, v_authority_epoch
      FROM owner_truth.vaults AS vault
     WHERE vault.vault_id = v_vault_id;

    SELECT COALESCE(revision, 0)
      INTO v_memory_revision
      FROM owner_truth.memory_revisions
     WHERE vault_id = v_vault_id;
    SELECT COALESCE(MAX(revision), 0)
      INTO v_rights_revision
      FROM owner_truth.projection_rights_events
     WHERE vault_id = v_vault_id
       AND authority_epoch = v_authority_epoch;

    UPDATE owner_truth.memory_projection_checkpoints
       SET state = 'rebuilding', updated_at = NOW()
     WHERE vault_id = v_vault_id AND state = 'ready';
    UPDATE owner_truth.search_document_checkpoints
       SET state = 'rebuilding', updated_at = NOW()
     WHERE vault_id = v_vault_id AND state = 'ready';

    INSERT INTO owner_truth.source_projection_rebuild_requests (
        vault_id, owner_subject_id, source_id, source_version,
        authority_epoch, memory_revision, rights_revision, reason_code
    ) VALUES (
        v_vault_id, v_owner_subject_id, v_source_id, v_source_version,
        v_authority_epoch, COALESCE(v_memory_revision, 0),
        COALESCE(v_rights_revision, 0), 'formalSourceDependencyChanged'
    ) ON CONFLICT DO NOTHING;

    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS owner_truth.source_has_current_formal_dependency(TEXT, UUID);
